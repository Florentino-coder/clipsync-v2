/**
 * Content script — confirm flow, canary health, approved withdraw scrape (pending_orders wire).
 * Uses ClipSyncEngine (engine.js) for DOM logic.
 */

(function () {
  const E = typeof ClipSyncEngine !== 'undefined' ? ClipSyncEngine : null;
  if (!E) return;

  /** @type {Promise<void>} */
  let commandQueue = Promise.resolve();
  let confirmInFlight = false;
  /** Epoch ms — skip กดค้นหา until this time (slip match / brief post-confirm settle). */
  let approvedSearchPauseUntil = 0;
  /** Brief settle after confirm so Swal/modal can close — then resume Search immediately. */
  const SEARCH_PAUSE_AFTER_CONFIRM_MS = 2000;
  /** While confirm_order sits in the content-script queue (before handler). */
  const SEARCH_PAUSE_QUEUED_CONFIRM_MS = 5000;
  /** Default when PC sends pause_approved_search without ms. */
  const SEARCH_PAUSE_SLIP_DEFAULT_MS = 8000;

  /** Extend pause (never shortens). Used for slip-intake. */
  function pauseApprovedSearch(ms) {
    const until = Date.now() + Math.max(0, Number(ms) || 0);
    if (until > approvedSearchPauseUntil) approvedSearchPauseUntil = until;
  }

  /** Replace pause end time (can shorten). Used when confirm finishes. */
  function releaseApprovedSearchPause(cooldownMs) {
    approvedSearchPauseUntil = Date.now() + Math.max(0, Number(cooldownMs) || 0);
  }

  function approvedSearchIsPaused() {
    return confirmInFlight || Date.now() < approvedSearchPauseUntil;
  }

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

  // CSP-safe MAIN-world Swal click: inline <script> injection is blocked by strict
  // CSP, so ask the background service worker to run the click via
  // chrome.scripting.executeScript({ world: 'MAIN', func }).
  if (typeof E.setMainWorldClicker === 'function') {
    E.setMainWorldClicker(() => sendToBackground({ type: 'main_world_swal_click' }));
  }
  if (typeof E.setMainWorldApprovedSearchClicker === 'function') {
    E.setMainWorldApprovedSearchClicker(() =>
      sendToBackground({ type: 'main_world_approved_search_click' })
    );
  }

function showResultBanner(ok, detail) {
    const id = 'clipsync-result-banner';
    let banner = document.getElementById(id);
    if (!banner) {
      banner = document.createElement('div');
      banner.id = id;
      banner.style.cssText =
        'position:fixed;top:0;left:0;right:0;z-index:2147483647;color:#fff;padding:10px 14px;font:14px/1.4 sans-serif;text-align:center;';
      document.documentElement.appendChild(banner);
    }
    banner.style.background = ok ? '#2e7d32' : '#e53935';
    banner.textContent = detail || (ok ? 'ClipSync: ok' : 'ClipSync: failed');
    clearTimeout(showResultBanner._t);
    showResultBanner._t = setTimeout(() => banner.remove(), 12000);
  }

  function showDryRunBanner(detail) {
    showResultBanner(false, `ClipSync dry-run: กรอบแดงที่เป้าหมายแล้ว (${detail || 'ok'}) — ยังไม่กดจริง`);
  }

  function showSessionBanner() {
    showResultBanner(false, 'ClipSync: admin session expired — please log in again');
  }

  function profileForConfirm(profiles, orderId) {
    const list = activeProfiles(profiles);
    return list[0] || null;
  }

  function enrichSlip(slip) {
    const out = slip && typeof slip === 'object' ? { ...slip } : {};
    const raw = String(out.bank_name_th || out.bank_name || out.bank || '').trim();
    const upper = raw.toUpperCase();
    const map = {
      SCB: 'ธนาคารไทยพาณิชย์',
      KBANK: 'ธนาคารกสิกรไทย',
      BBL: 'ธนาคารกรุงเทพ',
      KTB: 'ธนาคารกรุงไทย',
      GSB: 'ธนาคารออมสิน',
      TTB: 'ธนาคารทหารไทยธนชาต',
      BAY: 'ธนาคารกรุงศรีอยุธยา',
      BAAC: 'ธนาคารเพื่อการเกษตรและสหกรณ์การเกษตร',
      KKP: 'ธนาคารเกียรตินาคินภัทร',
    };
    if (map[upper]) out.bank_name_th = map[upper];
    else if (!out.bank_name_th && raw) {
      // Already Thai / partial — keep; engine aliases still match.
      out.bank_name_th = raw.startsWith('ธนาคาร') ? raw : raw;
    }
    if (!out.bank_name && out.bank) out.bank_name = out.bank;
    if (!out.receiver_account_last4 && out.receiverAccountLast4) {
      out.receiver_account_last4 = out.receiverAccountLast4;
    }
    if (!out.receiver_bank && out.receiverBank) out.receiver_bank = out.receiverBank;
    if (!out.receiver_bank_name_th && out.receiverBankNameTh) {
      out.receiver_bank_name_th = out.receiverBankNameTh;
    }
    if (!out.sender_name && out.senderName) out.sender_name = out.senderName;
    // Shop payout account = the slip's "จาก/from" account. Expose its last-4 for the
    // หมายเลขบัญชี dropdown match (e.g. SCB "xxx-xxx747-6" → 7476).
    const last4 = (v) => {
      const d = String(v == null ? '' : v).replace(/\D/g, '');
      return d.length >= 4 ? d.slice(-4) : '';
    };
    if (!out.sender_account_last4) {
      out.sender_account_last4 =
        last4(out.senderAccountLast4) ||
        last4(out.sender_account) ||
        last4(out.senderAccount) ||
        last4(out.from_account) ||
        last4(out.fromAccount) ||
        '';
    }
    return out;
  }

  async function handleConfirmOrder(data, profiles) {
    confirmInFlight = true;
    // Do NOT arm a long wall-clock pause here — confirmInFlight already blocks Search.
    // A long pause survived after success and left popup stuck on พักรีเฟรช.
    try {
      const orderId = data && data.orderId != null ? String(data.orderId) : '';
      const amount = data && data.amount != null ? String(data.amount) : '';
      const refNumber = data && data.refNumber != null ? String(data.refNumber) : '';
      const eventId = data && data.event_id != null ? String(data.event_id) : '';
      const profile = profileForConfirm(profiles, orderId);
      if (!profile) {
        return { ok: false, reason: 'no_site_profile', event_id: eventId, amount: amount || undefined };
      }

      if (E.isLoggedOut(profile)) {
        showSessionBanner();
        return { ok: false, reason: 'session_expired', event_id: eventId, amount: amount || undefined };
      }

      const matchKeys = [orderId, refNumber, amount].filter((k) => k && k !== '-' && k !== 'None');
      if (matchKeys.length === 0) {
        return { ok: false, reason: 'no_match_key', event_id: eventId, amount: amount || undefined };
      }

      // Busy shield: dim/blur page + block clicks while automation runs (auto + manual ยืนยันเอง).
      // Auto-dismisses after ~75s if the engine hangs — never sticks forever.
      if (typeof E.showBusyShield === 'function') {
        E.showBusyShield({ amount: amount || undefined });
      }
      try {
        return await runConfirmWithShield(data, profile, {
          orderId,
          amount,
          refNumber,
          eventId,
          matchKeys,
        });
      } finally {
        if (typeof E.hideBusyShield === 'function') E.hideBusyShield();
      }
    } finally {
      confirmInFlight = false;
      // Overwrite any leftover slip/confirm pause — resume Search after short settle.
      releaseApprovedSearchPause(SEARCH_PAUSE_AFTER_CONFIRM_MS);
    }
  }

  async function runConfirmWithShield(data, profile, ctx) {
    const { amount, refNumber, eventId, matchKeys } = ctx;

    // Enrich slip before row lookup so same-amount rows can be narrowed by
    // member (payee) account + bank from the slip.
    const slip = enrichSlip(
      data && data.slip && typeof data.slip === 'object' ? { ...data.slip } : {}
    );
    if (!slip.amount && amount) slip.amount = amount;
    if (!slip.ref_number && refNumber) slip.ref_number = refNumber;
    const rowHints = {
      account_last4: slip.receiver_account_last4 || '',
      bank: slip.receiver_bank || slip.receiver_bank_name_th || '',
    };

    let rowResult = { status: 'row_not_found' };
    let usedKey = '';
    for (const key of matchKeys) {
      rowResult = E.findRow(profile, key, document, rowHints);
      if (rowResult.status === 'ok') {
        usedKey = key;
        break;
      }
      if (rowResult.status === 'ambiguous') {
        showResultBanner(
          false,
          `ClipSync: พบหลายแถวสำหรับ ${key} — ตรวจยอด+เลขบัญชี/ธนาคารสมาชิกแล้วยังกำกวม`
        );
        return { ok: false, reason: 'ambiguous', matchKey: key };
      }
    }
    if (rowResult.status !== 'ok') {
      const reason = rowResult.status || 'row_not_found';
      showResultBanner(
        false,
        `ClipSync: หาแถวไม่เจอ (${reason}) — ลองแล้ว: ${matchKeys.join(', ')} — ให้เปิดหน้าที่มีจำนวนตรงกับสลิป`
      );
      return { ok: false, reason, tried: matchKeys };
    }

    const dryRun = profile.dry_run !== false;
    showResultBanner(
      true,
      dryRun
        ? `ClipSync: โหมด dry_run — จะตีกรอบอย่างเดียว (${usedKey})`
        : `ClipSync: โหมดกดจริง — กำลังคลิก… (${usedKey})`
    );

    const workflow = profile.close_job_workflow;
    if (Array.isArray(workflow) && workflow.length > 0) {
      try {
        console.error(
          '[ClipSync] workflow begin',
          chrome.runtime.getManifest().version,
          'steps=',
          workflow.length,
          'dry_run=',
          dryRun
        );
      } catch (_) {
        /* ignore */
      }
      const result = await E.runWorkflow(
        profile,
        workflow,
        { row: rowResult.row, slip, matchKey: usedKey },
        { dry_run: dryRun, outline_only: dryRun }
      );
      try {
        console.error('[ClipSync] workflow end', result && result.ok, result && result.reason, result && result.failed_step);
      } catch (_) {
        /* ignore */
      }
      // The site can close the job while the workflow loses sight of the proof
      // (success dialog eaten, list re-rendered) — that is still a green result.
      const closedAnyway = result.reason === 'already_confirmed' || result.reason === 'success_observed';
      if (result.reason === 'dry_run') {
        showDryRunBanner(usedKey);
      } else if (result.ok && closedAnyway) {
        showResultBanner(true, `ClipSync: ปิดงานสำเร็จแล้ว (${result.via || 'already_confirmed'}) (จับ: ${usedKey})`);
      } else if (!result.ok) {
        // On account-field failures, surface the slip fields we actually received so we
        // can see which key (if any) carries the payer account number.
        let slipDiag = '';
        if (
          (result.reason === 'missing_select_value' ||
            result.reason === 'option_not_found' ||
            result.reason === 'bank_not_selected') &&
          String(result.field || '').includes('บัญชี')
        ) {
          const shown = {};
          for (const k of Object.keys(slip)) {
            if (k === 'thumbnail_jpeg_b64') continue;
            shown[k] = slip[k];
          }
          slipDiag = ' — slip=' + JSON.stringify(shown);
        }
        showResultBanner(
          false,
          `ClipSync: ล้มเหลว ${result.reason || 'workflow_failed'} ขั้น ${result.failed_step ?? '-'} ` +
            `${result.field ? '(' + result.field + ')' : ''} ` +
            `${result.tried_value ? 'ค่า=' + result.tried_value : ''} ` +
            `(จับ: ${usedKey})` +
            `${result.hint ? ' — ' + result.hint : ''}` +
            ` [ext ${chrome.runtime.getManifest().version}]` +
            slipDiag
        );
      } else {
        showResultBanner(true, `ClipSync: ยืนยันสำเร็จ (จับ: ${usedKey})`);
      }
      return {
        ...result,
        matchKey: usedKey,
        dry_run: dryRun,
        event_id: eventId,
        amount: amount || undefined,
      };
    }

    const btnResult = await E.waitForConfirmButton(profile, rowResult.row);
    if (btnResult.status === 'already_confirmed') {
      return {
        ok: true,
        verified: true,
        reason: 'already_confirmed',
        matchKey: usedKey,
        event_id: eventId,
        amount: amount || undefined,
      };
    }
    if (btnResult.status !== 'ok') {
      return { ok: false, reason: btnResult.status, matchKey: usedKey, event_id: eventId };
    }

    if (dryRun) {
      E.outlineButton(btnResult.btn);
      showDryRunBanner(usedKey);
      return {
        ok: false,
        reason: 'dry_run',
        wouldClick: true,
        matchKey: usedKey,
        event_id: eventId,
      };
    }

    if (typeof E.dispatchClick === 'function') E.dispatchClick(btnResult.btn);
    else btnResult.btn.click();
    const verify = await E.waitForPostClickVerify(profile, rowResult.row);
    if (!verify.ok) {
      return {
        ok: false,
        reason: verify.reason || 'clicked_but_unverified',
        matchKey: usedKey,
        event_id: eventId,
      };
    }
    return {
      ok: true,
      verified: true,
      matchKey: usedKey,
      event_id: eventId,
      amount: amount || undefined,
    };
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

  function publishApprovedSearchStatus(status) {
    try {
      chrome.storage.local.set({ approvedSearchStatus: status });
    } catch (_) {
      /* extension context invalidated */
    }
  }

  /** Tell background this tab is approved / pending / unknown so confirm routes here. */
  function reportTabRole(kind) {
    let role = 'unknown';
    if (kind === true) role = 'approved';
    else if (kind === false) role = 'pending';
    sendToBackground({ type: 'clipsync_tab_role', role });
  }

  function syncWatchHudForProfiles(profiles) {
    if (typeof E.syncApprovedWatchHud !== 'function') return;
    const list = activeProfiles(profiles);
    if (list.length === 0) {
      if (typeof E.hideApprovedWatchHud === 'function') E.hideApprovedWatchHud(document);
      reportTabRole(null);
      return;
    }
    const kind = E.syncApprovedWatchHud(list[0], document);
    reportTabRole(kind);
  }

  function probeAndStoreSearchStatus(profiles, clickResult, intervalMs) {
    if (typeof E.probeApprovedSearchStatus !== 'function') return null;
    const list = activeProfiles(profiles);
    const profile = list[0];
    if (!profile) {
      const empty = {
        found: null,
        reason: 'no_profile',
        detail: '',
        href: String(location.href || ''),
        at: new Date().toISOString(),
      };
      publishApprovedSearchStatus(empty);
      return empty;
    }
    const status = E.probeApprovedSearchStatus(profile, document, {
      clickResult: clickResult || undefined,
      intervalMs,
      at: new Date().toISOString(),
    });
    publishApprovedSearchStatus(status);
    return status;
  }

  function runApprovedSearchRefresh(profiles) {
    if (typeof E.maybeClickApprovedSearch !== 'function') return;
    syncWatchHudForProfiles(profiles);
    chrome.storage.local.get(
      ['approvedSearchPollMs', 'approvedSearchAutoEnabled'],
      (data) => {
        const autoOn = isApprovedSearchAutoEnabled(
          data && data.approvedSearchAutoEnabled
        );
        for (const profile of activeProfiles(profiles)) {
          const paused = approvedSearchIsPaused();
          let result;
          if (!autoOn) {
            result = { clicked: false, reason: 'auto_search_off' };
          } else if (paused) {
            result = { clicked: false, reason: 'paused_for_confirm' };
          } else {
            result = E.maybeClickApprovedSearch(profile, document, {
              confirmInFlight,
            });
          }
          probeAndStoreSearchStatus(
            [profile],
            result,
            data && data.approvedSearchPollMs
          );
          // After a real Search click, wait for 「พบ: N」 to settle then scrape.
          if (result && result.clicked) {
            void settleThenScrape(profiles);
          }
        }
      }
    );
  }

  async function settleThenScrape(profiles) {
    const maxMs = 2500;
    const stableMs = 800;
    const pollMs = 200;
    const start = Date.now();
    let last =
      typeof E.readResultsCountLabel === 'function'
        ? E.readResultsCountLabel(document)
        : null;
    let lastChange = Date.now();
    while (Date.now() - start < maxMs) {
      await new Promise((r) => setTimeout(r, pollMs));
      if (approvedSearchIsPaused() && confirmInFlight) return;
      const cur =
        typeof E.readResultsCountLabel === 'function'
          ? E.readResultsCountLabel(document)
          : null;
      if (cur !== last) {
        last = cur;
        lastChange = Date.now();
      } else if (
        cur != null &&
        Date.now() - lastChange >= stableMs &&
        typeof E.isResultsCountStable === 'function' &&
        E.isResultsCountStable([last, cur])
      ) {
        break;
      }
    }
    try {
      await publishPendingOrders(profiles);
    } catch (_) {
      /* ignore */
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

  let pendingOrdersTimer = null;
  let approvedSearchTimer = null;

  function restartPendingOrdersTimer(profiles, ms) {
    if (pendingOrdersTimer) clearInterval(pendingOrdersTimer);
    const interval = clampPendingOrdersPollMs(ms);
    pendingOrdersTimer = setInterval(() => publishPendingOrders(profiles), interval);
  }

  function restartApprovedSearchTimer(profiles, ms) {
    if (approvedSearchTimer) clearInterval(approvedSearchTimer);
    const interval = clampApprovedSearchPollMs(ms);
    approvedSearchTimer = setInterval(() => runApprovedSearchRefresh(profiles), interval);
  }

  function startCanaryInterval(profiles, pollMs, searchMs) {
    runHealthCheck(profiles);
    setInterval(() => runHealthCheck(profiles), 3 * 60 * 1000);
    restartPendingOrdersTimer(profiles, pollMs);
    restartApprovedSearchTimer(profiles, searchMs);
    probeAndStoreSearchStatus(profiles, null, searchMs);
    syncWatchHudForProfiles(profiles);
    // Keep HUD + tab role fresh when staff switches Jinbao tabs without full reload.
    setInterval(() => {
      chrome.storage.local.get(['siteProfiles'], ({ siteProfiles }) => {
        syncWatchHudForProfiles(siteProfiles || profiles);
      });
    }, 2000);
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    chrome.storage.local.get(['siteProfiles'], ({ siteProfiles }) => {
      const profiles = siteProfiles || [];
      if (activeProfiles(profiles).length === 0) return;
      if (changes.pendingOrdersPollMs) {
        restartPendingOrdersTimer(profiles, changes.pendingOrdersPollMs.newValue);
      }
      if (changes.approvedSearchPollMs) {
        restartApprovedSearchTimer(profiles, changes.approvedSearchPollMs.newValue);
      }
    });
  });

  chrome.storage.local.get(
    ['siteProfiles', 'pendingOrdersPollMs', 'approvedSearchPollMs'],
    (data) => {
      const profiles = data.siteProfiles || [];
      if (activeProfiles(profiles).length === 0) return;

      wireObservers(profiles);
      startCanaryInterval(profiles, data.pendingOrdersPollMs, data.approvedSearchPollMs);
    }
  );

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || typeof message !== 'object') return;

    if (message.type === 'get_approved_search_status') {
      chrome.storage.local.get(['siteProfiles', 'approvedSearchPollMs'], (data) => {
        const status = probeAndStoreSearchStatus(
          data.siteProfiles || [],
          null,
          data.approvedSearchPollMs
        );
        sendResponse({ ok: true, status: status || null });
      });
      return true;
    }

    if (message.type === 'confirm_order') {
      // Short pause only while queued — handleConfirmOrder clears to 2s settle on exit.
      pauseApprovedSearch(SEARCH_PAUSE_QUEUED_CONFIRM_MS);
      chrome.storage.local.get(['siteProfiles'], ({ siteProfiles }) => {
        enqueue(async () => {
          const resp = await handleConfirmOrder(message, siteProfiles || []);
          sendResponse(resp);
        });
      });
      return true;
    }

    // Optional: PC/bridge can ask to pause Search while a slip is being matched.
    if (message.type === 'pause_approved_search') {
      const ms = Number(message.ms);
      pauseApprovedSearch(
        Number.isFinite(ms) && ms > 0 ? ms : SEARCH_PAUSE_SLIP_DEFAULT_MS
      );
      sendResponse({ ok: true, until: approvedSearchPauseUntil });
      return true;
    }

    if (message.type === 'request_pending_scrape') {
      chrome.storage.local.get(['siteProfiles'], ({ siteProfiles }) => {
        void publishPendingOrders(siteProfiles || []).finally(() => {
          try {
            sendResponse({ ok: true });
          } catch (_) {
            /* ignore */
          }
        });
      });
      return true;
    }

    return;
  });
})();
