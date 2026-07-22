/**
 * ClipSync extension background (MV3 service worker).
 * Resilient localhost WS client + alarm keepalive (Task 4.2).
 */

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
  const list = Array.isArray(profiles) ? profiles : [];
  return chrome.storage.local.set({ [STORAGE_KEYS.siteProfiles]: list });
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
    const patterns = (siteProfiles || []).flatMap((p) => p.domain_patterns || []);
    if (patterns.length === 0) {
      reportResult(orderId, false, 'no_site_profile');
      return;
    }
    chrome.tabs.query({ url: patterns }, (tabs) => {
      if (chrome.runtime.lastError || !tabs || tabs.length === 0) {
        reportResult(orderId, false, 'admin_tab_not_found');
        return;
      }
      chrome.tabs.sendMessage(tabs[0].id, data, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          reportResult(orderId, false, 'content_script_unreachable');
          return;
        }
        reportResult(orderId, resp.ok, resp.reason, resp);
      });
    });
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

function bootstrap() {
  chrome.storage.local.get([STORAGE_KEYS.siteProfiles, STORAGE_KEYS.connectionStatus], (data) => {
    const patch = {};
    if (!Array.isArray(data.siteProfiles)) patch[STORAGE_KEYS.siteProfiles] = [];
    if (!data.connectionStatus) patch[STORAGE_KEYS.connectionStatus] = 'disconnected';
    if (Object.keys(patch).length) chrome.storage.local.set(patch);
  });
  ensureKeepaliveAlarm();
  connect();
}

chrome.runtime.onInstalled.addListener(bootstrap);
chrome.runtime.onStartup.addListener(bootstrap);

// Alarm may already exist from a previous SW lifetime — recreate + connect on wake.
ensureKeepaliveAlarm();
connect();

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

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message !== 'object') return;

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
