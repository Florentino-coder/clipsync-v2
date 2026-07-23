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

  function showDryRunBanner(detail) {
    const id = 'clipsync-dry-run-banner';
    let banner = document.getElementById(id);
    if (!banner) {
      banner = document.createElement('div');
      banner.id = id;
      banner.style.cssText =
        'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#e53935;color:#fff;padding:10px 14px;font:14px sans-serif;text-align:center;';
      document.documentElement.appendChild(banner);
    }
    banner.textContent = `ClipSync dry-run: กรอบแดงที่เป้าหมายแล้ว (${detail || 'ok'}) — ยังไม่กดจริง`;
    clearTimeout(showDryRunBanner._t);
    showDryRunBanner._t = setTimeout(() => banner.remove(), 8000);
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
    const amount = data && data.amount != null ? String(data.amount) : '';
    const refNumber = data && data.refNumber != null ? String(data.refNumber) : '';
    const profile = profileForConfirm(profiles, orderId);
    if (!profile) return { ok: false, reason: 'no_site_profile' };

    if (E.isLoggedOut(profile)) {
      showSessionBanner();
      return { ok: false, reason: 'session_expired' };
    }

    const matchKeys = [orderId, refNumber, amount].filter((k) => k && k !== '-' && k !== 'None');
    if (matchKeys.length === 0) return { ok: false, reason: 'no_match_key' };

    let rowResult = { status: 'row_not_found' };
    let usedKey = '';
    for (const key of matchKeys) {
      rowResult = E.findRow(profile, key);
      if (rowResult.status === 'ok') {
        usedKey = key;
        break;
      }
      if (rowResult.status === 'ambiguous') {
        return { ok: false, reason: 'ambiguous', matchKey: key };
      }
    }
    if (rowResult.status !== 'ok') {
      return { ok: false, reason: rowResult.status, tried: matchKeys };
    }

    const dryRun = profile.dry_run !== false;
    const workflow = profile.close_job_workflow;
    if (Array.isArray(workflow) && workflow.length > 0) {
      const slip = data && data.slip && typeof data.slip === 'object' ? data.slip : {};
      if (!slip.amount && amount) slip.amount = amount;
      if (!slip.ref_number && refNumber) slip.ref_number = refNumber;
      const result = await E.runWorkflow(
        profile,
        workflow,
        { row: rowResult.row, slip, matchKey: usedKey },
        { dry_run: dryRun, outline_only: dryRun }
      );
      if (result.reason === 'dry_run') {
        showDryRunBanner(usedKey);
      }
      return { ...result, matchKey: usedKey };
    }

    const btnResult = await E.waitForConfirmButton(profile, rowResult.row);
    if (btnResult.status === 'already_confirmed') {
      return { ok: true, verified: true, reason: 'already_confirmed', matchKey: usedKey };
    }
    if (btnResult.status !== 'ok') {
      return { ok: false, reason: btnResult.status, matchKey: usedKey };
    }

    if (dryRun) {
      E.outlineButton(btnResult.btn);
      showDryRunBanner(usedKey);
      return { ok: false, reason: 'dry_run', wouldClick: true, matchKey: usedKey };
    }

    btnResult.btn.click();
    const verify = await E.waitForPostClickVerify(profile, rowResult.row);
    if (!verify.ok) return { ok: false, reason: verify.reason || 'clicked_but_unverified', matchKey: usedKey };
    return { ok: true, verified: true, matchKey: usedKey };
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
