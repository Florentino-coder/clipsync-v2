/**
 * Admin tab routing preference — prefer role=approved over active/lastAccessed.
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const {
  compareAdminTabs,
  pickPreferredAdminTab,
} = require('../admin_tab_pick.js');

describe('compareAdminTabs / pickPreferredAdminTab', () => {
  it('prefers role=approved over an active non-approved tab', () => {
    const roles = { 1: 'pending', 2: 'approved' };
    const activePending = { id: 1, active: true, lastAccessed: 900 };
    const inactiveApproved = { id: 2, active: false, lastAccessed: 100 };
    assert.ok(compareAdminTabs(inactiveApproved, activePending, roles) < 0);
    assert.equal(pickPreferredAdminTab([activePending, inactiveApproved], roles).id, 2);
  });

  it('falls back to active when neither tab is approved', () => {
    const roles = { 1: 'pending', 2: 'unknown' };
    const tabs = [
      { id: 2, active: false, lastAccessed: 500 },
      { id: 1, active: true, lastAccessed: 100 },
    ];
    assert.equal(pickPreferredAdminTab(tabs, roles).id, 1);
  });

  it('falls back to lastAccessed when active ties', () => {
    const roles = {};
    const older = { id: 1, active: false, lastAccessed: 10 };
    const newer = { id: 2, active: false, lastAccessed: 99 };
    assert.equal(pickPreferredAdminTab([older, newer], roles).id, 2);
  });

  it('returns null for empty tab list', () => {
    assert.equal(pickPreferredAdminTab([], {}), null);
    assert.equal(pickPreferredAdminTab(null, {}), null);
  });
});
