/**
 * ClipSync extension background (MV3 service worker).
 * Task 4.1: minimal WS stub + site_profiles → chrome.storage.local.
 * Full MV3 keepalive / reconnect is Task 4.2.
 */

const DEFAULT_WS_URL = 'ws://127.0.0.1:8765';
const STORAGE_KEYS = {
  pairingToken: 'pairingToken',
  connectionStatus: 'connectionStatus',
  siteProfiles: 'siteProfiles',
  wsUrl: 'wsUrl',
};

let socket = null;

function setStatus(status) {
  chrome.storage.local.set({ [STORAGE_KEYS.connectionStatus]: status });
}

function storeSiteProfiles(profiles) {
  const list = Array.isArray(profiles) ? profiles : [];
  return chrome.storage.local.set({ [STORAGE_KEYS.siteProfiles]: list });
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
      // Task 4.2+ forwards to admin tabs; stub ignores for now.
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
        return;
      }

      socket.addEventListener('open', () => {
        socket.send(JSON.stringify({ type: 'auth', token }));
      });

      socket.addEventListener('message', (event) => {
        handleServerMessage(event.data);
      });

      socket.addEventListener('close', () => {
        setStatus('disconnected');
        socket = null;
        // Minimal stub reconnect — Task 4.2 hardens keepalive.
        setTimeout(connect, 3000);
      });

      socket.addEventListener('error', () => {
        setStatus('error');
      });
    }
  );
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get([STORAGE_KEYS.siteProfiles, STORAGE_KEYS.connectionStatus], (data) => {
    const patch = {};
    if (!Array.isArray(data.siteProfiles)) patch[STORAGE_KEYS.siteProfiles] = [];
    if (!data.connectionStatus) patch[STORAGE_KEYS.connectionStatus] = 'disconnected';
    if (Object.keys(patch).length) chrome.storage.local.set(patch);
  });
  connect();
});

chrome.runtime.onStartup.addListener(connect);

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes[STORAGE_KEYS.pairingToken] || changes[STORAGE_KEYS.wsUrl]) {
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

// Popup / future callers can push profiles into storage without WS.
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
});
