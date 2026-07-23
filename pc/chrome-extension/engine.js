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
    SCB: ['ไทยพาณิชย์', 'SCB', 'Siam Commercial'],
    KBANK: ['กสิกร', 'กสิกรไทย', 'KBank', 'KBANK'],
    BBL: ['กรุงเทพ', 'BBL', 'Bangkok'],
    KTB: ['กรุงไทย', 'KTB', 'Krungthai'],
    GSB: ['ออมสิน', 'GSB'],
    TTB: ['ทหารไทย', 'ธนชาต', 'TTB', 'ttb'],
    BAY: ['กรุงศรี', 'BAY', 'Krungsri'],
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

  function findRow(profile, refNumber, doc) {
    const document = getDocument(doc);
    if (!document || !document.body) return { status: 'row_not_found' };

    const needles = matchNeedles(refNumber).slice().sort((a, b) => b.length - a.length);
    if (needles.length === 0) return { status: 'row_not_found' };

    const selector = rowSelector(profile);
    let ambiguousRows = null;

    // Try most specific needle first (1828.00 before 1828).
    for (const needle of needles) {
      const rows = collectRowsForNeedle(document, selector, needle);
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
    try {
      const view = target.ownerDocument && target.ownerDocument.defaultView;
      if (view && view.MouseEvent) {
        target.dispatchEvent(
          new view.MouseEvent('click', { bubbles: true, cancelable: true, view, buttons: 1 })
        );
      }
    } catch (_) {
      /* fall through to .click() */
    }
    try {
      target.click();
    } catch (_) {
      return false;
    }
    return true;
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
    try {
      if (el.getClientRects && el.getClientRects().length > 0) return true;
    } catch (_) {
      /* ignore */
    }
    return Boolean(el.offsetParent);
  }

  function findScopeRoot(step, context, doc) {
    const document = getDocument(doc);
    if (step.scope === 'popup') {
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
    if (!value) return false;
    if (t.includes(value) || value.includes(t)) return true;
    return bankMatchNeedles(value).some((n) => t.includes(n));
  }

  function findFieldContainer(root, fieldHint) {
    if (!root || !fieldHint) return null;
    const labeled = [...root.querySelectorAll('.el-form-item, .form-group, label, div')];
    for (const el of labeled) {
      const labelEl = el.querySelector('.el-form-item__label, label, .control-label');
      const labelText = labelEl ? labelEl.textContent || '' : '';
      // Prefer short label text over giant containers.
      if (labelText.includes(fieldHint) && labelText.length < 80) return el;
    }
    for (const el of labeled) {
      const own = (el.childNodes && el.childNodes[0] && el.childNodes[0].textContent) || '';
      if (own.includes(fieldHint) && own.length < 80) return el;
    }
    return null;
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

    if (step.match_text && candidates.length === 0) {
      // Prefer controls over ancestor containers (div/span textContent includes children).
      const interactive = [
        ...root.querySelectorAll("button, a, a[role='button'], input[type='submit'], [onclick], li, option"),
      ].filter((el) => textMatches(el, step.match_text));
      candidates =
        interactive.length > 0
          ? interactive
          : [...root.querySelectorAll('span, div')].filter((el) => textMatches(el, step.match_text));
    }

    return pickTarget(candidates, step);
  }

  function domEvent(node, type) {
    const win = node && node.ownerDocument ? node.ownerDocument.defaultView : null;
    if (win && win.Event) return new win.Event(type, { bubbles: true });
    return { type, bubbles: true };
  }

  async function selectOption(step, context, doc) {
    const document = getDocument(doc);
    const root = findScopeRoot(step, context, doc);
    const value = step.value_from ? resolvePath(context, step.value_from) : step.match_text;
    if (!value && !step.match_pattern) return { ok: false, reason: 'missing_select_value' };

    let select = null;
    const fieldBox = step.field_hint ? findFieldContainer(root, step.field_hint) : null;
    if (fieldBox) select = fieldBox.querySelector('select');
    else if (step.field_hint) {
      const labels = [...root.querySelectorAll('label')].filter((l) =>
        (l.textContent || '').includes(step.field_hint)
      );
      if (labels.length) select = labels[0].querySelector('select');
    } else {
      select = root.querySelector('select');
    }

    if (select) {
      const opt = [...select.options].find((o) => optionTextMatches(o.textContent || o.value, value, step));
      if (!opt) return { ok: false, reason: 'option_not_found' };
      select.value = opt.value;
      select.dispatchEvent(domEvent(select, 'change'));
      return { ok: true };
    }

    // Element UI / custom dropdown
    let trigger =
      (fieldBox &&
        fieldBox.querySelector(
          '.el-select, .el-select__wrapper, [class*="select"], .custom-dropdown, [class*="dropdown"]'
        )) ||
      null;
    if (!trigger && step.field_hint) {
      const labels = [...root.querySelectorAll('label')].filter((l) =>
        (l.textContent || '').includes(step.field_hint)
      );
      if (labels.length) {
        trigger = labels[0].querySelector('.el-select, .custom-dropdown, [class*="dropdown"], [class*="select"]');
      }
    }
    if (!trigger) {
      trigger = root.querySelector('.el-select, .custom-dropdown, [class*="dropdown"]');
    }
    if (!trigger) return { ok: false, reason: 'select_control_not_found' };

    const clickEl =
      trigger.querySelector('.el-input__inner, .el-select__wrapper, input, button, .dropdown-toggle') ||
      trigger;
    if (typeof clickEl.scrollIntoView === 'function') {
      clickEl.scrollIntoView({ block: 'center', inline: 'nearest' });
    }
    dispatchClick(clickEl);

    const timeout = step.timeout_ms || 4000;
    const start = Date.now();
    let item = null;
    while (Date.now() - start < timeout) {
      const items = [
        ...document.querySelectorAll(
          '.el-select-dropdown__item, .el-scrollbar__view li, [role="option"], .dropdown-menu li, li'
        ),
      ].filter((el) => isVisible(el));
      item = items.find((el) => optionTextMatches(el.textContent, value, step));
      if (item) break;
      await sleep(50);
    }
    if (!item) return { ok: false, reason: 'option_not_found' };
    dispatchClick(item);
    await sleep(150);
    return { ok: true };
  }

  function scrollIntoViewStep(step, context, doc) {
    const document = getDocument(doc);
    const root = findScopeRoot(step, context, doc);
    let target = findStepTarget(step, context, document);
    if (!target && step.match_text) {
      const nodes = [...root.querySelectorAll('div, span, h1, h2, h3, h4, label, section')].filter((el) =>
        textMatches(el, step.match_text)
      );
      target = nodes.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length)[0] || null;
    }
    if (!target) return { ok: false, reason: 'scroll_target_not_found' };
    try {
      if (typeof target.scrollIntoView === 'function') {
        target.scrollIntoView({ block: 'center', inline: 'nearest' });
      }
    } catch (_) {
      /* ignore */
    }
    const dialogBody = target.closest('.el-dialog__body, .modal-body, [class*="dialog"]');
    if (dialogBody && typeof dialogBody.scrollTop === 'number') {
      const top = target.getBoundingClientRect().top - dialogBody.getBoundingClientRect().top;
      dialogBody.scrollTop += top - 40;
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
          if (queryByHints(root, hints).length > 0) return { ok: true };
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
    waitForConfirmButton,
    waitForPostClickVerify,
  };
});
