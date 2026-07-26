/**
 * Prefer admin tabs that reported role=approved (withdraw-notify approved list).
 * Pure — no chrome.*. UMD for Node tests + background importScripts.
 */

(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.ClipSyncAdminTabPick = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function adminTabPickFactory() {
  'use strict';

  /**
   * Sort comparator: approved role first, then active, then lastAccessed desc.
   * @param {{id?: number, active?: boolean, lastAccessed?: number}} a
   * @param {{id?: number, active?: boolean, lastAccessed?: number}} b
   * @param {Record<number|string, string>|null|undefined} roleByTabId
   */
  function compareAdminTabs(a, b, roleByTabId) {
    const roles = roleByTabId && typeof roleByTabId === 'object' ? roleByTabId : {};
    const roleA = a && a.id != null ? roles[a.id] : undefined;
    const roleB = b && b.id != null ? roles[b.id] : undefined;
    return (
      Number(roleB === 'approved') - Number(roleA === 'approved') ||
      Number(Boolean(b && b.active)) - Number(Boolean(a && a.active)) ||
      ((b && b.lastAccessed) || 0) - ((a && a.lastAccessed) || 0)
    );
  }

  /**
   * @param {Array<{id?: number, active?: boolean, lastAccessed?: number}>|null|undefined} tabs
   * @param {Record<number|string, string>|null|undefined} roleByTabId
   */
  function pickPreferredAdminTab(tabs, roleByTabId) {
    if (!tabs || !tabs.length) return null;
    return tabs.slice().sort((a, b) => compareAdminTabs(a, b, roleByTabId))[0] || null;
  }

  return {
    compareAdminTabs,
    pickPreferredAdminTab,
  };
});
