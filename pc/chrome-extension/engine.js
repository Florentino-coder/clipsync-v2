/**
 * ClipSync text-anchor + workflow engine (pure functions — no chrome.*).
 * UMD export for Node tests and browser content scripts.
 */

(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.ClipSyncEngine = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function engineFactory() {
  'use strict';

  // Strip spaces, dashes, and thousands separators so 1,347.00 matches 1347.00
  const normalize = (s) => (s || '').replace(/[\s\-,\u00a0]/g, '');

  const POPUP_SCOPE_HINTS = [
    '.el-dialog__wrapper .el-dialog',
    '.el-dialog',
    "[class*='modal']",
    "[class*='popup']",
    'dialog',
    "[role='dialog']",
  ];

  const BANK_ALIASES = {
    SCB: ['ธนาคารไทยพาณิชย์', 'ไทยพาณิชย์', 'SCB', 'Siam Commercial'],
    KBANK: ['ธนาคารกสิกรไทย', 'กสิกรไทย', 'กสิกร', 'KBank', 'KBANK'],
    BBL: ['ธนาคารกรุงเทพ', 'กรุงเทพ', 'BBL', 'Bangkok'],
    KTB: ['ธนาคารกรุงไทย', 'กรุงไทย', 'KTB', 'Krungthai'],
    GSB: ['ธนาคารออมสิน', 'ออมสิน', 'GSB'],
    TTB: ['ธนาคารทหารไทยธนชาต', 'ทหารไทย', 'ธนชาต', 'TTB', 'ttb'],
    BAY: ['ธนาคารกรุงศรีอยุธยา', 'กรุงศรี', 'BAY', 'Krungsri'],
  };

  const BANK_FULL_TH = {
    SCB: 'ธนาคารไทยพาณิชย์',
    KBANK: 'ธนาคารกสิกรไทย',
    BBL: 'ธนาคารกรุงเทพ',
    KTB: 'ธนาคารกรุงไทย',
    GSB: 'ธนาคารออมสิน',
    TTB: 'ธนาคารทหารไทยธนชาต',
    BAY: 'ธนาคารกรุงศรีอยุธยา',
  };

  function getDocument(doc) {
    return doc || (typeof document !== 'undefined' ? document : null);
  }

  /** Expand amount/ref needles so 1175.0 matches 1,175.00 on the page. */
  function matchNeedles(raw) {
    const n = normalize(String(raw == null ? '' : raw));
    if (!n) return [];
    const out = new Set([n]);
    if (/^\d+(\.\d+)?$/.test(n)) {
      const num = Number(n);
      if (!Number.isNaN(num)) {
        out.add(num.toFixed(2));
        out.add(String(Math.trunc(num)));
      }
    }
    return [...out];
  }

  function deepFindByText(root, needle, doc) {
    const hits = [];
    if (!root || !needle) return hits;

    const walk = (node) => {
      if (!node) return;
      if (node.shadowRoot) walk(node.shadowRoot);
      const children = node.children || [];
      for (const child of children) walk(child);
      if (children.length === 0 && normalize(node.textContent).includes(needle)) {
        hits.push(node);
      }
    };
    walk(root);
    return hits;
  }

  function rowSelector(profile) {
    return (profile.row_selector_hints || []).join(',');
  }

  function dedupeNestedRows(rows) {
    // Prefer innermost match (el-table often nests tr / row wrappers).
    return rows.filter(
      (row) => !rows.some((other) => other !== row && row.contains && row.contains(other))
    );
  }

  function collectRowsForNeedle(document, selector, needle) {
    const rowsFromLeaves = [];
    const hits = deepFindByText(document.body, needle, document);
    for (const el of hits) {
      const row = selector ? el.closest(selector) : el.parentElement;
      if (row) rowsFromLeaves.push(row);
    }
    let rows = [...new Set(rowsFromLeaves)];
    if (rows.length === 0 && selector) {
      try {
        rows = [...document.querySelectorAll(selector)].filter((row) =>
          normalize(row.textContent || '').includes(needle)
        );
      } catch (_) {
        rows = [];
      }
    }
    return dedupeNestedRows(rows);
  }

  /**
   * When several rows share the same amount/ref, narrow by member (payee) account
   * and bank from the slip. Account last-4 is the primary disambiguator; bank is
   * applied when provided (receiver/member bank, not the shop/sender bank).
   */
  function filterRowsBySlipHints(rows, hints) {
    if (!hints || !rows || rows.length <= 1) return rows;
    let filtered = rows;
    const last4 = String(hints.account_last4 || hints.receiver_account_last4 || '')
      .replace(/\D/g, '')
      .slice(-4);
    if (last4.length === 4) {
      filtered = filtered.filter((row) =>
        String(row.textContent || '').replace(/\D/g, '').includes(last4)
      );
    }
    const bank = hints.bank || hints.receiver_bank || hints.bank_name_th || '';
    if (bank && filtered.length > 1) {
      const bankNeedles = bankMatchNeedles(bank);
      filtered = filtered.filter((row) => {
        const text = String(row.textContent || '');
        return bankNeedles.some((n) => text.includes(n));
      });
    }
    return filtered;
  }

  function findRow(profile, refNumber, doc, hints) {
    const document = getDocument(doc);
    if (!document || !document.body) return { status: 'row_not_found' };

    const needles = matchNeedles(refNumber).slice().sort((a, b) => b.length - a.length);
    if (needles.length === 0) return { status: 'row_not_found' };

    const selector = rowSelector(profile);
    let ambiguousRows = null;

    // Try most specific needle first (1828.00 before 1828).
    for (const needle of needles) {
      let rows = collectRowsForNeedle(document, selector, needle);
      if (rows.length > 1) rows = filterRowsBySlipHints(rows, hints);
      if (rows.length === 1) return { status: 'ok', row: rows[0] };
      if (rows.length > 1) ambiguousRows = rows;
    }

    if (ambiguousRows) return { status: 'ambiguous' };
    return { status: 'row_not_found' };
  }

  function findConfirmButton(profile, row) {
    if (!row) return { status: 'button_not_found_in_row' };

    const indicators = profile.already_confirmed_indicators || [];
    if (indicators.some((k) => (row.textContent || '').includes(k))) {
      return { status: 'already_confirmed' };
    }

    const kw = new RegExp((profile.confirm_keywords || []).join('|'), 'i');
    const btns = [...row.querySelectorAll("button, a[role='button'], [onclick], input[type='submit']")].filter(
      (b) => kw.test((b.textContent || '') + (b.getAttribute('aria-label') || '') + (b.value || ''))
    );

    if (btns.length === 0) return { status: 'button_not_found_in_row' };
    if (btns.length > 1) return { status: 'ambiguous_buttons' };
    if (btns[0].disabled) return { status: 'button_disabled' };
    return { status: 'ok', btn: btns[0] };
  }

  function isLoggedOut(profile, doc) {
    const document = getDocument(doc);
    if (!document) return false;
    for (const sel of profile.logout_indicators || []) {
      try {
        if (document.querySelector(sel)) return true;
      } catch (_) {
        /* invalid selector */
      }
    }
    return false;
  }

  function checkCanary(profile, doc) {
    const document = getDocument(doc);
    if (!document) return { canary_ok: false, logged_in: false };
    const loggedIn = !isLoggedOut(profile, document);
    let canaryOk = false;
    const sel = profile.order_list_canary_selector;
    if (sel) {
      try {
        canaryOk = Boolean(document.querySelector(sel));
      } catch (_) {
        canaryOk = false;
      }
    }
    return { canary_ok: canaryOk, logged_in: loggedIn };
  }

  function scrapePendingOrders(profile, doc) {
    const document = getDocument(doc);
    if (!document) return [];

    const selector = rowSelector(profile);
    if (!selector) return [];

    const amountRe = /[\d,]+\.\d{2}/;
    const rows = [...document.querySelectorAll(selector)];
    const orders = [];

    for (const row of rows) {
      const text = row.textContent || '';
      const amountMatch = text.match(amountRe);
      if (!amountMatch) continue;

      const refCandidates = deepFindByText(row, normalize(text.replace(amountMatch[0], '')));
      let ref = null;
      for (const hit of refCandidates.length ? refCandidates : [row]) {
        const normalized = normalize(hit.textContent || '');
        if (normalized.length >= 4) {
          ref = (hit.textContent || '').trim().split(/\s+/)[0];
          break;
        }
      }

      const cells = [...row.querySelectorAll('td')];
      if (!ref && cells.length > 0) ref = (cells[0].textContent || '').trim();

      if (ref) {
        orders.push({
          ref: ref.replace(/\s+/g, ' ').trim(),
          amount: amountMatch[0],
        });
      }
    }
    return orders;
  }

  function outlineButton(btn, doc) {
    const document = getDocument(doc);
    if (!btn || !document) return;
    btn.style.outline = '3px solid #e53935';
    btn.style.outlineOffset = '2px';
    btn.setAttribute('data-clipsync-dry-run', '1');
  }

  /** Prefer a real clickable ancestor — eye icons are often <svg>/<i> inside <a>/<button>. */
  function clickableTarget(el) {
    if (!el || !el.closest) return el;
    return (
      el.closest(
        "button, a, a[role='button'], [role='button'], [onclick], input[type='button'], input[type='submit'], .el-button"
      ) || el
    );
  }

  function dispatchClick(el) {
    const target = clickableTarget(el);
    if (!target) return false;
    // Prefer a single native click — dispatching MouseEvent AND .click() toggles twice
    // (breaks custom dropdowns). MessageBox dismiss uses forceClick separately.
    try {
      target.click();
      return true;
    } catch (_) {
      /* fall through */
    }
    try {
      const view = target.ownerDocument && target.ownerDocument.defaultView;
      if (view && view.MouseEvent) {
        target.dispatchEvent(
          new view.MouseEvent('click', { bubbles: true, cancelable: true, view, buttons: 1 })
        );
        return true;
      }
    } catch (_) {
      return false;
    }
    return false;
  }

  /** Stronger click for stubborn Element UI / Vue MessageBox buttons. */
  function forceClick(el) {
    const target = clickableTarget(el);
    if (!target) return false;
    try {
      const view = target.ownerDocument && target.ownerDocument.defaultView;
      if (view && view.MouseEvent) {
        const rect = target.getBoundingClientRect ? target.getBoundingClientRect() : null;
        const cx = rect ? rect.left + rect.width / 2 : 0;
        const cy = rect ? rect.top + rect.height / 2 : 0;
        const base = {
          bubbles: true,
          cancelable: true,
          view,
          buttons: 1,
          clientX: cx,
          clientY: cy,
        };
        // Pointer/mouse down-up help Vue listeners; a single .click() fires the action once.
        target.dispatchEvent(new view.MouseEvent('pointerdown', base));
        target.dispatchEvent(new view.MouseEvent('mousedown', base));
        target.dispatchEvent(new view.MouseEvent('pointerup', base));
        target.dispatchEvent(new view.MouseEvent('mouseup', base));
      }
    } catch (_) {
      /* ignore */
    }
    try {
      target.click();
      return true;
    } catch (_) {
      return false;
    }
  }

  // Content scripts run in an ISOLATED world, so `window.Swal` here is NOT the
  // page's Swal. Reaching the page's real Swal.clickConfirm() requires running in
  // the MAIN world. Injecting an inline <script> tag is blocked by strict CSP
  // (Jinbao: "Executing inline script violates Content Security Policy"), so the
  // content script registers a CSP-safe clicker that asks the background service
  // worker to run chrome.scripting.executeScript({ world: 'MAIN', func }).
  let mainWorldClicker = null;

  /** content-script.js registers a fn that triggers a MAIN-world Swal click. */
  function setMainWorldClicker(fn) {
    mainWorldClicker = typeof fn === 'function' ? fn : null;
  }

  /**
   * Ask the (CSP-safe) MAIN-world clicker to press SweetAlert2's confirm button.
   * No-op when no clicker is registered (e.g. jsdom tests) — callers still fall
   * back to the isolated-world Swal API + a direct `.click()`, which is the path
   * that is confirmed working in the extension.
   */
  function injectMainWorldSwalClick() {
    try {
      if (typeof mainWorldClicker === 'function') {
        mainWorldClicker();
        return true;
      }
    } catch (_) {
      /* ignore */
    }
    return false;
  }

  /**
   * Dismiss a success/confirm dialog (SweetAlert2 on Jinbao, Element UI, plain popups).
   * SweetAlert2 success is closed via MAIN-world injection + native click, and is
   * considered done ONLY when `.swal2-container` is gone from the document.
   * Never returns already_gone / ok while `.swal2-container` still exists.
   */
  async function dismissMessageBox(step, context, doc) {
    const document = getDocument(doc);
    if (!document || !document.body) {
      return { ok: false, reason: 'dismiss_no_document' };
    }
    const win = document.defaultView || (typeof window !== 'undefined' ? window : null);
    const btnNeedles = String(step.match_text || 'ตกลง|OK|Ok|ok')
      .split('|')
      .map((s) => String(s).trim())
      .filter(Boolean);
    const successHints = String(
      step.success_text || 'บันทึก รายการถอน สำเร็จ|รายการถอน สำเร็จ|ปิดงานสำเร็จ'
    )
      .split('|')
      .map((s) => String(s).trim())
      .filter(Boolean);
    const timeout = step.timeout_ms || 10000;
    const start = Date.now();
    // Requirement: visible even when DevTools filter = Errors only (warn/log hidden).
    const warn = (...args) => {
      try {
        if (typeof console !== 'undefined' && console.error) {
          console.error('[ClipSync dismiss]', ...args);
        }
      } catch (_) {
        /* ignore */
      }
      // NOTE: We used to also stamp the PAGE-world console by appending an inline
      // <script>, but strict CSP (Jinbao) blocks inline scripts. Use the on-page
      // HUD below instead — it needs no script execution and cannot violate CSP.
      // On-page HUD so user sees activity without opening the right console filter.
      try {
        let hud = document.getElementById('clipsync-dismiss-hud');
        if (!hud) {
          hud = document.createElement('div');
          hud.id = 'clipsync-dismiss-hud';
          hud.setAttribute(
            'style',
            'position:fixed;left:8px;bottom:8px;z-index:2147483647;max-width:70vw;' +
              'background:#111;color:#0f0;font:12px/1.35 monospace;padding:8px 10px;' +
              'border:1px solid #0f0;border-radius:6px;opacity:0.92;pointer-events:none;'
          );
          document.body.appendChild(hud);
        }
        hud.textContent = '[ClipSync dismiss] ' + args.map((a) => String(a)).join(' ').slice(0, 240);
      } catch (_) {
        /* ignore */
      }
    };

    const restoreDialogs = installAutoAcceptDialogs(win);
    warn('start', { timeout, needles: btnNeedles, href: (win && win.location && win.location.href) || '' });

    const buttonLabel = (b) =>
      String(b.textContent || b.value || '')
        .replace(/\s+/g, ' ')
        .trim();

    const isOkLabel = (t) => btnNeedles.some((n) => t.toLowerCase() === String(n).toLowerCase());

    const styleDisplayNone = (el) => {
      if (!el) return true;
      const styleAttr = (el.getAttribute && el.getAttribute('style')) || '';
      if (/display\s*:\s*none/i.test(styleAttr)) return true;
      try {
        const cs = win && win.getComputedStyle && win.getComputedStyle(el);
        if (cs && cs.display === 'none') return true;
      } catch (_) {
        /* ignore */
      }
      return false;
    };

    // DOM PRESENCE, not visibility — SweetAlert2 keeps `.swal2-container` in the
    // document until it is fully closed; success == this node is gone.
    const swalContainerInDom = () => document.querySelector('.swal2-container');
    const swalConfirmBtn = () => document.querySelector('button.swal2-confirm');

    const successStillVisible = () => {
      const hay = document.body ? document.body.textContent || '' : '';
      return successHints.some((h) => h && hay.includes(h));
    };

    /** Rank NON-swal OK buttons — BootstrapVue save-confirm / Element UI MessageBox. */
    const findOkButtons = () => {
      const scored = [];
      const all = [...document.querySelectorAll('button, [role="button"], a.el-button, input[type="button"]')].filter(
        (b) => isVisible(b) && !styleDisplayNone(b)
      );
      for (const b of all) {
        const t = buttonLabel(b);
        if (!t || t.length > 24) continue;
        if (!isOkLabel(t)) continue;
        let score = 100;
        const root =
          b.closest(
            '.swal2-popup, .swal2-container, .modal, .modal-content, [role="dialog"], .el-message-box, .el-message-box__wrapper, .el-overlay-message-box, [role="alertdialog"], [class*="message-box"]'
          ) || b.parentElement;
        const around = (root && root.textContent) || '';
        if (b.classList && b.classList.contains('swal2-confirm')) score += 5000;
        if (successHints.some((h) => h && around.includes(h))) score += 500;
        if (/สำเร็จ/.test(around) && around.length < 400) score += 200;
        // BootstrapVue save-confirm: "ยืนยันการถอนรายการ" / "คุณแน่ใจใช่ไหมที่จะบันทึก"
        if (/ยืนยันการถอน|คุณแน่ใจ/.test(around)) score += 4000;
        if (b.classList && (b.classList.contains('el-button--primary') || b.classList.contains('btn-primary'))) {
          score += 20;
        }
        if (
          around.includes('โอนเงินทางบัญชี') &&
          around.includes('ชื่อธนาคาร') &&
          !successHints.some((h) => h && around.includes(h))
        ) {
          score -= 400;
        }
        scored.push({ btn: b, score, root: root || b, via: 'label' });
      }
      scored.sort((a, b) => b.score - a.score);
      return scored;
    };

    const findSaveConfirmModal = () => {
      const nodes = [
        ...document.querySelectorAll(
          '.modal.show, .modal[style*="display: block"], [id*="modal-withdraw"], [class*="modal"]'
        ),
      ];
      return (
        nodes.find(
          (n) =>
            !styleDisplayNone(n) &&
            isVisible(n) &&
            /ยืนยันการถอน|คุณแน่ใจใช่ไหมที่จะบันทึก/.test(n.textContent || '')
        ) || null
      );
    };

    const dialogStillOpen = (btn, root) => {
      if (swalContainerInDom()) return true;
      if (findSaveConfirmModal()) return true;
      if (btn && document.body.contains(btn) && isVisible(btn) && !styleDisplayNone(btn)) return true;
      if (root && document.body.contains(root) && isVisible(root) && !styleDisplayNone(root)) return true;
      return false;
    };

    // Isolated-world Swal is usually NOT the page's Swal, but call it anyway — it is a
    // harmless no-op in the extension and lets jsdom tests exercise the API path.
    const trySwalApi = () => {
      try {
        const Swal = (win && (win.Swal || win.swal || win.Sweetalert2)) || null;
        if (Swal && typeof Swal.clickConfirm === 'function') {
          warn('Swal.clickConfirm()');
          Swal.clickConfirm();
          return true;
        }
      } catch (err) {
        warn('Swal API error', String(err && err.message ? err.message : err));
      }
      return false;
    };

    // --- SweetAlert2 path: MAIN-world inject + native click, done when container gone.
    const dismissSwal = async () => {
      while (Date.now() - start < timeout) {
        const container = swalContainerInDom();
        if (!container) {
          warn('swal container gone — done');
          return { ok: true, dismissed: true, via: 'swal2' };
        }
        const btn = swalConfirmBtn();
        warn('swal poll', {
          ms: Date.now() - start,
          hasContainer: true,
          hasConfirm: Boolean(btn),
          confirmText: btn ? buttonLabel(btn) : '',
          confirmCls: btn ? (btn.className || '').toString().slice(0, 80) : '',
        });

        // 1) Reach the page's real Swal in the MAIN world (isolated world can't).
        injectMainWorldSwalClick();
        // 1b) Also try the isolated-world Swal API (no-op in the extension; used in tests).
        trySwalApi();

        // 2) Also click the button from the content script (works in jsdom + as a fallback).
        if (btn) {
          try {
            if (typeof btn.focus === 'function') btn.focus();
          } catch (_) {
            /* ignore */
          }
          warn('swal click', { text: buttonLabel(btn) });
          try {
            btn.click();
          } catch (_) {
            forceClick(btn);
          }
          try {
            if (win && win.KeyboardEvent) {
              const opts = { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13 };
              btn.dispatchEvent(new win.KeyboardEvent('keydown', opts));
              (document.activeElement || btn).dispatchEvent(new win.KeyboardEvent('keydown', opts));
            }
          } catch (_) {
            /* ignore */
          }
        }

        await sleep(150);
        // Never report success while the container is still present.
        if (!swalContainerInDom()) {
          warn('swal container gone after click — done');
          return { ok: true, dismissed: true, via: 'swal2' };
        }
      }
      warn('swal timeout — container still present', { lastReason: 'dismiss_dialog_still_open' });
      return { ok: false, reason: 'dismiss_dialog_still_open' };
    };

    let lastReason = 'dismiss_dialog_not_found';
    let sawDialog = false;
    try {
      while (Date.now() - start < timeout) {
        // SweetAlert2 always wins — handle it exclusively via the MAIN-world path.
        if (swalContainerInDom()) {
          sawDialog = true;
          return await dismissSwal();
        }

        const cands = findOkButtons();
        if (cands.length) sawDialog = true;

        warn('poll', {
          ms: Date.now() - start,
          swal: false,
          candidates: cands.map((c) => ({
            via: c.via,
            score: c.score,
            text: buttonLabel(c.btn),
            cls: (c.btn.className || '').toString().slice(0, 80),
          })),
          successText: successStillVisible(),
        });

        if (!cands.length) {
          // Never already_gone while a Swal is (or might still be) around, or before
          // we ever saw a dialog (Jinbao: toast text can flicker then Swal mounts late).
          if (sawDialog && !successStillVisible() && !swalContainerInDom()) {
            warn('done already_gone after prior dialog');
            return { ok: true, dismissed: true, already_gone: true };
          }
          lastReason = successStillVisible() ? 'waiting_for_swal_or_ok' : 'dismiss_dialog_not_found';
          await sleep(120);
          continue;
        }

        const { btn, root, via } = cands[0];
        warn('click', { via, text: buttonLabel(btn), cls: (btn.className || '').toString() });
        try {
          if (typeof btn.focus === 'function') btn.focus();
        } catch (_) {
          /* ignore */
        }
        try {
          btn.click();
        } catch (_) {
          forceClick(btn);
        }
        try {
          if (win && win.KeyboardEvent) {
            const opts = { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13 };
            btn.dispatchEvent(new win.KeyboardEvent('keydown', opts));
            (document.activeElement || btn).dispatchEvent(new win.KeyboardEvent('keydown', opts));
          }
        } catch (_) {
          /* ignore */
        }

        const until = Date.now() + 3500;
        while (Date.now() < until) {
          // A SweetAlert2 popup may mount right after this click — switch to swal path.
          if (swalContainerInDom()) {
            warn('swal appeared after click — switching to swal path');
            return await dismissSwal();
          }
          if (!dialogStillOpen(btn, root)) {
            warn('closed ok', { via });
            return { ok: true, dismissed: true, via };
          }
          await sleep(100);
        }
        lastReason = 'dismiss_dialog_still_open';
        warn('still open after click, retry');
        await sleep(150);
      }
      // Final guard: never report success while a Swal container remains.
      if (swalContainerInDom()) {
        warn('timeout — swal container still present');
        return { ok: false, reason: 'dismiss_dialog_still_open' };
      }
      warn('timeout', { lastReason, sawDialog });
      return { ok: false, reason: lastReason };
    } finally {
      restoreDialogs();
    }
  }

  /** Auto-accept page alert/confirm during dismiss so native dialogs cannot block the click path. */
  function installAutoAcceptDialogs(win) {
    if (!win) return () => {};
    const originals = {};
    try {
      originals.alert = win.alert;
      originals.confirm = win.confirm;
      originals.prompt = win.prompt;
      win.alert = function autoAlert(msg) {
        try {
          console.log('[ClipSync dismiss] auto-alert', msg);
        } catch (_) {
          /* ignore */
        }
        return undefined;
      };
      win.confirm = function autoConfirm(msg) {
        try {
          console.log('[ClipSync dismiss] auto-confirm', msg);
        } catch (_) {
          /* ignore */
        }
        return true;
      };
      win.prompt = function autoPrompt(msg, def) {
        return def != null ? def : '';
      };
    } catch (_) {
      return () => {};
    }
    return () => {
      try {
        if (originals.alert) win.alert = originals.alert;
        if (originals.confirm) win.confirm = originals.confirm;
        if (originals.prompt) win.prompt = originals.prompt;
      } catch (_) {
        /* ignore */
      }
    };
  }

  function resolvePath(obj, path) {
    if (!path || !obj) return undefined;
    return String(path)
      .split('.')
      .reduce((acc, key) => (acc == null ? undefined : acc[key]), obj);
  }

  function resolveUrlTemplate(template, context) {
    if (!template) return '';
    const today = new Date().toISOString().slice(0, 10);
    return template.replace(/\{today\}/g, today).replace(/\{(\w+)\}/g, (_, key) => {
      const val = resolvePath(context, key);
      return val != null ? String(val) : '';
    });
  }

  function isApproveStub(urlTemplate) {
    if (!urlTemplate) return true;
    const upper = String(urlTemplate).toUpperCase();
    return upper.includes('TODO') || upper.includes('(FROM RECON') || upper.includes('STUB');
  }

  async function apiListPending(profile, fetchFn, context) {
    const api = profile.api;
    if (!api || !api.enabled) return { status: 'disabled', orders: [] };

    const listCfg = api.list_pending;
    if (!listCfg || !listCfg.url_template) {
      return { status: 'error', reason: 'missing_list_pending_config', orders: [] };
    }

    const url = resolveUrlTemplate(listCfg.url_template, context || {});
    const method = (listCfg.method || 'GET').toUpperCase();
    const fetch = fetchFn || globalThis.fetch;

    try {
      const resp = await fetch(url, { method, credentials: 'include' });
      if (resp.status === 401 || resp.status === 403) {
        return { status: 'session_expired', orders: [] };
      }
      if (!resp.ok) {
        return { status: 'error', reason: `http_${resp.status}`, orders: [] };
      }
      const data = await resp.json();
      const items = Array.isArray(data) ? data : data.items || data.data || data.results || [];
      const map = listCfg.fields_map || {};
      const orders = items.map((item) => ({
        order_id: item[map.order_id || 'id'],
        amount: item[map.amount || 'amount'],
        account: item[map.account || 'member_bank_account'],
        bank: item[map.bank || 'bank_information_id'],
        name: item[map.name || 'username'],
      }));
      return { status: 'ok', orders };
    } catch (err) {
      return { status: 'error', reason: String(err && err.message ? err.message : err), orders: [] };
    }
  }

  async function apiApprove(profile, orderId, payloadContext, fetchFn) {
    const api = profile.api;
    if (!api || !api.enabled) return { status: 'disabled' };

    const approveCfg = api.approve;
    if (!approveCfg || !approveCfg.url_template) {
      return { status: 'stub', reason: 'approve_endpoint_todo' };
    }
    if (isApproveStub(approveCfg.url_template)) {
      return { status: 'stub', reason: 'approve_endpoint_todo' };
    }

    const url = resolveUrlTemplate(approveCfg.url_template, { orderId, ...(payloadContext || {}) });
    const method = (approveCfg.method || 'POST').toUpperCase();
    const fetch = fetchFn || globalThis.fetch;
    const body = approveCfg.payload_template
      ? JSON.stringify(approveCfg.payload_template)
      : undefined;

    try {
      const resp = await fetch(url, {
        method,
        credentials: 'include',
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body,
      });
      if (resp.status === 401 || resp.status === 403) {
        return { status: 'session_expired' };
      }
      if (!resp.ok) return { status: 'error', reason: `http_${resp.status}` };
      return { status: 'ok', verified: true };
    } catch (err) {
      return { status: 'error', reason: String(err && err.message ? err.message : err) };
    }
  }

  function apiAdapter(profile, fetchFn) {
    return {
      listPending: (context) => apiListPending(profile, fetchFn, context),
      approve: (orderId, payloadContext) => apiApprove(profile, orderId, payloadContext, fetchFn),
    };
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function isVisible(el) {
    if (!el) return false;
    if (el.hidden) return false;
    const styleAttr = (el.getAttribute && el.getAttribute('style')) || '';
    if (/display\s*:\s*none/i.test(styleAttr) || /visibility\s*:\s*hidden/i.test(styleAttr)) {
      return false;
    }
    let cur = el;
    while (cur && cur.nodeType === 1) {
      if (cur.hidden) return false;
      const aria = cur.getAttribute && cur.getAttribute('aria-hidden');
      if (aria === 'true') return false;
      cur = cur.parentElement;
    }
    try {
      if (el.getClientRects && el.getClientRects().length > 0) return true;
    } catch (_) {
      /* ignore */
    }
    // jsdom often has no layout — treat as visible if not explicitly hidden.
    if (typeof el.offsetParent === 'undefined' || el.offsetParent === null) {
      return true;
    }
    return Boolean(el.offsetParent);
  }

  /**
   * Smallest visible container whose text includes scopeText and that holds a real form control.
   * Needed because some sites (BootstrapVue) render the close-job form in a plain .card, not a dialog.
   */
  function findScopeByText(document, scopeText) {
    if (!document || !scopeText) return null;
    let cands = [];
    try {
      cands = [
        ...document.querySelectorAll('.card, .modal, [role="dialog"], .el-dialog, form, section, div'),
      ].filter((el) => isVisible(el) && (el.textContent || '').includes(scopeText));
    } catch (_) {
      cands = [];
    }
    if (!cands.length) return null;
    // Prefer compact scopes that actually contain the close-job bank+account fields.
    const withForm = cands.filter((el) => el.querySelector('fieldset, select, .el-select'));
    const pool = withForm.length ? withForm : cands;
    pool.sort((a, b) => {
      const aForm =
        (a.textContent || '').includes('ชื่อธนาคาร') && (a.textContent || '').includes('หมายเลขบัญชี')
          ? 0
          : 1;
      const bForm =
        (b.textContent || '').includes('ชื่อธนาคาร') && (b.textContent || '').includes('หมายเลขบัญชี')
          ? 0
          : 1;
      if (aForm !== bForm) return aForm - bForm;
      return (a.textContent || '').length - (b.textContent || '').length;
    });
    return pool[0] || null;
  }

  function findScopeRoot(step, context, doc) {
    const document = getDocument(doc);
    if (step.scope === 'popup') {
      if (step.scope_text) {
        const byText = findScopeByText(document, step.scope_text);
        if (byText) return byText;
      }
      for (const hint of POPUP_SCOPE_HINTS) {
        let els = [];
        try {
          els = [...document.querySelectorAll(hint)];
        } catch (_) {
          els = [];
        }
        const visible = els.find((el) => isVisible(el));
        if (visible) return visible;
      }
      return document.body;
    }
    if (step.in_row || (step.target && step.target.in_row)) {
      return context.row || document.body;
    }
    return document.body;
  }

  function bankMatchNeedles(value) {
    const raw = String(value || '').trim();
    if (!raw) return [];
    const out = new Set([raw]);
    const upper = raw.toUpperCase();
    for (const [code, aliases] of Object.entries(BANK_ALIASES)) {
      if (upper === code || upper.includes(code) || aliases.some((a) => raw.includes(a) || upper.includes(String(a).toUpperCase()))) {
        aliases.forEach((a) => out.add(a));
        out.add(code);
      }
    }
    return [...out];
  }

  function optionTextMatches(text, value, step) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    if (!t || t.includes('กรุณาเลือก')) return false;
    if (step && step.match_pattern) {
      try {
        return new RegExp(step.match_pattern).test(t);
      } catch (_) {
        return false;
      }
    }
    if (value == null || String(value).trim() === '') return false;
    const v = String(value).replace(/\s+/g, ' ').trim();
    // Last4 / account fragment: match if option contains the digits.
    if (/^\d{4,}$/.test(v.replace(/\D/g, '')) && v.replace(/\D/g, '').length <= 6) {
      const digits = v.replace(/\D/g, '');
      return t.replace(/\D/g, '').endsWith(digits) || t.includes(digits);
    }
    if (t === v) return true;
    if (t.includes(v) || v.includes(t)) return true;
    return bankMatchNeedles(v).some((n) => t === n || t.includes(n));
  }

  function resolveSelectValue(step, context) {
    if (step.match_text && !step.value_from) return step.match_text;
    const paths = [];
    if (step.value_from) paths.push(step.value_from);
    if (step.value_from_fallbacks) paths.push(...step.value_from_fallbacks);
    const from = String(step.value_from || '');
    if (from.includes('bank')) {
      paths.push('slip.bank_name_th', 'slip.bank_name', 'slip.bank');
    }
    if (from.includes('account')) {
      // Payout-account field = which shop account SENT the money (slip "จาก"/sender).
      // Never fall back to receiver_* — that is the member account and is not in the
      // shop dropdown; picking it could select the wrong payout account.
      paths.push(
        'slip.sender_account_last4',
        'slip.senderAccountLast4',
        'slip.sender_account',
        'slip.from_account',
        'slip.account_number'
      );
    }
    for (const p of paths) {
      const v = resolvePath(context, p);
      if (v != null && String(v).trim() !== '') return v;
    }
    return step.match_text || null;
  }

  /**
   * Resolve a normalized masked-account template for a payout-account field, e.g.
   * "5840xxx518" (BBL) or "xxxxx0758x" (KBANK where the tail digit is hidden).
   * The slip's last-4 alone is unreliable across banks because each masks a
   * different position — the full template preserves every visible digit AND the
   * hidden positions, so we can build a position-aware matcher on the dropdown.
   * Only the payer/sender template is considered: on a payout the shop is always
   * the sender, and receiver_* is the member account (not in the shop dropdown).
   */
  function resolveMaskedTemplate(step, context) {
    const paths = [];
    if (step.value_from_masked) paths.push(step.value_from_masked);
    paths.push('slip.sender_account_masked', 'slip.senderAccountMasked');
    for (const p of paths) {
      const v = resolvePath(context, p);
      if (v != null && /[xX]/.test(String(v)) && String(v).trim() !== '') {
        return String(v).trim();
      }
    }
    return null;
  }

  /** Turn "5840xxx518" into /5840\d\d\d518$/ — masks become "any digit". */
  function maskTemplateToRegex(template) {
    const body = String(template)
      .replace(/[^0-9xX]/g, '')
      .split('')
      .map((c) => (c === 'x' || c === 'X' ? '\\d' : c))
      .join('');
    if (!body || !/\d/.test(body.replace(/\\d/g, ''))) {
      // Require at least one real (literal) digit so an all-mask template can't
      // match everything.
      return null;
    }
    try {
      return new RegExp(body + '$');
    } catch (_) {
      return null;
    }
  }

  /** Native <option>s whose account digits are consistent with the mask template. */
  function matchMaskedOptions(select, template) {
    const re = maskTemplateToRegex(template);
    if (!re) return [];
    return [...select.options].filter((o) => {
      const digits = String(o.textContent || o.value || '').replace(/\D/g, '');
      return digits.length >= 4 && re.test(digits);
    });
  }

  function pickBestOption(items, value, step) {
    const matched = items.filter((el) => optionTextMatches(el.textContent, value, step));
    if (!matched.length) return null;
    // Prefer exact full-name match, then longest label (more specific), first wins for duplicates.
    const exact = matched.filter((el) => String(el.textContent || '').trim() === String(value || '').trim());
    if (exact.length) return exact[0];
    matched.sort(
      (a, b) => String(b.textContent || '').trim().length - String(a.textContent || '').trim().length
    );
    return matched[0];
  }

  // Label selectors incl. BootstrapVue <legend> and el-form-item label.
  const FIELD_LABEL_SELECTOR =
    '.el-form-item__label, legend, .col-form-label, label, .control-label';
  const FIELD_CONTROL_SELECTOR =
    '.el-select, .el-select__wrapper, select, .custom-dropdown, input, textarea';

  function fieldLabelText(el) {
    const labelEl = el.querySelector(FIELD_LABEL_SELECTOR);
    return labelEl ? labelEl.textContent || '' : '';
  }

  /**
   * Score how well a label matches field_hint.
   * Exact "ชื่อธนาคาร" must beat page filters like "ชื่อธนาคารของสมาชิก".
   */
  function fieldLabelMatchScore(labelText, fieldHint) {
    const t = String(labelText || '').replace(/\s+/g, ' ').trim();
    const hint = String(fieldHint || '').replace(/\s+/g, ' ').trim();
    if (!t || !hint || t.length >= 80) return -1;
    if (t === hint) return 1000;
    // Label is exactly hint plus short suffix/prefix punctuation only.
    if (t.replace(/[:：\s]+$/g, '') === hint) return 950;
    if (!t.includes(hint)) return -1;
    // Longer labels that merely contain the hint (filters, member bank, etc.) lose.
    return Math.max(0, 400 - (t.length - hint.length) * 20);
  }

  /** Prefer real form rows that contain a control (avoid member-info display labels). */
  function findFieldContainer(root, fieldHint) {
    if (!root || !fieldHint) return null;
    // fieldset = BootstrapVue field; .form-group/.el-form-item = other frameworks.
    const formItems = [...root.querySelectorAll('fieldset, .el-form-item, .form-group')];
    let best = null;
    let bestScore = -1;
    for (const el of formItems) {
      const score = fieldLabelMatchScore(fieldLabelText(el), fieldHint);
      if (score < 0) continue;
      const hasControl = Boolean(el.querySelector(FIELD_CONTROL_SELECTOR));
      const ranked = score + (hasControl ? 50 : 0);
      if (ranked > bestScore) {
        bestScore = ranked;
        best = el;
      }
    }
    if (best) return best;

    // Fallback: generic label/div (same scoring).
    const labeled = [...root.querySelectorAll('label, div')];
    for (const el of labeled) {
      const score = fieldLabelMatchScore(fieldLabelText(el), fieldHint);
      if (score < 0) continue;
      if (score > bestScore) {
        bestScore = score;
        best = el;
      }
    }
    for (const el of labeled) {
      const own = (el.childNodes && el.childNodes[0] && el.childNodes[0].textContent) || '';
      const score = fieldLabelMatchScore(own, fieldHint);
      if (score < 0) continue;
      if (score > bestScore) {
        bestScore = score;
        best = el;
      }
    }
    return best;
  }

  /**
   * Visible Element UI / custom dropdown panels, leaf-only.
   * Do NOT include bare `.el-popper` — it nests `.el-select-dropdown` and doubles every option.
   */
  function leafVisibleDropdowns(doc) {
    const document = getDocument(doc);
    if (!document) return [];
    const found = [...document.querySelectorAll('.el-select-dropdown, .dropdown-menu')].filter((el) =>
      isVisible(el)
    );
    // Keep only leaves: drop a panel that contains another matched panel.
    return found.filter((el) => !found.some((other) => other !== el && el.contains(other)));
  }

  function collectVisibleSelectOptions(doc) {
    const scopes = leafVisibleDropdowns(doc);
    const items = [];
    for (const scope of scopes) {
      items.push(
        ...[
          ...scope.querySelectorAll(
            '.el-select-dropdown__item, [role="option"], .el-scrollbar__view li, li'
          ),
        ].filter((el) => isVisible(el) && !(el.textContent || '').includes('กรุณาเลือก'))
      );
    }
    return dedupeOptionsByText([...new Set(items)]);
  }

  /** Prefer the dropdown panel closest to the opened select trigger (avoids stale twin panels). */
  function collectOptionsForTrigger(trigger, doc) {
    const document = getDocument(doc);
    const panels = leafVisibleDropdowns(document);
    if (!panels.length) return [];
    let best = panels[0];
    if (trigger && panels.length > 1 && typeof trigger.getBoundingClientRect === 'function') {
      const tr = trigger.getBoundingClientRect();
      let bestScore = Infinity;
      for (const panel of panels) {
        try {
          const pr = panel.getBoundingClientRect();
          const dx = Math.abs(pr.left - tr.left);
          const dy = Math.abs(pr.top - tr.bottom);
          const score = dy * 2 + dx;
          if (score < bestScore) {
            bestScore = score;
            best = panel;
          }
        } catch (_) {
          /* ignore */
        }
      }
    }
    const items = [
      ...best.querySelectorAll(
        '.el-select-dropdown__item, [role="option"], .el-scrollbar__view li, li'
      ),
    ].filter((el) => isVisible(el) && !(el.textContent || '').includes('กรุณาเลือก'));
    return dedupeOptionsByText(items);
  }

  function dedupeOptionsByText(items) {
    const seen = new Set();
    const out = [];
    for (const el of items) {
      const key = String(el.textContent || '')
        .replace(/\s+/g, ' ')
        .trim();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(el);
    }
    return out;
  }

  function closeOpenSelectDropdowns(doc) {
    const document = getDocument(doc);
    if (!document) return;
    // Prefer Vue visible=false on every el-select (avoids Escape/click toggle races).
    try {
      const selects = [...document.querySelectorAll('.el-select, .el-select__wrapper')];
      for (const el of selects) {
        const vue = findVueInstance(el);
        if (vue && 'visible' in vue) {
          try {
            vue.visible = false;
          } catch (_) {
            /* ignore */
          }
        }
      }
    } catch (_) {
      /* ignore */
    }
    try {
      const view = document.defaultView;
      if (view && view.KeyboardEvent) {
        const opts = { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true, cancelable: true };
        document.dispatchEvent(new view.KeyboardEvent('keydown', opts));
      }
    } catch (_) {
      /* ignore */
    }
  }

  function findVueInstance(el) {
    let cur = el;
    for (let i = 0; cur && i < 12; i++) {
      if (cur.__vue__) return cur.__vue__;
      if (cur.__vueParentComponent && cur.__vueParentComponent.proxy) {
        return cur.__vueParentComponent.proxy;
      }
      cur = cur.parentElement;
    }
    return null;
  }

  function optionLabelOf(opt) {
    if (!opt) return '';
    return String(opt.currentLabel || opt.label || opt.value || '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  /** Open Element UI select once via Vue — never open twice (that appends twin option lists 4→8). */
  function openElementUiSelect(trigger) {
    const vue = findVueInstance(trigger);
    if (vue && 'visible' in vue) {
      try {
        vue.visible = false;
      } catch (_) {
        /* ignore */
      }
      try {
        vue.visible = true;
        return { ok: true, via: 'vue' };
      } catch (_) {
        /* fall through */
      }
    }
    const clickEl =
      (trigger &&
        trigger.querySelector &&
        trigger.querySelector('.el-input__inner, .el-select__wrapper, input, button, .dropdown-toggle')) ||
      trigger;
    // One open only — a second click toggles closed/re-mounts and duplicates banks.
    dispatchClick(clickEl);
    return { ok: true, via: 'click' };
  }

  /**
   * Apply option via Element UI Select's own options list (safe).
   * Do NOT pass random DOM __vue__ nodes — that can mutate/duplicate options (4 → 8).
   */
  function tryApplyElementUiOption(trigger, optionEl, label) {
    const want = String(label || (optionEl && optionEl.textContent) || '')
      .replace(/\s+/g, ' ')
      .trim();
    if (!want) return false;

    const selectVue = findVueInstance(trigger);
    if (!selectVue || typeof selectVue.handleOptionSelect !== 'function') return false;

    try {
      const opts = [...(selectVue.options || []), ...(selectVue.hoverOptions || [])];
      // De-dupe by label in case the site already doubled the list.
      const seen = new Set();
      const unique = [];
      for (const o of opts) {
        const l = optionLabelOf(o);
        if (!l || l.includes('กรุณาเลือก') || seen.has(l)) continue;
        seen.add(l);
        unique.push(o);
      }
      const match =
        unique.find((o) => optionLabelOf(o) === want) ||
        unique.find((o) => optionLabelOf(o).includes(want) || want.includes(optionLabelOf(o))) ||
        unique.find((o) => bankMatchNeedles(want).some((n) => optionLabelOf(o).includes(n)));
      if (!match) return false;
      selectVue.handleOptionSelect(match);
      if ('visible' in selectVue) selectVue.visible = false;
      return true;
    } catch (_) {
      return false;
    }
  }

  /** Deduped Element UI option objects from the Vue Select instance (placeholder removed). */
  function uniqueVueOptions(vue) {
    const opts = [...((vue && vue.options) || []), ...((vue && vue.hoverOptions) || [])];
    const seen = new Set();
    const out = [];
    for (const o of opts) {
      const l = optionLabelOf(o);
      if (!l || l.includes('กรุณาเลือก') || seen.has(l)) continue;
      seen.add(l);
      out.push(o);
    }
    return out;
  }

  function pickVueOption(list, value, step) {
    const matchStep =
      value == null && step.fallback_match_pattern
        ? { ...step, match_pattern: step.fallback_match_pattern }
        : step;
    const matched = list.filter((o) => optionTextMatches(optionLabelOf(o), value, matchStep));
    if (!matched.length) return null;
    const want = String(value || '').trim();
    const exact = matched.find((o) => optionLabelOf(o) === want);
    if (exact) return exact;
    matched.sort((a, b) => optionLabelOf(b).length - optionLabelOf(a).length);
    return matched[0];
  }

  /**
   * Element UI `.el-select` path — Vue instance ONLY. Never click/mousedown to open
   * (that remounts/appends the el-option list, e.g. 4 banks → 8 duplicates on Jinbao).
   * Selects while closed when options are already present; otherwise opens once (visible=true),
   * waits, selects, closes. No soft-reopen, no click fallback.
   */
  async function selectElSelectViaVue(trigger, value, step) {
    const vue = findVueInstance(trigger);
    if (!vue || typeof vue.handleOptionSelect !== 'function') {
      return { ok: false, reason: 'el_select_no_vue', field: step.field_hint };
    }

    // Start from a known-closed state so we never toggle-open a second time.
    try {
      if ('visible' in vue) vue.visible = false;
    } catch (_) {
      /* ignore */
    }

    let list = uniqueVueOptions(vue);
    let match = pickVueOption(list, value, step);
    let opened = false;

    if (!match) {
      // Options not populated yet — open exactly once to let Vue mount el-option children.
      try {
        if ('visible' in vue) {
          vue.visible = true;
          opened = true;
        }
      } catch (_) {
        /* ignore */
      }
      const timeout = step.timeout_ms || 4000;
      const start = Date.now();
      while (Date.now() - start < timeout) {
        list = uniqueVueOptions(vue);
        match = pickVueOption(list, value, step);
        if (match) break;
        await sleep(50);
      }
    }

    if (!match) {
      if (opened) {
        try {
          vue.visible = false;
        } catch (_) {
          /* ignore */
        }
      }
      return {
        ok: false,
        reason: 'option_not_found',
        field: step.field_hint,
        tried_value: value,
        option_count: list.length,
      };
    }

    const itemText = optionLabelOf(match);
    try {
      vue.handleOptionSelect(match);
      if ('visible' in vue) vue.visible = false;
    } catch (_) {
      return {
        ok: false,
        reason: 'option_not_applied',
        field: step.field_hint,
        tried_value: value,
        option_count: list.length,
      };
    }

    const verifyUntil = Date.now() + 3000;
    while (Date.now() < verifyUntil) {
      const shown = readSelectDisplayValue(trigger);
      if (displayMatchesSelection(shown, value, step, itemText)) {
        return { ok: true, matched: itemText, via_vue: true, option_count: list.length };
      }
      await sleep(50);
    }
    return {
      ok: false,
      reason: 'option_not_applied',
      field: step.field_hint,
      tried_value: value,
      shown: readSelectDisplayValue(trigger),
      option_count: list.length,
      hint: 'เลือกผ่าน Vue แล้วแต่ค่าไม่ติด — อย่าเปิด dropdown ซ้ำ',
    };
  }

  function clickOptionOnce(optionEl) {
    if (!optionEl) return false;
    // Single native click only — mousedown+click combo double-fires on some Element UI builds.
    try {
      optionEl.click();
      return true;
    } catch (_) {
      return false;
    }
  }

  /** Displayed value of an Element UI / native-ish select — never full trigger textContent. */
  function readSelectDisplayValue(trigger) {
    if (!trigger) return '';
    const input =
      trigger.querySelector &&
      trigger.querySelector('.el-input__inner, input.el-select__input, input:not([type="hidden"])');
    if (input) {
      const v = String(input.value || '').trim();
      if (v) return v;
      const ph = String(input.getAttribute('placeholder') || '').trim();
      if (ph && ph.includes('กรุณาเลือก')) return '';
    }
    const selected =
      trigger.querySelector &&
      trigger.querySelector('.el-select__selected-item span, .el-select__selected-item');
    if (selected) {
      const t = String(selected.textContent || '').trim();
      if (t && !t.includes('กรุณาเลือก')) return t;
    }
    // Custom / fixture dropdowns use a toggle button as the display.
    const toggle =
      trigger.querySelector && trigger.querySelector('.dropdown-toggle, button.dropdown-toggle');
    if (toggle) {
      const t = String(toggle.textContent || '').trim();
      if (t && !/กรุณาเลือก|เลือกธนาคาร|select/i.test(t)) return t;
      return '';
    }
    const placeholder =
      trigger.querySelector && trigger.querySelector('.el-select__placeholder, .el-input__inner');
    if (placeholder) {
      const t = String(placeholder.textContent || placeholder.value || '').trim();
      if (t && !t.includes('กรุณาเลือก')) return t;
      return '';
    }
    return '';
  }

  function displayMatchesSelection(shown, value, step, itemText) {
    const text = String(shown || '').replace(/\s+/g, ' ').trim();
    if (!text || text.includes('กรุณาเลือก')) return false;
    const needle = value != null ? String(value).trim() : '';
    if (needle && (text.includes(needle) || bankMatchNeedles(needle).some((n) => text.includes(n)))) {
      return true;
    }
    if (!needle && step && step.match_pattern) {
      try {
        if (new RegExp(step.match_pattern).test(text.replace(/\s+/g, ''))) return true;
      } catch (_) {
        /* ignore */
      }
      if (/\d{8,}/.test(text.replace(/\D/g, ''))) return true;
    }
    const slice = String(itemText || '').trim().slice(0, 8);
    if (slice && text.includes(slice)) return true;
    return false;
  }

  function textMatches(el, pattern) {
    if (!el || !pattern) return false;
    const text = (el.textContent || '') + (el.getAttribute('aria-label') || '') + (el.value || '');
    if (pattern.includes('|')) return new RegExp(pattern, 'i').test(text);
    return text.includes(pattern);
  }

  function queryByHints(root, hints) {
    if (!root || !hints || !hints.length) return [];
    const selector = hints.join(',');
    try {
      return [...root.querySelectorAll(selector)];
    } catch (_) {
      return [];
    }
  }

  function pickTarget(candidates, step) {
    let list = candidates;
    if (step.match_text) {
      list = list.filter((el) => textMatches(el, step.match_text));
    }
    if (list.length === 0) return null;
    if (list.length === 1) return list[0];
    if (step.nth_fallback === 'last' || (step.target && step.target.nth_fallback === 'last')) {
      return list[list.length - 1];
    }
    return null;
  }

  /** Prefer a real, visible primary control when several nodes share the same label. */
  function pickClickTarget(candidates, step) {
    let list = (candidates || []).filter(Boolean);
    if (step && step.match_text) {
      list = list.filter((el) => textMatches(el, step.match_text));
    }
    if (!list.length) return null;

    const visible = list.filter((el) => {
      try {
        return typeof isVisible === 'function' ? isVisible(el) : true;
      } catch (_) {
        return true;
      }
    });
    if (visible.length) list = visible;

    const pattern = String((step && step.match_text) || '');
    const alts = pattern.includes('|') ? pattern.split('|') : [pattern];
    const exact = list.filter((el) => {
      const t = String(el.textContent || el.value || '')
        .replace(/\s+/g, ' ')
        .trim();
      return alts.some((a) => t.toLowerCase() === String(a).trim().toLowerCase());
    });
    if (exact.length) list = exact;

    const buttons = list.filter((el) => {
      const tag = (el.tagName || '').toLowerCase();
      return (
        tag === 'button' ||
        tag === 'a' ||
        (tag === 'input' && /submit|button/i.test(el.type || '')) ||
        el.getAttribute('role') === 'button'
      );
    });
    if (buttons.length) list = buttons;

    if (list.length === 1) return list[0];
    if (step && (step.nth_fallback === 'last' || (step.target && step.target.nth_fallback === 'last'))) {
      return list[list.length - 1];
    }
    // Footer Save/Confirm: when still ambiguous, prefer the last visible button
    // (Jinbao puts ยกเลิก then บันทึก).
    return list[list.length - 1];
  }

  function findStepTarget(step, context, doc) {
    const root = findScopeRoot(step, context, doc);
    const hints = step.selector_hints || (step.target && step.target.selector_hints) || [];
    let candidates = queryByHints(root, hints);

    if (step.field_hint) {
      const labeled = [...root.querySelectorAll('label, .field, div')].filter((el) =>
        (el.textContent || '').includes(step.field_hint)
      );
      for (const label of labeled) {
        const input = label.querySelector('input, select, textarea, button, .custom-dropdown');
        if (input) candidates.push(input);
      }
    }

    const textSearch = () => {
      const interactive = [
        ...root.querySelectorAll("button, a, a[role='button'], input[type='submit'], [onclick], li, option"),
      ].filter((el) => textMatches(el, step.match_text));
      if (interactive.length > 0) return interactive;
      return [...root.querySelectorAll('span, div')].filter((el) => textMatches(el, step.match_text));
    };

    if (step.match_text) {
      if (candidates.length === 0) {
        candidates = textSearch();
      } else {
        // Hints may match many buttons — keep only those whose label matches.
        const narrowed = candidates.filter((el) => textMatches(el, step.match_text));
        candidates = narrowed.length ? narrowed : textSearch();
      }
    }

    // Click steps must never return null just because >1 node matched the label
    // (common: page header + modal footer both contain "บันทึก").
    if (step.action === 'click' || (!step.action && step.match_text)) {
      return pickClickTarget(candidates, step);
    }
    return pickTarget(candidates, step);
  }

  function domEvent(node, type) {
    const win = node && node.ownerDocument ? node.ownerDocument.defaultView : null;
    if (win && win.Event) return new win.Event(type, { bubbles: true });
    return { type, bubbles: true };
  }

  /** Set <select>.value through the native setter so Vue/React v-model picks it up, then fire events. */
  function setNativeSelectValue(select, val) {
    try {
      const proto =
        (typeof HTMLSelectElement !== 'undefined' && HTMLSelectElement.prototype) ||
        Object.getPrototypeOf(select);
      const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(select, val);
      else select.value = val;
    } catch (_) {
      select.value = val;
    }
    // BootstrapVue / Vue listen on input+change; some builds want bubbling UIEvent.
    select.dispatchEvent(domEvent(select, 'input'));
    select.dispatchEvent(domEvent(select, 'change'));
  }

  function usableNativeOptions(select) {
    if (!select || !select.options) return [];
    return [...select.options].filter((o) => {
      if (o.disabled) return false;
      const t = (o.textContent || '').trim();
      return t && !t.includes('กรุณาเลือก') && !t.includes('ทั้งหมด');
    });
  }

  function isBankFieldHint(hint) {
    const h = String(hint || '');
    return h.includes('ธนาคาร') && !h.includes('สถานะ');
  }

  function isAccountFieldHint(hint) {
    return String(hint || '').includes('หมายเลขบัญชี') || String(hint || '').includes('เลขบัญชี');
  }

  /** True when a <select> is still on its empty/disabled placeholder (e.g. --- กรุณาเลือก ---). */
  function isPlaceholderSelectValue(select) {
    if (!select) return true;
    const val = String(select.value || '').trim();
    if (!val) return true;
    const opts = select.selectedOptions
      ? [...select.selectedOptions]
      : select.options
      ? [select.options[select.selectedIndex]]
      : [];
    const opt = opts[0];
    if (!opt) return false;
    if (opt.disabled) return true;
    return (opt.textContent || '').includes('กรุณาเลือก');
  }

  function matchNativeOptionsAll(select, value, step) {
    // Position-aware mask match wins when the slip carried a masked template —
    // it uses every visible digit (not just the last 4), so it works even when
    // the bank hides the tail (KBANK) or shows only 3 digits (BBL).
    if (step._masked_template) {
      const maskedMatches = matchMaskedOptions(select, step._masked_template);
      if (maskedMatches.length) return maskedMatches;
      // Nothing matched the template → fall through to the last-4 heuristic.
    }
    let opts = [...select.options].filter((o) =>
      optionTextMatches(o.textContent || o.value, value, step)
    );
    if (!opts.length && step.fallback_match_pattern) {
      opts = [...select.options].filter((o) =>
        optionTextMatches(o.textContent || o.value, null, {
          ...step,
          match_pattern: step.fallback_match_pattern,
        })
      );
    }
    return opts;
  }

  function matchNativeOption(select, value, step) {
    return matchNativeOptionsAll(select, value, step)[0] || null;
  }

  /**
   * When several account <option>s matched the needle, decide the SAFE pick.
   * Order (softened per user request — do not block when the account is knowable):
   *   1) position-aware mask template matches exactly one option → pick it;
   *   2) the slip last-4 matches exactly one option's last-4 → pick it;
   *   3) 2+ options share the EXACT same last-4 as the slip needle → refuse
   *      (account_ambiguous — genuinely unsafe to guess);
   *   4) otherwise the matches came from a broad fallback (e.g. "all 8+ digit
   *      options"): NEVER call that account_ambiguous — report option_not_found
   *      when a needle existed, or missing_select_value when none did.
   */
  function disambiguateAccountOptions(select, matches, value, step) {
    const digitsOf = (o) =>
      String((o && (o.textContent || o.value)) || '').replace(/\D/g, '');

    let pool = matches;

    // (1) Mask template wins — it uses every visible digit, so it beats a bare last-4.
    if (step && step._masked_template) {
      const masked = matchMaskedOptions(select, step._masked_template);
      if (masked.length === 1) return { option: masked[0] };
      if (masked.length > 1) pool = masked; // narrow, then disambiguate by last-4 below
    }

    // (2)/(3) Slip last-4.
    const needleLast4 = String(value == null ? '' : value)
      .replace(/\D/g, '')
      .slice(-4);
    if (needleLast4.length === 4) {
      const byLast4 = pool.filter((o) => digitsOf(o).slice(-4) === needleLast4);
      if (byLast4.length === 1) return { option: byLast4[0] };
      if (byLast4.length > 1) {
        return {
          reason: 'account_ambiguous',
          hint: 'มีหลายบัญชีลงท้าย ' + needleLast4 + ' เหมือนกัน — เลือกเองเพื่อความปลอดภัย',
        };
      }
      // byLast4.length === 0 → the needle did not pin any option (substring/fallback hits).
    }

    // (4) Broad fallback matched many options — do NOT treat as account_ambiguous.
    return {
      reason: needleLast4.length === 4 ? 'option_not_found' : 'missing_select_value',
      hint: 'ต้องมีเลขบัญชี/มาสก์จากสลิปเพื่อเลือกบัญชีให้ถูกใบ (fallback จับได้หลายบัญชี)',
    };
  }

  /**
   * Native <select> apply with polling — the account select is disabled + empty until the
   * bank change event repopulates it, so we wait for the real option to appear.
   */
  async function applyNativeSelect(select, value, step, root) {
    const isAccount = isAccountFieldHint(step.field_hint);
    if (isAccount) {
      const bankBox =
        root && (findFieldContainer(root, 'ชื่อธนาคาร') || findFieldContainer(root, 'ธนาคาร'));
      const bankSelect = bankBox && bankBox.querySelector('select');
      // Missing bank control OR still on placeholder → do not guess an account.
      if (!bankSelect || isPlaceholderSelectValue(bankSelect)) {
        return {
          ok: false,
          reason: 'bank_not_selected',
          field: step.field_hint,
          hint: 'ธนาคารยังไม่ถูกเลือก — ช่องหมายเลขบัญชีจะเปิดหลังเลือกธนาคารสำเร็จ',
        };
      }
    }

    const timeout = step.timeout_ms || 6000;
    const start = Date.now();
    let matches = matchNativeOptionsAll(select, value, step);
    while (!matches.length && Date.now() - start < timeout) {
      await sleep(120);
      matches = matchNativeOptionsAll(select, value, step);
    }
    if (!matches.length) {
      return {
        ok: false,
        reason: 'option_not_found',
        field: step.field_hint,
        tried_value: value,
        option_count: select.options.length,
        disabled: Boolean(select.disabled),
      };
    }
    // Safety for account matching: prefer a unique last-4 / mask match. Only refuse
    // (account_ambiguous) when 2+ options share the EXACT same last-4 as the slip
    // needle. A broad fallback that matched many accounts is NOT ambiguous — it just
    // means we lack a specific needle (option_not_found / missing_select_value).
    if (isAccount && matches.length > 1) {
      const decision = disambiguateAccountOptions(select, matches, value, step);
      if (decision.option) {
        matches = [decision.option];
      } else {
        return {
          ok: false,
          reason: decision.reason,
          field: step.field_hint,
          tried_value: value,
          option_count: matches.length,
          hint: decision.hint,
        };
      }
    }
    const opt = matches[0];
    const want = opt.value;

    // Apply + require the value to stay put briefly (Vue often snaps back on next tick
    // if it never received the change into component state).
    let stableMs = 0;
    const verifyUntil = Date.now() + Math.max(1500, Math.min(timeout, 4000));
    while (Date.now() < verifyUntil) {
      if (String(select.value) !== String(want) || isPlaceholderSelectValue(select)) {
        setNativeSelectValue(select, want);
        stableMs = 0;
      } else {
        stableMs += 50;
        if (stableMs >= 300) break;
      }
      await sleep(50);
    }
    if (String(select.value) !== String(want) || isPlaceholderSelectValue(select)) {
      return {
        ok: false,
        reason: 'option_not_applied',
        field: step.field_hint,
        tried_value: value,
        shown: select.value,
        hint: 'ตั้งค่า select แล้วแต่ค่าไม่ติด — ตรวจ event binding ของหน้าเว็บ',
      };
    }

    // Bank field must unlock/populate หมายเลขบัญชี. If Vue ignored the change, the
    // native value can look selected while the UI still shows กรุณาเลือก and account
    // stays empty — that was the live Jinbao failure mode.
    if (isBankFieldHint(step.field_hint) && root) {
      const acctBox = findFieldContainer(root, 'หมายเลขบัญชี');
      const acctSelect = acctBox && acctBox.querySelector('select');
      if (acctSelect) {
        const depUntil = Date.now() + Math.max(2500, Math.min(timeout, 8000));
        while (Date.now() < depUntil) {
          if (!acctSelect.disabled && usableNativeOptions(acctSelect).length > 0) break;
          // Re-fire bank change in case the first event was dropped.
          if (String(select.value) !== String(want) || isPlaceholderSelectValue(select)) {
            setNativeSelectValue(select, want);
          } else {
            select.dispatchEvent(domEvent(select, 'change'));
          }
          await sleep(120);
        }
        if (acctSelect.disabled || usableNativeOptions(acctSelect).length === 0) {
          return {
            ok: false,
            reason: 'bank_not_applied',
            field: step.field_hint,
            tried_value: value,
            matched: (opt.textContent || '').trim(),
            hint: 'เลือกธนาคารแล้วแต่ช่องหมายเลขบัญชีไม่เปิด — ค่าไม่ถึง Vue/BootstrapVue',
          };
        }
      }
    }

    return { ok: true, matched: (opt.textContent || '').trim(), native: true };
  }

  async function selectOption(step, context, doc) {
    const document = getDocument(doc);
    const root = findScopeRoot(step, context, doc);
    let value = resolveSelectValue(step, context);
    // Map bank codes → full Thai dropdown labels.
    if (value != null && step.field_hint && String(step.field_hint).includes('ธนาคาร')) {
      const upper = String(value).trim().toUpperCase();
      if (BANK_FULL_TH[upper]) value = BANK_FULL_TH[upper];
      else {
        for (const [code, full] of Object.entries(BANK_FULL_TH)) {
          if (bankMatchNeedles(value).some((n) => String(n).includes(code) || full.includes(String(n)))) {
            value = full;
            break;
          }
        }
      }
    }
    // Account fields: prefer a position-aware mask template (works across banks),
    // and keep the last-4 as a fallback value for banks that show the full tail.
    if (step.field_hint && String(step.field_hint).includes('บัญชี')) {
      const template = resolveMaskedTemplate(step, context);
      if (template) step = { ...step, _masked_template: template };
      if (value != null) {
        const digits = String(value).replace(/\D/g, '');
        if (digits.length >= 4) value = digits.slice(-4);
      }
    }

    if (
      (value == null || String(value).trim() === '') &&
      !step.match_pattern &&
      !step._masked_template
    ) {
      if (step.fallback_match_pattern) {
        step = { ...step, match_pattern: step.fallback_match_pattern };
        value = null;
      } else {
        return { ok: false, reason: 'missing_select_value', field: step.field_hint };
      }
    }

    let select = null;
    const fieldBox = step.field_hint ? findFieldContainer(root, step.field_hint) : null;
    if (fieldBox) select = fieldBox.querySelector('select');
    else if (step.field_hint) {
      const labels = [...root.querySelectorAll('label, legend')].filter((l) =>
        (l.textContent || '').includes(step.field_hint)
      );
      for (const l of labels) {
        // Control may be a sibling (BootstrapVue legend + div>select), not a child.
        const near =
          l.querySelector('select') ||
          (l.parentElement && l.parentElement.querySelector('select'));
        if (near) {
          select = near;
          break;
        }
      }
    } else {
      select = root.querySelector('select');
    }

    if (select) {
      return applyNativeSelect(select, value, step, root);
    }

    // Element UI / custom dropdown — prefer real el-select in the field box.
    let trigger =
      (fieldBox && fieldBox.querySelector('.el-select, .el-select__wrapper')) ||
      (fieldBox && fieldBox.querySelector('.custom-dropdown, [class*="dropdown"]')) ||
      null;
    if (!trigger && step.field_hint) {
      const labels = [...root.querySelectorAll('label, .el-form-item')].filter((l) => {
        const t = (l.querySelector('.el-form-item__label, label') || l).textContent || '';
        return t.includes(step.field_hint) && t.length < 80;
      });
      if (labels.length) {
        trigger = labels[0].querySelector('.el-select, .el-select__wrapper, .custom-dropdown');
      }
    }
    if (!trigger) {
      trigger = root.querySelector('.el-select, .custom-dropdown');
    }
    if (!trigger) return { ok: false, reason: 'select_control_not_found', field: step.field_hint };

    // Disabled select (e.g. account before bank chosen).
    const disabled =
      trigger.classList.contains('is-disabled') ||
      trigger.getAttribute('aria-disabled') === 'true' ||
      Boolean(trigger.querySelector('.is-disabled, [disabled]'));
    if (disabled) {
      return {
        ok: false,
        reason: 'select_disabled',
        field: step.field_hint,
        hint: 'เลือกชื่อธนาคารให้ติดก่อน ช่องหมายเลขบัญชีถึงจะเปิด',
      };
    }

    // Account step: refuse to proceed if bank field still on placeholder.
    if (isAccountFieldHint(step.field_hint)) {
      const bankBox = findFieldContainer(root, 'ชื่อธนาคาร') || findFieldContainer(root, 'ธนาคาร');
      const bankNative = bankBox && bankBox.querySelector('select');
      if (bankNative) {
        if (isPlaceholderSelectValue(bankNative)) {
          return {
            ok: false,
            reason: 'bank_not_selected',
            field: step.field_hint,
            hint: 'ชื่อธนาคารยังเป็นกรุณาเลือก — ต้องเลือกธนาคารให้ติดก่อน (อย่าเปิด dropdown ซ้ำ)',
          };
        }
      } else {
        const bankTrigger =
          bankBox && bankBox.querySelector('.el-select, .el-select__wrapper, .custom-dropdown');
        const bankShown = readSelectDisplayValue(bankTrigger);
        if (!bankTrigger || !bankShown) {
          return {
            ok: false,
            reason: 'bank_not_selected',
            field: step.field_hint,
            hint: 'ชื่อธนาคารยังเป็นกรุณาเลือก — ต้องเลือกธนาคารให้ติดก่อน (อย่าเปิด dropdown ซ้ำ)',
          };
        }
      }
    }

    // Close leftover selects first — twin open panels look like 4→8 banks.
    closeOpenSelectDropdowns(document);
    await sleep(100);

    if (typeof trigger.scrollIntoView === 'function') {
      trigger.scrollIntoView({ block: 'center', inline: 'nearest' });
    }

    // Element UI select: Vue-only, NEVER click to open (click duplicates the option list 4→8).
    const isElSelect =
      trigger.classList &&
      (trigger.classList.contains('el-select') || trigger.classList.contains('el-select__wrapper'));
    if (isElSelect) {
      return selectElSelectViaVue(trigger, value, step);
    }

    // From here on: `.custom-dropdown` fixtures only — click path is safe for them.
    openElementUiSelect(trigger);
    await sleep(300);

    const timeout = step.timeout_ms || 4000;
    const start = Date.now();
    let item = null;
    let optionCount = 0;
    let reopened = false;
    while (Date.now() - start < timeout) {
      const items = collectOptionsForTrigger(trigger, document);
      optionCount = items.length;
      item = pickBestOption(items, value, step);
      if (!item && step.fallback_match_pattern) {
        item = pickBestOption(items, null, { ...step, match_pattern: step.fallback_match_pattern });
      }
      if (item) break;
      // Soft re-open once if nothing appeared (never spam — spam causes 4→8 banks).
      if (!reopened && Date.now() - start > 800 && optionCount === 0) {
        reopened = true;
        openElementUiSelect(trigger);
      }
      await sleep(50);
    }
    if (!item) {
      return {
        ok: false,
        reason: 'option_not_found',
        field: step.field_hint,
        tried_value: value,
        option_count: optionCount,
      };
    }

    const itemText = (item.textContent || '').trim();
    // Prefer Vue options API (deduped). Fallback: single mousedown on the li — never click+mousedown combo.
    const appliedVue = tryApplyElementUiOption(trigger, item, itemText || value);
    if (!appliedVue) clickOptionOnce(item);

    const verifyUntil = Date.now() + 3000;
    while (Date.now() < verifyUntil) {
      const shown = readSelectDisplayValue(trigger);
      if (displayMatchesSelection(shown, value, step, itemText)) {
        closeOpenSelectDropdowns(document);
        return { ok: true, matched: itemText, via_vue: appliedVue, option_count: optionCount };
      }
      await sleep(50);
    }
    closeOpenSelectDropdowns(document);
    return {
      ok: false,
      reason: 'option_not_applied',
      field: step.field_hint,
      tried_value: value,
      shown: readSelectDisplayValue(trigger),
      option_count: optionCount,
      hint: 'เลือกแล้วแต่ค่าไม่ติด — มักเปิด dropdown ซ้ำจนธนาคารกลายเป็น 8 รายการ',
    };
  }

  function scrollIntoViewStep(step, context, doc) {
    const document = getDocument(doc);
    const root = findScopeRoot(step, context, doc);

    function findMatch(scope) {
      if (!scope || !step.match_text) return null;
      const matches = [...scope.querySelectorAll('*')].filter((el) => textMatches(el, step.match_text));
      if (!matches.length) return null;
      matches.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length);
      return matches[0];
    }

    function scrollableParent(el) {
      if (!el) return null;
      const body =
        (el.querySelector &&
          (el.querySelector('.el-dialog__body') ||
            el.querySelector('.modal-body') ||
            el.querySelector('[class*="dialog__body"]'))) ||
        null;
      if (body) return body;
      if (el.closest) {
        const nested = el.closest('.el-dialog__body, .modal-body, [class*="dialog__body"]');
        if (nested) return nested;
      }
      return el;
    }

    function doScroll(el) {
      if (!el) return;
      try {
        if (typeof el.scrollIntoView === 'function') {
          el.scrollIntoView({ block: 'center', inline: 'nearest' });
        }
      } catch (_) {
        /* ignore */
      }
      const box = scrollableParent(root) || scrollableParent(el);
      if (box && typeof box.scrollTop === 'number') {
        try {
          const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top;
          box.scrollTop += top - 48;
        } catch (_) {
          box.scrollTop = box.scrollHeight;
        }
      }
    }

    let target = findStepTarget(step, context, document) || findMatch(root);
    if (!target && root !== document.body) target = findMatch(document.body);

    const box = scrollableParent(root);
    if (!target) {
      // Form is below the fold — scroll popup body to bottom even if text not matched yet.
      if (box && typeof box.scrollTop === 'number') {
        box.scrollTop = box.scrollHeight;
        target = findMatch(root) || findMatch(document.body);
        if (target) {
          doScroll(target);
          return { ok: true, matched_after_scroll: true };
        }
        return { ok: true, scrolled_to_bottom: true };
      }
      return { ok: false, reason: 'scroll_target_not_found' };
    }

    doScroll(target);
    if (box && typeof box.scrollTop === 'number' && box.scrollHeight > box.clientHeight + 20) {
      // Nudge further down — transfer form sits under the approval block.
      box.scrollTop = Math.min(box.scrollTop + 400, box.scrollHeight);
    }
    return { ok: true };
  }

  function checkStep(step, context, doc) {
    const root = findScopeRoot(step, context, doc);
    let input = null;
    if (step.match_text) {
      const labels = [...root.querySelectorAll('label, .el-checkbox, span, div')].filter((el) =>
        textMatches(el, step.match_text)
      );
      for (const el of labels) {
        input = el.querySelector('input[type="checkbox"]') || (el.tagName === 'INPUT' ? el : null);
        if (input) break;
        const nearby = el.closest('label, .el-checkbox');
        if (nearby) input = nearby.querySelector('input[type="checkbox"]');
        if (input) break;
      }
    }
    if (!input) input = root.querySelector('input[type="checkbox"]');
    if (!input) return { ok: false, reason: 'checkbox_not_found' };
    if (typeof input.scrollIntoView === 'function') {
      input.scrollIntoView({ block: 'center', inline: 'nearest' });
    }
    if (!input.checked) {
      dispatchClick(input);
      if (!input.checked) {
        input.checked = true;
        input.dispatchEvent(domEvent(input, 'change'));
        input.dispatchEvent(domEvent(input, 'input'));
      }
    }
    return { ok: true };
  }

  function verifyOrFill(step, context, doc) {
    const root = findScopeRoot(step, context, doc);
    const expected = resolvePath(context, step.value_from);
    if (expected == null) return { ok: false, reason: 'missing_expected_value' };

    let input = null;
    if (step.field_hint) {
      const labels = [...root.querySelectorAll('label')].filter((l) =>
        (l.textContent || '').includes(step.field_hint)
      );
      if (labels.length) input = labels[0].querySelector('input, textarea');
    }
    if (!input) input = root.querySelector('input[name="account_number"], input[type="text"]');
    if (!input) return { ok: false, reason: 'field_not_found' };

    const actual = (input.value || '').replace(/\D/g, '');
    const exp = String(expected).replace(/\D/g, '');
    if (actual && actual !== exp) {
      return { ok: false, reason: 'pending_review', field: step.field_hint || 'account' };
    }
    if (!actual) {
      input.value = expected;
      input.dispatchEvent(domEvent(input, 'input'));
    }
    return { ok: true };
  }

  function verifyResult(step, context, doc) {
    const document = getDocument(doc);
    const timeout = step.timeout_ms || 15000;
    const indicators = step.indicators || [];
    const start = Date.now();

    return new Promise((resolve) => {
      const check = () => {
        const hay = document.body ? document.body.textContent || '' : '';
        if (indicators.some((ind) => hay.includes(ind))) {
          resolve({ ok: true, verified: true });
          return;
        }
        if (Date.now() - start >= timeout) {
          resolve({ ok: false, reason: 'verify_result_timeout' });
          return;
        }
        setTimeout(check, 100);
      };
      check();
    });
  }

  function isSubmitStep(step) {
    if (step.action !== 'click') return false;
    const text = step.match_text || '';
    return /ยืนยัน|บันทึก|ตกลง|submit|confirm/i.test(text);
  }

  async function runWorkflowStep(step, stepIndex, profile, context, doc, options) {
    const document = getDocument(doc);
    const dryRun = options.dry_run !== false && profile.dry_run !== false;
    const outlineOnly = Boolean(options.outline_only);

    switch (step.action) {
      case 'click': {
        const target = findStepTarget(step, context, document);
        // dry_run + outline_only: outline first clickable target and stop (no real clicks).
        if (dryRun && (outlineOnly || isSubmitStep(step))) {
          if (target) outlineButton(clickableTarget(target), document);
          return {
            ok: false,
            reason: 'dry_run',
            wouldClick: true,
            stopped_before_submit: true,
            step_index: stepIndex,
          };
        }
        if (!target) return { ok: false, reason: 'click_target_not_found' };
        try {
          if (typeof target.scrollIntoView === 'function') {
            target.scrollIntoView({ block: 'center', inline: 'nearest' });
          }
        } catch (_) {
          /* ignore */
        }
        if (dryRun && options.outline_clicks) outlineButton(clickableTarget(target), document);
        else if (!dispatchClick(target)) return { ok: false, reason: 'click_failed' };
        return { ok: true };
      }
      case 'wait_for': {
        const timeout = step.timeout_ms || 10000;
        const hints = step.selector_hints || POPUP_SCOPE_HINTS;
        const start = Date.now();
        while (Date.now() - start < timeout) {
          const root = findScopeRoot(step, context, document);
          if (step.match_text) {
            const hit = [...root.querySelectorAll('*')].some((el) => textMatches(el, step.match_text));
            if (hit) return { ok: true };
          } else if (queryByHints(root, hints).length > 0) {
            return { ok: true };
          }
          await new Promise((r) => setTimeout(r, 50));
        }
        return { ok: false, reason: 'wait_for_timeout' };
      }
      case 'select_option':
        return selectOption(step, context, document);
      case 'scroll_into_view':
        return scrollIntoViewStep(step, context, document);
      case 'check':
        return checkStep(step, context, document);
      case 'verify_or_fill':
        return verifyOrFill(step, context, document);
      case 'verify_result':
        return verifyResult(step, context, document);
      case 'dismiss_dialog':
        if (dryRun && outlineOnly) {
          return {
            ok: false,
            reason: 'dry_run',
            wouldClick: true,
            stopped_before_submit: true,
            step_index: stepIndex,
          };
        }
        return dismissMessageBox(step, context, document);
      default:
        return { ok: false, reason: `unknown_action_${step.action}` };
    }
  }

  async function runWorkflow(profile, steps, context, options) {
    const opts = options || {};
    const doc = opts.document || getDocument();
    const list = Array.isArray(steps) ? steps : profile.close_job_workflow || [];
    const stepLog = (...args) => {
      try {
        // console.error so it shows even when DevTools filter = Errors only
        console.error('[ClipSync WF]', ...args);
      } catch (_) {
        /* ignore */
      }
    };

    stepLog('start', list.length, 'steps');
    for (let i = 0; i < list.length; i++) {
      const step = list[i] || {};
      stepLog('step', i, step.action, step.match_text || step.field_hint || '');
      const result = await runWorkflowStep(step, i, profile, context || {}, doc, opts);
      stepLog('step_done', i, step.action, result && result.ok, result && result.reason);
      if (!result.ok) {
        return {
          ok: false,
          failed_step: i,
          reason: result.reason || 'step_failed',
          ...result,
        };
      }
    }
    // Final safety: never report success while SweetAlert2 is still on screen.
    const document = getDocument(doc);
    if (document && document.querySelector && document.querySelector('.swal2-container')) {
      stepLog('swal still open after workflow — force dismiss');
      const last = await dismissMessageBox(
        { action: 'dismiss_dialog', match_text: 'ตกลง|OK', timeout_ms: 10000 },
        context || {},
        doc
      );
      if (!last.ok || (document.querySelector && document.querySelector('.swal2-container'))) {
        return {
          ok: false,
          reason: 'swal2_still_open',
          failed_step: list.length - 1,
          hint: 'SweetAlert2 ยังไม่ปิด — กดตกลงไม่ติด',
        };
      }
    }
    return { ok: true, verified: true };
  }

  function waitForConfirmButton(profile, row, timeoutMs, doc) {
    const document = getDocument(doc);
    const max = timeoutMs || profile.click_wait_max_ms || 30000;
    const start = Date.now();

    return new Promise((resolve) => {
      let settled = false;
      /** @type {MutationObserver|null} */
      let observer = null;

      const finish = (result) => {
        if (settled) return;
        settled = true;
        if (observer) observer.disconnect();
        resolve(result);
      };

      const attempt = () => {
        if (settled) return;
        const btnResult = findConfirmButton(profile, row);
        if (
          btnResult.status === 'ok' ||
          btnResult.status === 'button_disabled' ||
          btnResult.status === 'already_confirmed' ||
          btnResult.status === 'ambiguous_buttons'
        ) {
          finish(btnResult);
          return;
        }
        if (Date.now() - start >= max) {
          finish(btnResult);
          return;
        }
        setTimeout(attempt, 500);
      };

      if (typeof MutationObserver !== 'undefined' && document && document.body) {
        observer = new MutationObserver(attempt);
        observer.observe(document.body, { childList: true, subtree: true });
      }
      attempt();
    });
  }

  function waitForPostClickVerify(profile, row, timeoutMs, doc) {
    const max = timeoutMs || profile.post_click_verify_timeout_ms || 15000;
    const indicators = profile.already_confirmed_indicators || [];
    const start = Date.now();

    return new Promise((resolve) => {
      let settled = false;
      /** @type {MutationObserver|null} */
      let observer = null;

      const finish = (result) => {
        if (settled) return;
        settled = true;
        if (observer) observer.disconnect();
        resolve(result);
      };

      const check = () => {
        if (settled) return;
        if (indicators.some((k) => (row.textContent || '').includes(k))) {
          finish({ ok: true, verified: true });
          return;
        }
        if (Date.now() - start >= max) {
          finish({ ok: false, reason: 'clicked_but_unverified' });
          return;
        }
        setTimeout(check, 200);
      };

      if (typeof MutationObserver !== 'undefined' && row) {
        observer = new MutationObserver(check);
        observer.observe(row, { childList: true, subtree: true, characterData: true });
      }
      check();
    });
  }

  return {
    normalize,
    matchNeedles,
    deepFindByText,
    findRow,
    findConfirmButton,
    isLoggedOut,
    checkCanary,
    scrapePendingOrders,
    outlineButton,
    clickableTarget,
    dispatchClick,
    resolveUrlTemplate,
    isApproveStub,
    apiListPending,
    apiApprove,
    apiAdapter,
    runWorkflow,
    runWorkflowStep,
    selectOption,
    findStepTarget,
    collectVisibleSelectOptions,
    collectOptionsForTrigger,
    readSelectDisplayValue,
    waitForConfirmButton,
    waitForPostClickVerify,
    dismissMessageBox,
    setMainWorldClicker,
  };
});
