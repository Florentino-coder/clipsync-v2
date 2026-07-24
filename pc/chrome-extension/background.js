/**
 * ClipSync extension background (MV3 service worker).
 * Resilient localhost WS client + alarm keepalive (Task 4.2).
 */

importScripts('bundled_profiles.js');

const DEFAULT_WS_URL = 'ws://127.0.0.1:8765';
const KEEPALIVE_ALARM = 'ws-keepalive';
const RECONNECT_MS = 3000;

const STORAGE_KEYS = {
  pairingToken: 'pairingToken',
  connectionStatus: 'connectionStatus',
  siteProfiles: 'siteProfiles',
  wsUrl: 'wsUrl',
};

/** @type {WebSocket|null} */
let socket = null;
/** @type {ReturnType<typeof setTimeout>|null} */
let reconnectTimer = null;

function setStatus(status) {
  chrome.storage.local.set({ [STORAGE_KEYS.connectionStatus]: status });
}

function storeSiteProfiles(profiles) {
  // Always fold the bundled profiles in so a PC Push of a stale profile (e.g. an old
  // EXE that ships a 13-step workflow) can never overwrite the bundled 16-step
  // close_job_workflow. Bundled selectors/workflow win; only admin dry_run is kept.
  const merged = mergeBundledProfiles(Array.isArray(profiles) ? profiles : []);
  return chrome.storage.local.set({ [STORAGE_KEYS.siteProfiles]: merged });
}

function clearReconnectTimer() {
  if (reconnectTimer != null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function scheduleReconnect() {
  clearReconnectTimer();
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, RECONNECT_MS);
}

/**
 * Send confirm_result back to the PC bridge.
 * Extra fields (verified, wouldClick, …) are forwarded when present.
 */
function reportResult(orderId, ok, reason, extra) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const payload = {
    type: 'confirm_result',
    orderId,
    ok: Boolean(ok),
    reason: reason || null,
  };
  if (extra && typeof extra === 'object') {
    for (const key of Object.keys(extra)) {
      if (key === 'type' || key === 'orderId' || key === 'ok' || key === 'reason') continue;
      payload[key] = extra[key];
    }
  }
  socket.send(JSON.stringify(payload));
}

/** Forward opaque messages from content scripts onto the WS. */
function forwardToBridge(message) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  if (!message || typeof message !== 'object') return false;
  socket.send(JSON.stringify(message));
  return true;
}

/**
 * Find an admin tab by profile domain_patterns (not active:true — tab may be background).
 */
function forwardToAdminTab(data) {
  const orderId = data && data.orderId != null ? String(data.orderId) : '';
  chrome.storage.local.get([STORAGE_KEYS.siteProfiles], ({ siteProfiles }) => {
    // Re-assert the bundled workflow on EVERY confirm before the content script reads
    // storage — this guarantees the 16-step close_job_workflow is used even if a stale
    // 13-step profile was pushed since the last bootstrap. Commit first, then dispatch.
    const merged = mergeBundledProfiles(siteProfiles);
    const dispatch = () => forwardConfirmToTab(orderId, data, merged);
    if (JSON.stringify(siteProfiles || null) !== JSON.stringify(merged)) {
      chrome.storage.local.set({ [STORAGE_KEYS.siteProfiles]: merged }, dispatch);
    } else {
      dispatch();
    }
  });
}

function forwardConfirmToTab(orderId, data, profiles) {
  // Always fold the bundled profiles in again here so a transient empty/stale
  // `profiles` argument (e.g. storage read race) can never wrongly yield
  // no_site_profile while the bundled jinbao profile exists.
  const merged = mergeBundledProfiles(profiles);
  // Carry event_id + amount on EVERY result (incl. early failures) so the PC can
  // map the outcome back to the right Slip row instead of dropping to รอตรวจ.
  const meta = {};
  if (data && data.event_id != null) meta.event_id = data.event_id;
  if (data && data.amount != null) meta.amount = data.amount;

  const patterns = merged.flatMap((p) => (p && p.domain_patterns) || []);
  if (patterns.length === 0) {
    // Only possible when there are genuinely no bundled AND no stored profiles.
    reportResult(orderId, false, 'no_site_profile', meta);
    return;
  }

  const dispatchToTab = (tab) => {
    chrome.tabs.sendMessage(tab.id, data, (resp) => {
      if (chrome.runtime.lastError || !resp) {
        // Existing tab may predate extension reload — inject then retry once.
        chrome.scripting.executeScript(
          { target: { tabId: tab.id, allFrames: true }, files: ['engine.js', 'content-script.js'] },
          () => {
            if (chrome.runtime.lastError) {
              reportResult(orderId, false, 'content_script_unreachable', meta);
              return;
            }
            chrome.tabs.sendMessage(tab.id, data, (resp2) => {
              if (chrome.runtime.lastError || !resp2) {
                reportResult(orderId, false, 'content_script_unreachable', meta);
                return;
              }
              reportResult(orderId, resp2.ok, resp2.reason, { ...meta, ...resp2 });
            });
          }
        );
        return;
      }
      reportResult(orderId, resp.ok, resp.reason, { ...meta, ...resp });
    });
  };

  const pickTabAndSend = (tabs) => {
    // Prefer an ACTIVE tab (user is looking at it), then most recently accessed.
    const sorted = tabs
      .slice()
      .sort(
        (a, b) =>
          Number(Boolean(b.active)) - Number(Boolean(a.active)) ||
          (b.lastAccessed || 0) - (a.lastAccessed || 0)
      );
    dispatchToTab(sorted[0]);
  };

  chrome.tabs.query({ url: patterns }, (tabs) => {
    if (!chrome.runtime.lastError && tabs && tabs.length > 0) {
      pickTabAndSend(tabs);
      return;
    }
    // Retry once — the admin tab may still be loading / the query briefly raced.
    setTimeout(() => {
      chrome.tabs.query({ url: patterns }, (tabs2) => {
        if (chrome.runtime.lastError || !tabs2 || tabs2.length === 0) {
          reportResult(orderId, false, 'admin_tab_not_found', meta);
          return;
        }
        pickTabAndSend(tabs2);
      });
    }, 300);
  });
}

function handleServerMessage(raw) {
  let data;
  try {
    data = JSON.parse(raw);
  } catch (_) {
    return;
  }
  if (!data || typeof data !== 'object') return;

  switch (data.type) {
    case 'auth_success':
      setStatus('connected');
      break;
    case 'site_profiles':
      storeSiteProfiles(data.profiles);
      break;
    case 'ping':
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'pong' }));
      }
      break;
    case 'confirm_order':
      forwardToAdminTab(data);
      break;
    default:
      break;
  }
}

function connect() {
  chrome.storage.local.get(
    [STORAGE_KEYS.pairingToken, STORAGE_KEYS.wsUrl],
    ({ pairingToken, wsUrl }) => {
      const token = pairingToken || '';
      if (!token) {
        setStatus('needs_token');
        return;
      }
      if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
        return;
      }

      const url = wsUrl || DEFAULT_WS_URL;
      setStatus('connecting');
      try {
        socket = new WebSocket(url);
      } catch (_) {
        setStatus('error');
        scheduleReconnect();
        return;
      }

      socket.addEventListener('open', () => {
        clearReconnectTimer();
        socket.send(JSON.stringify({ type: 'auth', token }));
      });

      socket.addEventListener('message', (event) => {
        handleServerMessage(event.data);
      });

      socket.addEventListener('close', () => {
        setStatus('disconnected');
        socket = null;
        scheduleReconnect();
      });

      socket.addEventListener('error', () => {
        setStatus('error');
        // onclose will schedule reconnect when the socket finishes tearing down
        try {
          if (socket) socket.close();
        } catch (_) {
          /* ignore */
        }
      });
    }
  );
}

function ensureKeepaliveAlarm() {
  chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 1 });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm || alarm.name !== KEEPALIVE_ALARM) return;
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    connect();
  }
});

function mergeBundledProfiles(existing) {
  const bundled = Array.isArray(globalThis.BUNDLED_SITE_PROFILES)
    ? globalThis.BUNDLED_SITE_PROFILES
    : [];
  if (!bundled.length) return Array.isArray(existing) ? existing : [];
  const list = Array.isArray(existing) ? existing.slice() : [];
  for (const profile of bundled) {
    if (!profile || !profile.profile_id) continue;
    const idx = list.findIndex((p) => p && p.profile_id === profile.profile_id);
    if (idx >= 0) {
      const prev = list[idx] || {};
      // Bundled ALWAYS wins for close_job_workflow + field matchers (spread of the
      // bundled profile replaces every stored field). The ONLY thing we keep from the
      // stored profile is the admin's dry_run toggle, so a stale PC-pushed profile can
      // never downgrade the workflow (e.g. back to 13 steps).
      list[idx] = {
        ...profile,
        dry_run: typeof prev.dry_run === 'boolean' ? prev.dry_run : profile.dry_run,
      };
    } else {
      list.push(profile);
    }
  }
  return list;
}

function bootstrap() {
  chrome.storage.local.get([STORAGE_KEYS.siteProfiles, STORAGE_KEYS.connectionStatus], (data) => {
    const patch = {};
    const merged = mergeBundledProfiles(data.siteProfiles);
    const prev = Array.isArray(data.siteProfiles) ? data.siteProfiles : null;
    if (JSON.stringify(prev) !== JSON.stringify(merged)) {
      patch[STORAGE_KEYS.siteProfiles] = merged;
    }
    if (!data.connectionStatus) patch[STORAGE_KEYS.connectionStatus] = 'disconnected';
    if (Object.keys(patch).length) chrome.storage.local.set(patch);
  });
  ensureKeepaliveAlarm();
  connect();
}

chrome.runtime.onInstalled.addListener(bootstrap);
chrome.runtime.onStartup.addListener(bootstrap);

// Alarm may already exist from a previous SW lifetime — seed profiles + connect on wake.
bootstrap();

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes[STORAGE_KEYS.pairingToken] || changes[STORAGE_KEYS.wsUrl]) {
    clearReconnectTimer();
    if (socket) {
      try {
        socket.close();
      } catch (_) {
        /* ignore */
      }
      socket = null;
    }
    connect();
  }
});

/** MAIN-world SweetAlert2 confirm click (CSP-safe alternative to inline <script>). */
function mainWorldSwalClick() {
  try {
    if (window.Swal && typeof window.Swal.clickConfirm === 'function') {
      window.Swal.clickConfirm();
      return;
    }
  } catch (_) {
    /* ignore */
  }
  const btn = document.querySelector('button.swal2-confirm');
  if (btn) btn.click();
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== 'object') return;

  // Content script (isolated world) asks us to click SweetAlert2 in the MAIN world.
  if (message.type === 'main_world_swal_click') {
    const tabId = sender && sender.tab && sender.tab.id;
    if (typeof tabId === 'number' && chrome.scripting && chrome.scripting.executeScript) {
      try {
        chrome.scripting.executeScript(
          { target: { tabId, allFrames: true }, world: 'MAIN', func: mainWorldSwalClick },
          () => void chrome.runtime.lastError
        );
      } catch (_) {
        /* ignore */
      }
    }
    sendResponse({ ok: true });
    return true;
  }

  if (message.type === 'site_profiles') {
    storeSiteProfiles(message.profiles).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (message.type === 'get_status') {
    chrome.storage.local.get(
      [STORAGE_KEYS.connectionStatus, STORAGE_KEYS.siteProfiles, STORAGE_KEYS.pairingToken],
      (data) => sendResponse(data)
    );
    return true;
  }
  if (message.type === 'connect_now') {
    connect();
    sendResponse({ ok: true });
    return true;
  }
  // Content script → PC bridge (health, pending_orders, …)
  if (message.type === 'health' || message.type === 'pending_orders') {
    const ok = forwardToBridge(message);
    sendResponse({ ok });
    return true;
  }
  if (message.type === 'bridge_send' && message.payload) {
    const ok = forwardToBridge(message.payload);
    sendResponse({ ok });
    return true;
  }
});
