/**
 * Poll interval settings (pure — no chrome.*).
 * UMD export for Node tests, content script, and popup.
 */

(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.ClipSyncPollSettings = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function pollSettingsFactory() {
  'use strict';

  const DEFAULT_SCRAPE_POLL_MS = 45000;
  const DEFAULT_APPROVED_SEARCH_POLL_MS = 30000;

  function clampPollMs(value, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(300000, Math.max(10000, Math.round(n)));
  }

  function clampPendingOrdersPollMs(value, fallback = DEFAULT_SCRAPE_POLL_MS) {
    return clampPollMs(value, fallback);
  }

  function clampApprovedSearchPollMs(value, fallback = DEFAULT_APPROVED_SEARCH_POLL_MS) {
    return clampPollMs(value, fallback);
  }

  /** Default ON when unset. Explicit false / 'false' / 0 → OFF. */
  function isApprovedSearchAutoEnabled(value) {
    if (value === false || value === 0 || value === 'false') return false;
    return true;
  }

  /**
   * Popup line for chrome.storage.local.approvedSearchStatus.
   */
  function formatApprovedSearchStatusLine(status) {
    if (!status || typeof status !== 'object') {
      return {
        text: 'สถานะปุ่มค้นหา: ⏳ เปิดแท็บ Jinbao รายการที่อนุมัติแล้ว…',
        tone: 'muted',
      };
    }
    let head;
    let tone = 'muted';
    if (status.found === true) {
      const label = status.detail ? ` (${status.detail})` : '';
      head = `สถานะปุ่มค้นหา: ✅ เจอแล้ว${label}`;
      tone = 'ok';
    } else if (status.found === false) {
      head = 'สถานะปุ่มค้นหา: ❌ ไม่เจอปุ่มค้นหา';
      tone = 'bad';
    } else {
      head = 'สถานะปุ่มค้นหา: ⏳ เปิดแท็บ Jinbao รายการที่อนุมัติแล้ว…';
      tone = 'muted';
    }
    const reasonRaw = status.reason ? String(status.reason) : '';
    const reasonMap = {
      paused_for_confirm: 'พักรีเฟรช (กำลังยืนยัน/สลิป)',
      confirm_in_flight: 'พักรีเฟรช (กำลังยืนยัน)',
      busy: 'พักรีเฟรช (busy)',
      auto_search_off: 'ปิดกดค้นหาอัตโนมัติ',
      clicked: 'clicked',
      probed: 'probed',
    };
    const reason = reasonMap[reasonRaw] || reasonRaw;
    let timePart = '';
    if (status.at) {
      try {
        const d = new Date(status.at);
        if (!Number.isNaN(d.getTime())) {
          timePart = d.toLocaleTimeString('th-TH', { hour12: false });
        }
      } catch (_) {
        /* ignore */
      }
    }
    const tail =
      reason || timePart
        ? `ล่าสุด: ${[reason, timePart].filter(Boolean).join(' · ')}`
        : '';
    return {
      text: tail ? `${head}\n${tail}` : head,
      tone,
    };
  }

  return {
    clampPendingOrdersPollMs,
    clampApprovedSearchPollMs,
    isApprovedSearchAutoEnabled,
    formatApprovedSearchStatusLine,
    DEFAULT_SCRAPE_POLL_MS,
    DEFAULT_APPROVED_SEARCH_POLL_MS,
  };
});
