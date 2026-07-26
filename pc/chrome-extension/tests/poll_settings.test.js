/**
 * Poll interval clamp tests.
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const {
  clampPendingOrdersPollMs,
  clampApprovedSearchPollMs,
  DEFAULT_SCRAPE_POLL_MS,
  DEFAULT_APPROVED_SEARCH_POLL_MS,
} = require('../poll_settings.js');

describe('clampPendingOrdersPollMs', () => {
  it('keeps default-range values', () => {
    assert.strictEqual(clampPendingOrdersPollMs(45000), 45000);
  });

  it('clamps below minimum to 10s', () => {
    assert.strictEqual(clampPendingOrdersPollMs(1000), 10000);
  });

  it('clamps above maximum to 300s', () => {
    assert.strictEqual(clampPendingOrdersPollMs(999999), 300000);
  });

  it('returns fallback for non-numeric input', () => {
    assert.strictEqual(clampPendingOrdersPollMs('nope'), DEFAULT_SCRAPE_POLL_MS);
  });
});

describe('clampApprovedSearchPollMs', () => {
  it('defaults fallback to 30s', () => {
    assert.strictEqual(clampApprovedSearchPollMs('x'), DEFAULT_APPROVED_SEARCH_POLL_MS);
    assert.strictEqual(DEFAULT_APPROVED_SEARCH_POLL_MS, 30000);
  });

  it('clamps to 10s–300s', () => {
    assert.strictEqual(clampApprovedSearchPollMs(5000), 10000);
    assert.strictEqual(clampApprovedSearchPollMs(400000), 300000);
    assert.strictEqual(clampApprovedSearchPollMs(15000), 15000);
  });
});

describe('formatApprovedSearchStatusLine', () => {
  const { formatApprovedSearchStatusLine } = require('../poll_settings.js');

  it('shows waiting copy when status missing', () => {
    const line = formatApprovedSearchStatusLine(null);
    assert.match(line.text, /⏳/);
    assert.equal(line.tone, 'muted');
  });

  it('shows found + reason', () => {
    const line = formatApprovedSearchStatusLine({
      found: true,
      reason: 'clicked',
      detail: 'ค้นหา',
      at: '2026-07-26T04:32:01.000Z',
    });
    assert.match(line.text, /✅ เจอแล้ว/);
    assert.match(line.text, /ค้นหา/);
    assert.match(line.text, /clicked/);
    assert.equal(line.tone, 'ok');
  });

  it('shows not-found tone', () => {
    const line = formatApprovedSearchStatusLine({
      found: false,
      reason: 'no_button',
      at: '2026-07-26T04:32:01.000Z',
    });
    assert.match(line.text, /❌ ไม่เจอปุ่มค้นหา/);
    assert.equal(line.tone, 'bad');
  });

  it('maps paused_for_confirm to Thai pause label', () => {
    const line = formatApprovedSearchStatusLine({
      found: true,
      reason: 'paused_for_confirm',
      detail: 'ค้นหา',
      at: '2026-07-26T04:32:01.000Z',
    });
    assert.match(line.text, /พักรีเฟรช \(กำลังยืนยัน\/สลิป\)/);
    assert.equal(line.tone, 'ok');
  });
});

describe('isApprovedSearchAutoEnabled', () => {
  const { isApprovedSearchAutoEnabled } = require('../poll_settings.js');

  it('defaults ON when unset', () => {
    assert.equal(isApprovedSearchAutoEnabled(undefined), true);
    assert.equal(isApprovedSearchAutoEnabled(null), true);
    assert.equal(isApprovedSearchAutoEnabled(true), true);
  });

  it('is OFF only for explicit false-ish values', () => {
    assert.equal(isApprovedSearchAutoEnabled(false), false);
    assert.equal(isApprovedSearchAutoEnabled(0), false);
    assert.equal(isApprovedSearchAutoEnabled('false'), false);
  });
});
