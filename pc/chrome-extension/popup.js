/**
 * Popup: pairing token, connection status, profile list, per-profile dry_run toggle.
 */

const statusEl = document.getElementById('status');
const tokenEl = document.getElementById('token');
const saveBtn = document.getElementById('saveToken');
const profileListEl = document.getElementById('profileList');
const profileCountEl = document.getElementById('profileCount');
const emptyProfilesEl = document.getElementById('emptyProfiles');

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
  chrome.storage.local.get(['pairingToken', 'connectionStatus', 'siteProfiles'], (data) => {
    if (data.pairingToken) tokenEl.value = data.pairingToken;
    renderStatus(data.connectionStatus);
    renderProfiles(data.siteProfiles);
  });
}

saveBtn.addEventListener('click', () => {
  const token = tokenEl.value.trim();
  chrome.storage.local.set({ pairingToken: token }, () => {
    chrome.runtime.sendMessage({ type: 'connect_now' });
    refresh();
  });
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.connectionStatus) renderStatus(changes.connectionStatus.newValue);
  if (changes.siteProfiles) renderProfiles(changes.siteProfiles.newValue);
  if (changes.pairingToken && changes.pairingToken.newValue !== undefined) {
    tokenEl.value = changes.pairingToken.newValue || '';
  }
});

refresh();
const verEl = document.getElementById('extVersion');
if (verEl) verEl.textContent = 'v' + (chrome.runtime.getManifest().version || '?');
