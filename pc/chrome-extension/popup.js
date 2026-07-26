/**
 * Popup: pairing token, connection status, profile list, poll + search refresh settings.
 */

const statusEl = document.getElementById('status');
const tokenEl = document.getElementById('token');
const saveBtn = document.getElementById('saveToken');
const profileListEl = document.getElementById('profileList');
const profileCountEl = document.getElementById('profileCount');
const emptyProfilesEl = document.getElementById('emptyProfiles');
const pollSecondsEl = document.getElementById('pollSeconds');
const pollPresetsEl = document.getElementById('pollPresets');
const savePollBtn = document.getElementById('savePoll');
const searchSecondsEl = document.getElementById('searchSeconds');
const searchPresetsEl = document.getElementById('searchPresets');
const saveSearchPollBtn = document.getElementById('saveSearchPoll');
const saveScrapeFlashEl = document.getElementById('saveScrapeFlash');
const saveSearchFlashEl = document.getElementById('saveSearchFlash');
const searchBtnStatusEl = document.getElementById('searchBtnStatus');
const searchAutoEnabledEl = document.getElementById('searchAutoEnabled');

const clampPendingOrdersPollMs =
  typeof ClipSyncPollSettings !== 'undefined'
    ? ClipSyncPollSettings.clampPendingOrdersPollMs
    : (value, fallback = 45000) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return fallback;
        return Math.min(300000, Math.max(10000, Math.round(n)));
      };

const clampApprovedSearchPollMs =
  typeof ClipSyncPollSettings !== 'undefined'
    ? ClipSyncPollSettings.clampApprovedSearchPollMs
    : (value, fallback = 30000) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return fallback;
        return Math.min(300000, Math.max(10000, Math.round(n)));
      };

const isApprovedSearchAutoEnabled =
  typeof ClipSyncPollSettings !== 'undefined' &&
  typeof ClipSyncPollSettings.isApprovedSearchAutoEnabled === 'function'
    ? ClipSyncPollSettings.isApprovedSearchAutoEnabled
    : (value) => value !== false && value !== 0 && value !== 'false';

const formatApprovedSearchStatusLine =
  typeof ClipSyncPollSettings !== 'undefined' &&
  typeof ClipSyncPollSettings.formatApprovedSearchStatusLine === 'function'
    ? ClipSyncPollSettings.formatApprovedSearchStatusLine
    : (status) => {
        if (!status || status.found == null) {
          return {
            text: 'สถานะปุ่มค้นหา: ⏳ เปิดแท็บ Jinbao รายการที่อนุมัติแล้ว…',
            tone: 'muted',
          };
        }
        if (status.found) {
          return {
            text: `สถานะปุ่มค้นหา: ✅ เจอแล้ว${status.detail ? ` (${status.detail})` : ''}`,
            tone: 'ok',
          };
        }
        return { text: 'สถานะปุ่มค้นหา: ❌ ไม่เจอปุ่มค้นหา', tone: 'bad' };
      };

const DEFAULT_POLL_MS =
  (typeof ClipSyncPollSettings !== 'undefined' &&
    ClipSyncPollSettings.DEFAULT_SCRAPE_POLL_MS) ||
  45000;
const DEFAULT_SEARCH_POLL_MS =
  (typeof ClipSyncPollSettings !== 'undefined' &&
    ClipSyncPollSettings.DEFAULT_APPROVED_SEARCH_POLL_MS) ||
  30000;

let scrapeFlashTimer = null;
let searchFlashTimer = null;

function msToSeconds(ms, clampFn) {
  return Math.round(clampFn(ms) / 1000);
}

function showFlash(el, text, timerRef) {
  if (!el) return null;
  el.textContent = text;
  if (timerRef) clearTimeout(timerRef);
  return setTimeout(() => {
    el.textContent = '';
  }, 3000);
}

function renderPollSettings(pendingOrdersPollMs) {
  if (!pollSecondsEl) return;
  pollSecondsEl.value = String(
    msToSeconds(pendingOrdersPollMs, clampPendingOrdersPollMs)
  );
}

function renderSearchSettings(approvedSearchPollMs, approvedSearchAutoEnabled) {
  if (searchSecondsEl) {
    searchSecondsEl.value = String(
      msToSeconds(approvedSearchPollMs, clampApprovedSearchPollMs)
    );
  }
  if (searchAutoEnabledEl) {
    searchAutoEnabledEl.checked = isApprovedSearchAutoEnabled(approvedSearchAutoEnabled);
  }
}

function renderSearchBtnStatus(status) {
  if (!searchBtnStatusEl) return;
  const line = formatApprovedSearchStatusLine(status);
  searchBtnStatusEl.textContent = line.text;
  searchBtnStatusEl.dataset.tone = line.tone || 'muted';
}

function requestFreshSearchStatus() {
  try {
    chrome.runtime.sendMessage({ type: 'get_approved_search_status' }, (resp) => {
      void chrome.runtime.lastError;
      if (resp && resp.status) renderSearchBtnStatus(resp.status);
    });
  } catch (_) {
    /* ignore */
  }
}

function savePollSettings() {
  const sec = Number(pollSecondsEl.value);
  const ms = clampPendingOrdersPollMs(sec * 1000);
  const shownSec = msToSeconds(ms, clampPendingOrdersPollMs);
  chrome.storage.local.set({ pendingOrdersPollMs: ms }, () => {
    renderPollSettings(ms);
    scrapeFlashTimer = showFlash(
      saveScrapeFlashEl,
      `✓ บันทึกแล้ว (${shownSec}s)`,
      scrapeFlashTimer
    );
  });
}

function saveSearchSettings() {
  const sec = Number(searchSecondsEl.value);
  const ms = clampApprovedSearchPollMs(sec * 1000);
  const shownSec = msToSeconds(ms, clampApprovedSearchPollMs);
  const autoOn = searchAutoEnabledEl ? Boolean(searchAutoEnabledEl.checked) : true;
  chrome.storage.local.set(
    { approvedSearchPollMs: ms, approvedSearchAutoEnabled: autoOn },
    () => {
      renderSearchSettings(ms, autoOn);
      const mode = autoOn ? 'กดค้นหาอัตโนมัติเปิด' : 'กดค้นหาอัตโนมัติปิด';
      searchFlashTimer = showFlash(
        saveSearchFlashEl,
        `✓ บันทึกแล้ว (${shownSec}s, ${mode})`,
        searchFlashTimer
      );
      try {
        window.alert(
          autoOn
            ? `บันทึกแล้ว: รีเฟรชหน้าทุก ${shownSec} วินาที`
            : `บันทึกแล้ว: ปิดกดค้นหาอัตโนมัติ (scrape/HUD ยังทำงาน)`
        );
      } catch (_) {
        /* ignore */
      }
    }
  );
}

function renderStatus(status) {
  const value = status || 'disconnected';
  statusEl.textContent = value;
  statusEl.dataset.state = value;
}

function renderProfiles(profiles) {
  const list = Array.isArray(profiles) ? profiles : [];
  profileListEl.innerHTML = '';
  profileCountEl.textContent = String(list.length);
  emptyProfilesEl.hidden = list.length > 0;

  list.forEach((profile, index) => {
    const li = document.createElement('li');
    const id = profile.profile_id || `profile_${index}`;
    const domains = (profile.domain_patterns || []).join(', ') || '(no domains)';

    const title = document.createElement('div');
    title.innerHTML = `<strong>${escapeHtml(id)}</strong>`;
    const domain = document.createElement('div');
    domain.className = 'muted';
    domain.textContent = domains;

    const toggleLabel = document.createElement('label');
    toggleLabel.className = 'toggle';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = profile.dry_run !== false;
    checkbox.addEventListener('change', () => {
      list[index] = { ...profile, dry_run: checkbox.checked };
      chrome.storage.local.set({ siteProfiles: list });
    });
    toggleLabel.appendChild(checkbox);
    toggleLabel.appendChild(document.createTextNode('dry_run (outline only)'));

    li.appendChild(title);
    li.appendChild(domain);
    li.appendChild(toggleLabel);
    profileListEl.appendChild(li);
  });
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function refresh() {
  chrome.storage.local.get(
    [
      'pairingToken',
      'connectionStatus',
      'siteProfiles',
      'pendingOrdersPollMs',
      'approvedSearchPollMs',
      'approvedSearchAutoEnabled',
      'approvedSearchStatus',
    ],
    (data) => {
      if (data.pairingToken) tokenEl.value = data.pairingToken;
      renderStatus(data.connectionStatus);
      renderProfiles(data.siteProfiles);
      renderPollSettings(
        data.pendingOrdersPollMs != null ? data.pendingOrdersPollMs : DEFAULT_POLL_MS
      );
      renderSearchSettings(
        data.approvedSearchPollMs != null
          ? data.approvedSearchPollMs
          : DEFAULT_SEARCH_POLL_MS,
        data.approvedSearchAutoEnabled
      );
      renderSearchBtnStatus(data.approvedSearchStatus || null);
      requestFreshSearchStatus();
    }
  );
}

saveBtn.addEventListener('click', () => {
  const token = tokenEl.value.trim();
  chrome.storage.local.set({ pairingToken: token }, () => {
    chrome.runtime.sendMessage({ type: 'connect_now' });
    refresh();
  });
});

if (pollPresetsEl) {
  pollPresetsEl.addEventListener('click', (event) => {
    const btn = event.target.closest('button[data-sec]');
    if (!btn || !pollSecondsEl) return;
    pollSecondsEl.value = btn.dataset.sec;
  });
}

if (searchPresetsEl) {
  searchPresetsEl.addEventListener('click', (event) => {
    const btn = event.target.closest('button[data-sec]');
    if (!btn || !searchSecondsEl) return;
    searchSecondsEl.value = btn.dataset.sec;
  });
}

if (savePollBtn) {
  savePollBtn.addEventListener('click', savePollSettings);
}

if (saveSearchPollBtn) {
  saveSearchPollBtn.addEventListener('click', saveSearchSettings);
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.connectionStatus) renderStatus(changes.connectionStatus.newValue);
  if (changes.siteProfiles) renderProfiles(changes.siteProfiles.newValue);
  if (changes.pairingToken && changes.pairingToken.newValue !== undefined) {
    tokenEl.value = changes.pairingToken.newValue || '';
  }
  if (changes.pendingOrdersPollMs) {
    renderPollSettings(changes.pendingOrdersPollMs.newValue);
  }
  if (changes.approvedSearchPollMs) {
    renderSearchSettings(
      changes.approvedSearchPollMs.newValue,
      searchAutoEnabledEl ? searchAutoEnabledEl.checked : true
    );
  }
  if (changes.approvedSearchAutoEnabled) {
    renderSearchSettings(
      searchSecondsEl
        ? clampApprovedSearchPollMs(Number(searchSecondsEl.value) * 1000)
        : DEFAULT_SEARCH_POLL_MS,
      changes.approvedSearchAutoEnabled.newValue
    );
  }
  if (changes.approvedSearchStatus) {
    renderSearchBtnStatus(changes.approvedSearchStatus.newValue || null);
  }
});

refresh();
const verEl = document.getElementById('extVersion');
if (verEl) verEl.textContent = 'v' + (chrome.runtime.getManifest().version || '?');
