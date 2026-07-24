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
        target.dispatchEvent(new view.MouseEvent('pointerdown', base));
        target.dispatchEvent(new view.MouseEvent('mousedown', base));
        target.dispatchEvent(new view.MouseEvent('pointerup', base));
        target.dispatchEvent(new view.MouseEvent('mouseup', base));
        target.dispatchEvent(new view.MouseEvent('click', base));
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

  /**
   * Dismiss a success/confirm MessageBox (Element UI / Bootstrap modal).
   * Verifies the dialog actually disappears — .click() alone often reports ok
   * while the Jinbao success popup stays open.
   */
  async function dismissMessageBox(step, context, doc) {
    const document = getDocument(doc);
    if (!document || !document.body) {
      return { ok: false, reason: 'dismiss_no_document' };
    }
    const boxSelectors = [
      '.el-message-box__wrapper',
      '.el-message-box',
      '.el-overlay.is-message-box',
      '[class*="message-box"]',
      '.modal.show',
      '.modal[style*="display: block"]',
      '[role="dialog"]',
      '.swal2-container',
    ];
    const btnNeedles = String(step.match_text || 'ตกลง|OK|Ok|ok').split('|');
    const timeout = step.timeout_ms || 6000;
    const start = Date.now();

    const visibleBoxes = () => {
      const out = [];
      for (const sel of boxSelectors) {
        let nodes = [];
        try {
          nodes = [...document.querySelectorAll(sel)];
        } catch (_) {
          nodes = [];
        }
        for (const n of nodes) {
          if (isVisible(n)) out.push(n);
        }
      }
      // Prefer innermost / message-box over generic dialogs.
      return [...new Set(out)];
    };

    const findOkButton = (box) => {
      const buttons = [
        ...box.querySelectorAll(
          'button.el-button--primary, .el-message-box__btns button, .modal-footer button, button.btn-primary, button.swal2-confirm, button'
        ),
      ];
      const exact = buttons.filter((b) => {
        const t = String(b.textContent || b.value || '')
          .replace(/\s+/g, ' ')
          .trim();
        return btnNeedles.some((n) => t.toLowerCase() === String(n).trim().toLowerCase());
      });
      if (exact.length) return exact[exact.length - 1];
      const soft = buttons.filter((b) =>
        btnNeedles.some((n) => textMatches(b, n))
      );
      if (soft.length) return soft[soft.length - 1];
      return (
        box.querySelector('button.el-button--primary') ||
        box.querySelector('.el-message-box__btns button') ||
        buttons[buttons.length - 1] ||
        null
      );
    };

    let lastReason = 'dismiss_dialog_not_found';
    while (Date.now() - start < timeout) {
      const boxes = visibleBoxes();
      if (!boxes.length) {
        // Already gone — treat as success (e.g. auto-closed).
        if (Date.now() - start > 400) return { ok: true, dismissed: true, already_gone: true };
        await sleep(80);
        continue;
      }
      // Prefer a box that contains success copy when present.
      const prefer = boxes.find((b) =>
        /สำเร็จ|บันทึก|success|confirm/i.test(b.textContent || '')
      );
      const box = prefer || boxes[boxes.length - 1];
      const btn = findOkButton(box);
      if (!btn) {
        lastReason = 'dismiss_button_not_found';
        await sleep(120);
        continue;
      }
      dispatchClick(btn);
      forceClick(btn);
      // Enter key fallback (Element UI MessageBox often binds confirm to Enter).
      try {
        const view = document.defaultView;
        if (view && view.KeyboardEvent) {
          const opts = { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13 };
          box.dispatchEvent(new view.KeyboardEvent('keydown', opts));
          (document.activeElement || box).dispatchEvent(new view.KeyboardEvent('keydown', opts));
        }
      } catch (_) {
        /* ignore */
      }

      const until = Date.now() + 2500;
      while (Date.now() < until) {
        const still = visibleBoxes().some((b) => b === box || (box.contains && box.contains(b)));
        const anyBox = visibleBoxes().length > 0;
        // Success when the specific box is gone, or no message-box wrappers remain.
        if (!still && (!anyBox || !isVisible(box))) {
          return { ok: true, dismissed: true };
        }
        // Some wrappers stay in DOM with display:none — isVisible handles that.
        if (!isVisible(box)) return { ok: true, dismissed: true };
        await sleep(100);
      }
      lastReason = 'dismiss_dialog_still_open';
      await sleep(150);
    }
    return { ok: false, reason: lastReason };
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
    cands.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length);
    return cands.find((el) => el.querySelector('fieldset, select, .el-select')) || cands[0];
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

  /** Prefer real form rows that contain a control (avoid member-info display labels). */
  function findFieldContainer(root, fieldHint) {
    if (!root || !fieldHint) return null;
    // fieldset = BootstrapVue field; .form-group/.el-form-item = other frameworks.
    const formItems = [...root.querySelectorAll('fieldset, .el-form-item, .form-group')];
    // Pass 1: label matches AND container has a control.
    for (const el of formItems) {
      const labelText = fieldLabelText(el);
      if (!labelText.includes(fieldHint) || labelText.length >= 80) continue;
      if (el.querySelector(FIELD_CONTROL_SELECTOR)) return el;
    }
    // Pass 2: label matches (control optional).
    for (const el of formItems) {
      const labelText = fieldLabelText(el);
      if (labelText.includes(fieldHint) && labelText.length < 80) return el;
    }
    // Pass 3: generic label/div fallback.
    const labeled = [...root.querySelectorAll('label, div')];
    for (const el of labeled) {
      const labelText = fieldLabelText(el);
      if (labelText.includes(fieldHint) && labelText.length < 80) return el;
    }
    for (const el of labeled) {
      const own = (el.childNodes && el.childNodes[0] && el.childNodes[0].textContent) || '';
      if (own.includes(fieldHint) && own.length < 80) return el;
    }
    return null;
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
      const proto = Object.getPrototypeOf(select);
      const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(select, val);
      else select.value = val;
    } catch (_) {
      select.value = val;
    }
    // BootstrapVue / Vue listen on change; some bindings also want input.
    select.dispatchEvent(domEvent(select, 'input'));
    select.dispatchEvent(domEvent(select, 'change'));
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
   * Native <select> apply with polling — the account select is disabled + empty until the
   * bank change event repopulates it, so we wait for the real option to appear.
   */
  async function applyNativeSelect(select, value, step, root) {
    const isAccount = step.field_hint && String(step.field_hint).includes('หมายเลขบัญชี');
    if (isAccount) {
      const bankBox = root && (findFieldContainer(root, 'ชื่อธนาคาร') || findFieldContainer(root, 'ธนาคาร'));
      const bankSelect = bankBox && bankBox.querySelector('select');
      if (bankSelect && isPlaceholderSelectValue(bankSelect)) {
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
    // Safety for account matching: an OCR-read last-4 must map to exactly ONE
    // shop account. If several accounts share the same last-4, refuse to guess.
    if (isAccount && matches.length > 1) {
      return {
        ok: false,
        reason: 'account_ambiguous',
        field: step.field_hint,
        tried_value: value,
        option_count: matches.length,
        hint: 'มีหลายบัญชีลงท้ายเลขเดียวกัน — เลือกเองเพื่อความปลอดภัย',
      };
    }
    const opt = matches[0];

    setNativeSelectValue(select, opt.value);

    // Verify the value actually stuck (native select reflects immediately).
    const verifyUntil = Date.now() + 1500;
    const want = opt.value;
    while (Date.now() < verifyUntil) {
      if (String(select.value) === String(want)) {
        return { ok: true, matched: (opt.textContent || '').trim(), native: true };
      }
      await sleep(50);
    }
    return {
      ok: false,
      reason: 'option_not_applied',
      field: step.field_hint,
      tried_value: value,
      shown: select.value,
      hint: 'ตั้งค่า select แล้วแต่ค่าไม่ติด — ตรวจ event binding ของหน้าเว็บ',
    };
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
    if (step.field_hint && String(step.field_hint).includes('หมายเลขบัญชี')) {
      const bankBox = findFieldContainer(root, 'ชื่อธนาคาร') || findFieldContainer(root, 'ธนาคาร');
      const bankTrigger =
        bankBox && bankBox.querySelector('.el-select, .el-select__wrapper, .custom-dropdown');
      const bankShown = readSelectDisplayValue(bankTrigger);
      if (!bankShown) {
        return {
          ok: false,
          reason: 'bank_not_selected',
          field: step.field_hint,
          hint: 'ชื่อธนาคารยังเป็นกรุณาเลือก — ต้องเลือกธนาคารให้ติดก่อน (อย่าเปิด dropdown ซ้ำ)',
        };
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

    for (let i = 0; i < list.length; i++) {
      const result = await runWorkflowStep(list[i], i, profile, context || {}, doc, opts);
      if (!result.ok) {
        return {
          ok: false,
          failed_step: i,
          reason: result.reason || 'step_failed',
          ...result,
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
  };
});
