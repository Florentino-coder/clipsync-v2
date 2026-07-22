/**
 * Content script — confirm flow, canary health, pending_orders scrape.
 * Uses ClipSyncEngine (engine.js) for DOM logic.
 */

(function () {
  const E = typeof ClipSyncEngine !== 'undefined' ? ClipSyncEngine : null;
  if (!E) return;

  /** @type {Promise<void>} */
  let commandQueue = Promise.resolve();

  function urlMatchesPattern(url, pattern) {
    if (typeof pattern !== 'string' || !pattern) return false;
    if (pattern.endsWith('*')) return url.startsWith(pattern.slice(0, -1));
    return url === pattern;
  }

  function activeProfiles(profiles) {
    const href = location.href;
    return (profiles || []).filter((p) =>
      (p.domain_patterns || []).some((pat) => urlMatchesPattern(href, pat))
    );
  }

  function sendToBackground(message) {
    try {
      chrome.runtime.sendMessage(message);
    } catch (_) {
      /* extension context invalidated */
    }
  }

  function showSessionBanner() {
    if (document.getElementById('clipsync-session-banner')) return;
    const banner = document.createElement('div');
    banner.id = 'clipsync-session-banner';
    banner.textContent = 'ClipSync: admin session expired — please log in again';
    banner.style.cssText =
      'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#c62828;color:#fff;padding:8px 12px;font:14px sans-serif;text-align:center;';
    document.documentElement.appendChild(banner);
  }

  function profileForConfirm(profiles, orderId) {
    const list = activeProfiles(profiles);
    return list[0] || null;
  }

  async function handleConfirmOrder(data, profiles) {
    const orderId = data && data.orderId != null ? String(data.orderId) : '';
    const profile = profileForConfirm(profiles, orderId);
    if (!profile) return { ok: false, reason: 'no_site_profile' };

    if (E.isLoggedOut(profile)) {
      showSessionBanner();
      return { ok: false, reason: 'session_expired' };
    }

    const rowResult = E.findRow(profile, orderId);
    if (rowResult.status !== 'ok') {
      return { ok: false, reason: rowResult.status };
    }

    const btnResult = await E.waitForConfirmButton(profile, rowResult.row);
    if (btnResult.status === 'already_confirmed') {
      return { ok: true, verified: true, reason: 'already_confirmed' };
    }
    if (btnResult.status !== 'ok') {
      return { ok: false, reason: btnResult.status };
    }

    const dryRun = profile.dry_run !== false;
    if (dryRun) {
      E.outlineButton(btnResult.btn);
      return { ok: false, reason: 'dry_run', wouldClick: true };
    }

    btnResult.btn.click();
    const verify = await E.waitForPostClickVerify(profile, rowResult.row);
    if (!verify.ok) return { ok: false, reason: verify.reason || 'clicked_but_unverified' };
    return { ok: true, verified: true };
  }

  function runHealthCheck(profiles) {
    for (const profile of activeProfiles(profiles)) {
      const health = E.checkCanary(profile);
      sendToBackground({
        type: 'health',
        profile_id: profile.profile_id,
        canary_ok: health.canary_ok,
        logged_in: health.logged_in,
      });
    }
  }

  let scrapeTimer = null;
  function schedulePendingScrape(profiles) {
    if (scrapeTimer) clearTimeout(scrapeTimer);
    scrapeTimer = setTimeout(() => publishPendingOrders(profiles), 2000);
  }

  async function publishPendingOrders(profiles) {
    for (const profile of activeProfiles(profiles)) {
      if (profile.api && profile.api.enabled) {
        const adapter = E.apiAdapter(profile, fetch.bind(window));
        const result = await adapter.listPending({});
        if (result.status === 'session_expired') {
          showSessionBanner();
          continue;
        }
        if (result.status === 'ok') {
          sendToBackground({
            type: 'pending_orders',
            profile_id: profile.profile_id,
            source: 'api',
            orders: result.orders,
          });
          continue;
        }
      }

      const orders = E.scrapePendingOrders(profile);
      sendToBackground({
        type: 'pending_orders',
        profile_id: profile.profile_id,
        source: 'dom',
        orders,
      });
    }
  }

  function enqueue(fn) {
    commandQueue = commandQueue.then(fn).catch(() => {});
    return commandQueue;
  }

  function wireObservers(profiles) {
    if (!document.body) return;
    const observer = new MutationObserver(() => schedulePendingScrape(profiles));
    observer.observe(document.body, { childList: true, subtree: true });
    schedulePendingScrape(profiles);
  }

  function startCanaryInterval(profiles) {
    runHealthCheck(profiles);
    setInterval(() => runHealthCheck(profiles), 3 * 60 * 1000);
    setInterval(() => publishPendingOrders(profiles), 45000);
  }

  chrome.storage.local.get(['siteProfiles'], ({ siteProfiles }) => {
    const profiles = siteProfiles || [];
    if (activeProfiles(profiles).length === 0) return;

    wireObservers(profiles);
    startCanaryInterval(profiles);
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== 'confirm_order') return;

    chrome.storage.local.get(['siteProfiles'], ({ siteProfiles }) => {
      enqueue(async () => {
        const resp = await handleConfirmOrder(message, siteProfiles || []);
        sendResponse(resp);
      });
    });
    return true;
  });
})();
