/**
 * Text-anchor engine tests (Task 4.3).
 * Uses synthetic HTML fixtures — real partner snapshots are Task 4.0 (blocked).
 */

const { describe, it, before, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const {
  normalize,
  matchNeedles,
  deepFindByText,
  findRow,
  findConfirmButton,
  checkCanary,
  scrapePendingOrders,
  apiAdapter,
  runWorkflow,
  selectOption,
  collectVisibleSelectOptions,
  collectOptionsForTrigger,
  readSelectDisplayValue,
  findStepTarget,
  dismissMessageBox,
} = require('../engine.js');

const ORDER_FIXTURE = path.join(__dirname, '..', 'fixtures', 'order_list.html');
const POPUP_FIXTURE = path.join(__dirname, '..', 'fixtures', 'close_job_popup.html');

const PROFILE = {
  profile_id: 'synthetic_test_v1',
  domain_patterns: ['https://admin.example.invalid/*'],
  order_page_url_hint: '/orders',
  row_selector_hints: ['tr', "[class*='order']", "[class*='row']", 'li', "[class*='card']"],
  confirm_keywords: ['ยืนยัน', 'confirm', 'อนุมัติ', 'approve', 'สำเร็จ'],
  already_confirmed_indicators: ['ยืนยันแล้ว', 'confirmed', 'สำเร็จแล้ว', 'approved'],
  logout_indicators: ["form[action*='login']", "input[type='password']"],
  order_list_canary_selector: "table, [class*='order-list'], [class*='order']",
  uses_iframe: false,
  dry_run: true,
  post_click_verify_timeout_ms: 15000,
  click_wait_max_ms: 30000,
};

const WORKFLOW_PROFILE = {
  ...PROFILE,
  dry_run: true,
  close_job_workflow: [
    {
      action: 'click',
      target: { in_row: true, selector_hints: ['.eye-btn', 'button'], nth_fallback: 'last' },
    },
    { action: 'wait_for', selector_hints: ["[class*='modal']", "[class*='popup']"], timeout_ms: 5000 },
    { action: 'select_option', scope: 'popup', match_text: 'สำเร็จ' },
    {
      action: 'select_option',
      scope: 'popup',
      field_hint: 'ธนาคาร',
      value_from: 'slip.bank_name_th',
    },
    {
      action: 'verify_or_fill',
      scope: 'popup',
      field_hint: 'เลขบัญชี',
      value_from: 'slip.account_number',
    },
    { action: 'click', scope: 'popup', match_text: 'ยืนยัน|บันทึก|ตกลง' },
    { action: 'verify_result', indicators: ['ปิดงานสำเร็จ'], timeout_ms: 5000 },
  ],
};

describe('normalize', () => {
  it('strips spaces and dashes', () => {
    assert.equal(normalize('ABC-12 34'), 'ABC1234');
    assert.equal(normalize(null), '');
  });

  it('strips thousands separators so amounts match', () => {
    assert.equal(normalize('1,347.00'), '1347.00');
    assert.equal(normalize('1347.00'), '1347.00');
  });

  it('expands float amount needles', () => {
    assert.deepEqual(matchNeedles('1175.0').sort(), ['1175', '1175.0', '1175.00'].sort());
  });
});

describe('deepFindByText + findRow + findConfirmButton', () => {
  /** @type {string} */
  let html;

  before(() => {
    html = fs.readFileSync(ORDER_FIXTURE, 'utf8');
  });

  beforeEach(() => {
    const dom = new JSDOM(html);
    global.document = dom.window.document;
  });

  it('finds a row by exact ref', () => {
    const result = findRow(PROFILE, 'ORD1001');
    assert.equal(result.status, 'ok');
    assert.ok(result.row);
    assert.match(result.row.textContent, /ORD1001/);
  });

  it('finds a row by amount with comma thousands separator', () => {
    const dom = new JSDOM(`<!doctype html><body>
      <table>
        <tr class="order-row"><td>A</td><td>1,517.00</td><td><button class="eye">view</button></td></tr>
        <tr class="order-row"><td>B</td><td><span>1,175</span><span>.00</span></td><td><button class="eye">view</button></td></tr>
      </table>
    </body>`);
    global.document = dom.window.document;
    const profile = {
      ...PROFILE,
      row_selector_hints: ['tr.order-row', 'tr'],
    };
    const byFloat = findRow(profile, '1175.0');
    assert.equal(byFloat.status, 'ok');
    assert.match(byFloat.row.textContent, /1,175/);
    const byOther = findRow(profile, '1517.00');
    assert.equal(byOther.status, 'ok');
    assert.match(byOther.row.textContent, /1,517/);
  });

  it('dedupes nested row wrappers for the same amount', () => {
    const dom = new JSDOM(`<!doctype html><body>
      <div class="el-table">
        <table>
          <tr class="el-table__row"><td><div class="cell">1,828.00</div></td></tr>
        </table>
      </div>
    </body>`);
    global.document = dom.window.document;
    const profile = {
      ...PROFILE,
      row_selector_hints: ['tr.el-table__row', '[class*="el-table"]', 'tr'],
    };
    const result = findRow(profile, '1828.00');
    assert.equal(result.status, 'ok');
    assert.equal(result.row.className, 'el-table__row');
  });

  it('finds a row when ref has spaces or dashes', () => {
    const spaced = findRow(PROFILE, 'ORD-1001');
    assert.equal(spaced.status, 'ok');
    const dashed = findRow(PROFILE, 'ORD 1001');
    assert.equal(dashed.status, 'ok');
    assert.equal(spaced.row, dashed.row);
  });

  it('returns row_not_found for unknown ref', () => {
    const result = findRow(PROFILE, 'MISSING999');
    assert.equal(result.status, 'row_not_found');
  });

  it('returns ambiguous when the same ref appears in multiple rows', () => {
    const result = findRow(PROFILE, 'DUP0001');
    assert.equal(result.status, 'ambiguous');
  });

  it('disambiguates same amount using member account last4 + bank', () => {
    const dom = new JSDOM(`<!doctype html><body>
      <table>
        <tr class="order-row">
          <td>WD1</td><td>100.00</td><td>ธนาคารกสิกรไทย</td><td>xxx0860</td>
          <td><button class="eye">view</button></td>
        </tr>
        <tr class="order-row">
          <td>WD2</td><td>100.00</td><td>ธนาคารกรุงไทย</td><td>xxx0860</td>
          <td><button class="eye">view</button></td>
        </tr>
        <tr class="order-row">
          <td>WD3</td><td>100.00</td><td>ธนาคารกรุงไทย</td><td>xxx1234</td>
          <td><button class="eye">view</button></td>
        </tr>
      </table>
    </body>`);
    global.document = dom.window.document;
    const profile = { ...PROFILE, row_selector_hints: ['tr.order-row', 'tr'] };

    assert.equal(findRow(profile, '100.00').status, 'ambiguous');

    const byAcct = findRow(profile, '100.00', document, {
      account_last4: '1234',
    });
    assert.equal(byAcct.status, 'ok', JSON.stringify(byAcct));
    assert.match(byAcct.row.textContent, /WD3/);

    const byBank = findRow(profile, '100.00', document, {
      account_last4: '0860',
      bank: 'KTB',
    });
    assert.equal(byBank.status, 'ok', JSON.stringify(byBank));
    assert.match(byBank.row.textContent, /WD2/);
  });

  it('returns already_confirmed when indicators are present', () => {
    const rowResult = findRow(PROFILE, 'ORD2002');
    assert.equal(rowResult.status, 'ok');
    const btn = findConfirmButton(PROFILE, rowResult.row);
    assert.equal(btn.status, 'already_confirmed');
  });

  it('returns button_disabled when the confirm control is disabled', () => {
    const rowResult = findRow(PROFILE, 'ORD3003');
    assert.equal(rowResult.status, 'ok');
    const btn = findConfirmButton(PROFILE, rowResult.row);
    assert.equal(btn.status, 'button_disabled');
  });

  it('returns ok + btn for a pending confirmable row', () => {
    const rowResult = findRow(PROFILE, 'ORD1001');
    assert.equal(rowResult.status, 'ok');
    const btn = findConfirmButton(PROFILE, rowResult.row);
    assert.equal(btn.status, 'ok');
    assert.ok(btn.btn);
    assert.match((btn.btn.textContent || '').toLowerCase(), /confirm|ยืนยัน/);
  });

  it('deepFindByText walks leaf text nodes', () => {
    const hits = deepFindByText(document.body, normalize('ORD1001'));
    assert.ok(hits.length >= 1);
  });

  it('checkCanary passes on order list fixture', () => {
    const health = checkCanary(PROFILE);
    assert.equal(health.canary_ok, true);
    assert.equal(health.logged_in, true);
  });

  it('scrapePendingOrders finds refs and amounts', () => {
    const orders = scrapePendingOrders(PROFILE);
    assert.ok(orders.length >= 3);
    const first = orders.find((o) => o.ref.includes('ORD1001'));
    assert.ok(first);
    assert.match(first.amount, /1,250\.00/);
  });
});

describe('apiAdapter', () => {
  it('returns stub for TODO approve endpoint', async () => {
    const profile = {
      api: {
        enabled: true,
        list_pending: {
          method: 'GET',
          url_template: '/bo/withdrawal-approve-search?start_date={today}',
          fields_map: { order_id: 'id', amount: 'amount' },
        },
        approve: { method: 'POST', url_template: 'TODO: from HAR recon', payload_template: {} },
      },
    };
    const adapter = apiAdapter(profile, async () => ({
      ok: true,
      status: 200,
      json: async () => ({ items: [{ id: '1', amount: 100 }] }),
    }));
    const list = await adapter.listPending({});
    assert.equal(list.status, 'ok');
    assert.equal(list.orders.length, 1);
    const approve = await adapter.approve('1', {});
    assert.equal(approve.status, 'stub');
    assert.equal(approve.reason, 'approve_endpoint_todo');
  });
});

describe('runWorkflow', () => {
  /** @type {string} */
  let popupHtml;

  before(() => {
    popupHtml = fs.readFileSync(POPUP_FIXTURE, 'utf8');
  });

  beforeEach(() => {
    const dom = new JSDOM(popupHtml, { runScripts: 'dangerously' });
    global.document = dom.window.document;
  });

  it('dry_run stops before final submit click', async () => {
    const rowResult = findRow(WORKFLOW_PROFILE, 'WD1001');
    assert.equal(rowResult.status, 'ok');

    const result = await runWorkflow(
      WORKFLOW_PROFILE,
      WORKFLOW_PROFILE.close_job_workflow,
      {
        row: rowResult.row,
        slip: { bank_name_th: 'ไทยพาณิชย์', account_number: '1234567890' },
      },
      { dry_run: true, outline_clicks: true }
    );

    assert.equal(result.ok, false);
    assert.equal(result.reason, 'dry_run');
    assert.equal(result.wouldClick, true);
    assert.equal(result.stopped_before_submit, true);
    const submit = document.querySelector('.submit-btn');
    assert.equal(submit.getAttribute('data-clipsync-dry-run'), '1');
  });

  it('completes success path when dry_run is false', async () => {
    const profile = { ...WORKFLOW_PROFILE, dry_run: false };
    const rowResult = findRow(profile, 'WD1001');
    assert.equal(rowResult.status, 'ok');

    const result = await runWorkflow(
      profile,
      profile.close_job_workflow,
      {
        row: rowResult.row,
        slip: { bank_name_th: 'ไทยพาณิชย์', account_number: '1234567890' },
      },
      { dry_run: false }
    );

    assert.equal(result.ok, true);
    assert.equal(result.verified, true);
    assert.equal(document.querySelector('#status-select').value, 'success');
    assert.match(document.querySelector('.dropdown-toggle').textContent, /ไทยพาณิชย์/);
    assert.equal(document.querySelector('input[name="account_number"]').value, '1234567890');
    assert.match(document.querySelector('.result-banner').textContent, /ปิดงานสำเร็จ/);
  });

  it('fail-fast when popup missing on wait_for', async () => {
    document.querySelector('.modal').remove();
    const rowResult = findRow(WORKFLOW_PROFILE, 'WD1001');
    const steps = [
      {
        action: 'click',
        target: { in_row: true, selector_hints: ['.eye-btn'] },
      },
      { action: 'wait_for', selector_hints: ["[class*='modal']"], timeout_ms: 200 },
    ];
    const result = await runWorkflow(WORKFLOW_PROFILE, steps, { row: rowResult.row }, { dry_run: false });
    assert.equal(result.ok, false);
    assert.equal(result.failed_step, 1);
    assert.equal(result.reason, 'wait_for_timeout');
  });

  it('fail-fast on account mismatch (pending_review)', async () => {
    const profile = { ...WORKFLOW_PROFILE, dry_run: false };
    document.querySelector('input[name="account_number"]').value = '9999999999';
    const rowResult = findRow(profile, 'WD1001');
    const result = await runWorkflow(
      profile,
      profile.close_job_workflow,
      {
        row: rowResult.row,
        slip: { bank_name_th: 'ไทยพาณิชย์', account_number: '1234567890' },
      },
      { dry_run: false }
    );
    assert.equal(result.ok, false);
    assert.equal(result.failed_step, 4);
    assert.equal(result.reason, 'pending_review');
  });
});

describe('Element UI select_option', () => {
  it('does not double-count options from nested el-popper + el-select-dropdown', () => {
    const dom = new JSDOM(`<!DOCTYPE html><body>
      <div class="el-dialog" role="dialog">
        <div class="el-form-item">
          <label class="el-form-item__label">ชื่อธนาคาร</label>
          <div class="el-select"><input class="el-input__inner" value="" placeholder="--- กรุณาเลือก ---" /></div>
        </div>
      </div>
      <div class="el-popper" style="display:block">
        <div class="el-select-dropdown el-popper" style="display:block">
          <ul class="el-scrollbar__view">
            <li class="el-select-dropdown__item">ธนาคารกรุงไทย</li>
            <li class="el-select-dropdown__item">ธนาคารออมสิน</li>
            <li class="el-select-dropdown__item">ธนาคารกสิกรไทย</li>
            <li class="el-select-dropdown__item">ธนาคารไทยพาณิชย์</li>
          </ul>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    const items = collectVisibleSelectOptions(document);
    const labels = items.map((el) => (el.textContent || '').trim());
    assert.equal(labels.length, 4, `expected 4 unique options, got ${labels.length}: ${labels.join('|')}`);
    assert.deepEqual(labels, [
      'ธนาคารกรุงไทย',
      'ธนาคารออมสิน',
      'ธนาคารกสิกรไทย',
      'ธนาคารไทยพาณิชย์',
    ]);
  });

  it('dedupes twin open bank dropdown panels to one option each', () => {
    const dom = new JSDOM(`<!DOCTYPE html><body>
      <div class="el-dialog" role="dialog">
        <div class="el-form-item">
          <label class="el-form-item__label">ชื่อธนาคาร</label>
          <div id="bank" class="el-select" style="position:absolute;top:100px;left:40px;width:200px;height:32px">
            <input class="el-input__inner" value="" placeholder="--- กรุณาเลือก ---" />
          </div>
        </div>
      </div>
      <div class="el-select-dropdown" style="position:absolute;top:10px;left:0;display:block">
        <ul>
          <li class="el-select-dropdown__item">ธนาคารกสิกรไทย</li>
          <li class="el-select-dropdown__item">ธนาคารไทยพาณิชย์</li>
        </ul>
      </div>
      <div class="el-select-dropdown" style="position:absolute;top:140px;left:40px;display:block">
        <ul>
          <li class="el-select-dropdown__item">ธนาคารกสิกรไทย</li>
          <li class="el-select-dropdown__item">ธนาคารไทยพาณิชย์</li>
        </ul>
      </div>
    </body>`);
    global.document = dom.window.document;
    // jsdom getBoundingClientRect is zeros — still dedupe by text across all panels via collectVisible.
    const all = collectVisibleSelectOptions(document);
    assert.equal(all.length, 2);
    const near = collectOptionsForTrigger(document.getElementById('bank'), document);
    assert.equal(near.length, 2);
  });

  // Mock an Element UI Select Vue instance: options list + handleOptionSelect.
  // `applyEffect` runs when an option is selected (or is a no-op to simulate a stuck select).
  function mockElSelectVue(selectEl, labels, applyEffect) {
    let visible = false;
    const options = labels.map((label) => ({ currentLabel: label, label, value: label }));
    selectEl.__vue__ = {
      options,
      hoverOptions: [],
      get visible() {
        return visible;
      },
      set visible(v) {
        visible = v;
      },
      handleOptionSelect(opt) {
        if (typeof applyEffect === 'function') applyEffect(opt);
      },
    };
    return selectEl.__vue__;
  }

  it('rejects false success when Vue handleOptionSelect does not update the input (stays placeholder)', async () => {
    const dom = new JSDOM(`<!DOCTYPE html><body>
      <div class="el-dialog" role="dialog">
        <div class="el-form-item">
          <label class="el-form-item__label">ชื่อธนาคาร</label>
          <div class="el-select">
            <input class="el-input__inner" value="" placeholder="--- กรุณาเลือก ---" />
          </div>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    // Vue has the option but selecting it is a no-op (simulates Element UI not committing).
    mockElSelectVue(document.querySelector('.el-select'), ['ธนาคารกรุงไทย', 'ธนาคารกสิกรไทย'], null);

    const result = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        field_hint: 'ชื่อธนาคาร',
        match_text: 'ธนาคารกสิกรไทย',
        timeout_ms: 400,
      },
      {},
      document
    );
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'option_not_applied');
    assert.equal(readSelectDisplayValue(document.querySelector('.el-select')), '');
  });

  it('returns el_select_no_vue for an Element UI select with no Vue instance (never clicks)', async () => {
    const dom = new JSDOM(`<!DOCTYPE html><body>
      <div class="el-dialog" role="dialog">
        <div class="el-form-item">
          <label class="el-form-item__label">ชื่อธนาคาร</label>
          <div class="el-select">
            <input class="el-input__inner" value="" placeholder="--- กรุณาเลือก ---" />
          </div>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    const result = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        field_hint: 'ชื่อธนาคาร',
        match_text: 'ธนาคารกสิกรไทย',
        timeout_ms: 300,
      },
      {},
      document
    );
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'el_select_no_vue');
  });

  it('applies bank option via Vue only and verifies via input value', async () => {
    const dom = new JSDOM(`<!DOCTYPE html><body>
      <div class="el-dialog" role="dialog">
        <div class="el-form-item">
          <label class="el-form-item__label">ชื่อธนาคาร</label>
          <div class="el-select">
            <input class="el-input__inner" value="" placeholder="--- กรุณาเลือก ---" />
          </div>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">หมายเลขบัญชี</label>
          <div class="el-select is-disabled">
            <input class="el-input__inner" disabled value="" placeholder="--- กรุณาเลือก ---" />
          </div>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;

    const bankSel = document.querySelectorAll('.el-select')[0];
    const bankInput = bankSel.querySelector('input');
    mockElSelectVue(bankSel, ['ธนาคารกรุงไทย', 'ธนาคารกสิกรไทย'], (opt) => {
      bankInput.value = opt.currentLabel;
      bankInput.removeAttribute('placeholder');
      const acct = document.querySelectorAll('.el-select')[1];
      acct.classList.remove('is-disabled');
      acct.querySelector('input').disabled = false;
    });

    const bank = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        field_hint: 'ชื่อธนาคาร',
        match_text: 'ธนาคารกสิกรไทย',
        timeout_ms: 2000,
      },
      {},
      document
    );
    assert.equal(bank.ok, true, JSON.stringify(bank));
    assert.equal(bank.via_vue, true);
    assert.match(document.querySelector('.el-input__inner').value, /กสิกรไทย/);

    // Account el-select now enabled but has a Vue with no numeric options yet.
    mockElSelectVue(document.querySelectorAll('.el-select')[1], [], null);
    const acct = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        field_hint: 'หมายเลขบัญชี',
        fallback_match_pattern: '^[0-9]{8,}$',
        timeout_ms: 500,
      },
      {},
      document
    );
    // Account has no numeric options yet — must not be select_control_not_found / select_disabled.
    assert.notEqual(acct.reason, 'select_control_not_found');
    assert.notEqual(acct.reason, 'select_disabled');
  });

  it('refuses account step with bank_not_selected when bank still shows placeholder', async () => {
    const dom = new JSDOM(`<!DOCTYPE html><body>
      <div class="el-dialog" role="dialog">
        <div class="el-form-item">
          <label class="el-form-item__label">ชื่อธนาคาร</label>
          <div class="el-select">
            <input class="el-input__inner" value="" placeholder="--- กรุณาเลือก ---" />
          </div>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">หมายเลขบัญชี</label>
          <div class="el-select">
            <input class="el-input__inner" value="" placeholder="--- กรุณาเลือก ---" />
          </div>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    mockElSelectVue(document.querySelectorAll('.el-select')[1], [], null);
    const acct = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        field_hint: 'หมายเลขบัญชี',
        fallback_match_pattern: '^[0-9]{8,}$',
        timeout_ms: 300,
      },
      {},
      document
    );
    assert.equal(acct.ok, false);
    assert.equal(acct.reason, 'bank_not_selected');
  });
});

describe('BootstrapVue native <select> close-job (real Jinbao structure)', () => {
  function makeDom(extraFilterHtml) {
    // Mirrors manage.jinbao356.com: 11 native selects; transfer form in a .card, labels in <legend>.
    return new JSDOM(`<!DOCTYPE html><body>
      <!-- top page filters (must NOT be picked) -->
      <fieldset class="form-group"><legend>บัญชีถอน</legend><div>
        <select id="filter-acc"><option value="">--- ทั้งหมด ---</option><option value="p1">12payme</option></select>
      </div></fieldset>
      <fieldset class="form-group"><legend>ธนาคาร</legend><div>
        <select id="filter-bank">
          <option value="">--- ทั้งหมด ---</option>
          <option value="x1">ธนาคารกรุงไทย</option>
          <option value="x2">ธนาคารกสิกรไทย</option>
          <option value="x3">ธนาคารไทยพาณิชย์</option>
        </select>
      </div></fieldset>
      ${extraFilterHtml || ''}

      <div class="card p-4 mt-5" data-v-993c85a0>
        <div class="d-flex"><h5>โอนเงินทางบัญชี</h5></div>
        <fieldset class="form-group col-md-6"><legend class="col-form-label">สถานะการถอน</legend><div>
          <select id="status" class="custom-select">
            <option disabled value="">--- กรุณาเลือก ---</option>
            <option value="success">สำเร็จ</option>
            <option value="fail">ไม่สำเร็จ</option>
          </select>
        </div></fieldset>
        <fieldset class="form-group col-md-6"><legend class="col-form-label">ชื่อธนาคาร</legend><div>
          <select id="bank" class="custom-select">
            <option disabled value="">--- กรุณาเลือก ---</option>
            <option value="b1">ธนาคารกรุงไทย</option>
            <option value="b2">ธนาคารออมสิน</option>
            <option value="b3">ธนาคารกสิกรไทย</option>
            <option value="b4">ธนาคารไทยพาณิชย์</option>
          </select>
        </div></fieldset>
        <fieldset class="form-group col-md-6"><legend class="col-form-label">หมายเลขบัญชี</legend><div>
          <select id="account" class="custom-select" disabled>
            <option disabled value="">--- กรุณาเลือก ---</option>
          </select>
        </div></fieldset>
      </div>

      <script>
        (function () {
          var bank = document.getElementById('bank');
          var acct = document.getElementById('account');
          bank.addEventListener('change', function () {
            if (bank.value) {
              acct.disabled = false;
              // First option is a decoy; correct one ends in 7476 (slip "จาก" account).
              acct.innerHTML =
                '<option disabled value="">--- กรุณาเลือก ---</option>' +
                '<option value="a1">4251526900</option>' +
                '<option value="a2">1234567476</option>';
            }
          });
        })();
      </script>
    </body>`, { runScripts: 'dangerously' });
  }

  it('selects the modal bank <select>, not the top filter, and enables the account', async () => {
    const dom = makeDom();
    global.document = dom.window.document;

    const bank = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 2000,
      },
      { slip: { bank_name_th: 'ธนาคารกสิกรไทย' } },
      document
    );
    assert.equal(bank.ok, true, JSON.stringify(bank));
    assert.equal(document.getElementById('bank').value, 'b3');
    // Top filter bank must remain untouched.
    assert.equal(document.getElementById('filter-bank').value, '');
    // Bank change enabled the account select.
    assert.equal(document.getElementById('account').disabled, false);

    const acct = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        value_from: 'slip.sender_account_last4',
        timeout_ms: 2000,
      },
      { slip: { sender_account_last4: '7476' } },
      document
    );
    assert.equal(acct.ok, true, JSON.stringify(acct));
    // Must pick the account ending 7476 (a2), NOT the first option (a1).
    assert.equal(document.getElementById('account').value, 'a2');
  });

  it('refuses to guess when >1 account shares the same last 4 (account_ambiguous)', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 2000,
      },
      { slip: { bank_name_th: 'ธนาคารไทยพาณิชย์' } },
      document
    );
    const acct = document.getElementById('account');
    // Second account also ends in 7476 → ambiguous, must not auto-pick.
    acct.insertAdjacentHTML('beforeend', '<option value="a3">9999997476</option>');
    const res = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        value_from: 'slip.sender_account_last4',
        timeout_ms: 1000,
      },
      { slip: { sender_account_last4: '7476' } },
      document
    );
    assert.equal(res.ok, false, JSON.stringify(res));
    assert.equal(res.reason, 'account_ambiguous', JSON.stringify(res));
    // Must not have picked either of the ambiguous 7476 accounts.
    assert.ok(acct.value !== 'a2' && acct.value !== 'a3', 'must not pick a 7476 account');
  });

  it('picks the uniquely-matching account among 4 distinct accounts (no account_ambiguous)', async () => {
    // Reproduces the live screenshot: dropdown had 4 distinct accounts and everything
    // before this step filled fine. A unique slip last-4 must be picked, not blocked.
    const dom = makeDom();
    global.document = dom.window.document;
    await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 2000,
      },
      { slip: { bank_name_th: 'ธนาคารกสิกรไทย' } },
      document
    );
    const acct = document.getElementById('account');
    acct.innerHTML =
      '<option disabled value="">--- กรุณาเลือก ---</option>' +
      '<option value="a1">4251526900</option>' +
      '<option value="a2">4097034535</option>' +
      '<option value="a3">3014993023</option>' +
      '<option value="a4">5042827476</option>';
    const res = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        value_from: 'slip.sender_account_last4',
        timeout_ms: 1000,
      },
      { slip: { sender_account_last4: '7476' } },
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.equal(acct.value, 'a4', 'unique last-4 7476 must map to 5042827476');
  });

  it('does NOT flag account_ambiguous when a broad fallback matched all 8+ digit options', async () => {
    // No slip account/mask at all → fallback ^[0-9]{8,}$ matches every account.
    // That must fail missing_select_value, never account_ambiguous.
    const dom = makeDom();
    global.document = dom.window.document;
    await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 2000,
      },
      { slip: { bank_name_th: 'ธนาคารกสิกรไทย' } },
      document
    );
    const acct = document.getElementById('account');
    acct.innerHTML =
      '<option disabled value="">--- กรุณาเลือก ---</option>' +
      '<option value="a1">4251526900</option>' +
      '<option value="a2">4097034535</option>' +
      '<option value="a3">3014993023</option>' +
      '<option value="a4">5042827476</option>';
    const res = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        fallback_match_pattern: '^[0-9]{8,}$',
        timeout_ms: 1000,
      },
      { slip: {} },
      document
    );
    assert.equal(res.ok, false, JSON.stringify(res));
    assert.notEqual(res.reason, 'account_ambiguous', JSON.stringify(res));
    assert.equal(res.reason, 'missing_select_value', JSON.stringify(res));
  });

  it('fails option_not_found (not account_ambiguous) when the slip last-4 matches none', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 2000,
      },
      { slip: { bank_name_th: 'ธนาคารกสิกรไทย' } },
      document
    );
    const acct = document.getElementById('account');
    acct.innerHTML =
      '<option disabled value="">--- กรุณาเลือก ---</option>' +
      '<option value="a1">4251526900</option>' +
      '<option value="a2">4097034535</option>' +
      '<option value="a3">3014993023</option>' +
      '<option value="a4">5042827476</option>';
    const res = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        value_from: 'slip.sender_account_last4',
        fallback_match_pattern: '^[0-9]{8,}$',
        timeout_ms: 1000,
      },
      { slip: { sender_account_last4: '0001' } },
      document
    );
    assert.equal(res.ok, false, JSON.stringify(res));
    assert.notEqual(res.reason, 'account_ambiguous', JSON.stringify(res));
    assert.equal(res.reason, 'option_not_found', JSON.stringify(res));
  });

  it('KBANK: masked template picks the position-correct account (last4 alone would fail)', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 2000,
      },
      { slip: { bank_name_th: 'ธนาคารกสิกรไทย' } },
      document
    );
    const acct = document.getElementById('account');
    // Slip shows "xxx-x-x0758-x" → tail digit is masked. A naive last-4="0758"
    // would wrongly match the decoy that literally ends in 0758.
    acct.innerHTML =
      '<option disabled value="">--- กรุณาเลือก ---</option>' +
      '<option value="decoy">9999990758</option>' + // ends 0758 (last4 trap)
      '<option value="real">1234507589</option>'; // 0758 then hidden 9
    const res = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        value_from_masked: 'slip.sender_account_masked',
        value_from: 'slip.sender_account_last4',
        timeout_ms: 1000,
      },
      { slip: { sender_account_masked: 'xxxxx0758x', sender_account_last4: '0758' } },
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.equal(acct.value, 'real', 'mask template must beat the last-4 decoy');
  });

  it('BBL: matches with only 3 visible tail digits via mask prefix', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 2000,
      },
      { slip: { bank_name_th: 'ธนาคารกสิกรไทย' } },
      document
    );
    const acct = document.getElementById('account');
    acct.innerHTML =
      '<option disabled value="">--- กรุณาเลือก ---</option>' +
      '<option value="decoy">0000000518</option>' + // ends 518 but wrong prefix
      '<option value="real">5840123518</option>'; // 5840...518
    const res = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        value_from_masked: 'slip.sender_account_masked',
        value_from: 'slip.sender_account_last4',
        timeout_ms: 1000,
      },
      { slip: { sender_account_masked: '5840xxx518', sender_account_last4: '0518' } },
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.equal(acct.value, 'real', 'mask prefix 5840...518 must select correctly');
  });

  it('matches account by last 4 even when slip provides a full account number', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 2000,
      },
      { slip: { bank_name_th: 'ธนาคารกสิกรไทย' } },
      document
    );
    const acct = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        value_from: 'slip.sender_account',
        timeout_ms: 2000,
      },
      { slip: { sender_account: 'xxx-xxx747-6' } },
      document
    );
    assert.equal(acct.ok, true, JSON.stringify(acct));
    assert.equal(document.getElementById('account').value, 'a2');
  });

  it('prefers modal ชื่อธนาคาร over page filter ชื่อธนาคารของสมาชิก', async () => {
    const memberFilter = `
      <fieldset class="form-group"><legend>ชื่อธนาคารของสมาชิก</legend><div>
        <select id="member-bank-filter">
          <option value="">--- ทั้งหมด ---</option>
          <option value="m1" selected>ธนาคารไทยพาณิชย์</option>
        </select>
      </div></fieldset>`;
    // Wide wrapper so a naive first-match would hit the member filter first.
    const dom = makeDom(memberFilter);
    global.document = dom.window.document;

    const bank = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank',
        timeout_ms: 3000,
      },
      { slip: { bank: 'SCB' } },
      document
    );
    assert.equal(bank.ok, true, JSON.stringify(bank));
    assert.equal(document.getElementById('bank').value, 'b4');
    // Member filter must stay on its pre-selected value — we must not retarget it.
    assert.equal(document.getElementById('member-bank-filter').value, 'm1');
    assert.equal(document.getElementById('account').disabled, false);
  });

  it('fails bank_not_applied when bank value sets but account never unlocks', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    // Remove the change listener population by replacing the bank node (clone has no listeners).
    const old = document.getElementById('bank');
    const clone = old.cloneNode(true);
    old.parentNode.replaceChild(clone, old);
    clone.id = 'bank';

    const bank = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank_name_th',
        timeout_ms: 800,
      },
      { slip: { bank_name_th: 'ธนาคารไทยพาณิชย์' } },
      document
    );
    assert.equal(bank.ok, false, JSON.stringify(bank));
    assert.equal(bank.reason, 'bank_not_applied', JSON.stringify(bank));
    assert.equal(document.getElementById('account').disabled, true);
  });

  it('maps slip bank code SCB → ธนาคารไทยพาณิชย์', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    const bank = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'ชื่อธนาคาร',
        value_from: 'slip.bank',
        timeout_ms: 2000,
      },
      { slip: { bank: 'SCB' } },
      document
    );
    assert.equal(bank.ok, true, JSON.stringify(bank));
    assert.equal(document.getElementById('bank').value, 'b4');
  });

  it('refuses account step when bank still on placeholder', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    // Bank stays on placeholder; manually enable account (simulate stale/loaded state).
    document.getElementById('bank').value = '';
    const acct = document.getElementById('account');
    acct.disabled = false;
    acct.innerHTML =
      '<option disabled value="">--- กรุณาเลือก ---</option><option value="a1">2262610449</option>';
    const res = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'หมายเลขบัญชี',
        fallback_match_pattern: '^[0-9]{8,}$',
        timeout_ms: 500,
      },
      {},
      document
    );
    assert.equal(res.ok, false);
    assert.equal(res.reason, 'bank_not_selected');
  });

  it('sets สถานะการถอน = สำเร็จ on the modal status select', async () => {
    const dom = makeDom();
    global.document = dom.window.document;
    const res = await selectOption(
      {
        action: 'select_option',
        scope: 'popup',
        scope_text: 'โอนเงินทางบัญชี',
        field_hint: 'สถานะการถอน',
        match_text: 'สำเร็จ',
        timeout_ms: 2000,
      },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.equal(document.getElementById('status').value, 'success');
  });
});

describe('click บันทึก when multiple matches exist', () => {
  it('picks the modal footer Save button, not a page-level duplicate', () => {
    const dom = new JSDOM(`<!doctype html><body>
      <header><button type="button">บันทึกตัวกรอง</button></header>
      <div class="modal" role="dialog">
        <div>โอนเงินทางบัญชี</div>
        <div class="modal-footer">
          <button type="button" class="btn">ยกเลิก</button>
          <button type="button" class="btn btn-primary" id="save-real">บันทึก</button>
        </div>
      </div>
      <button type="button">บันทึก</button>
    </body>`);
    global.document = dom.window.document;
    const target = findStepTarget(
      {
        action: 'click',
        scope: 'popup',
        match_text: 'บันทึก',
        nth_fallback: 'last',
        selector_hints: ['button.btn-primary', 'button.btn', 'button'],
      },
      {},
      document
    );
    assert.ok(target, 'must find a Save button');
    assert.equal(target.id, 'save-real');
  });
});

describe('dismiss_dialog closes Element UI MessageBox', () => {
  it('clicks ตกลง and waits until the box is gone', async () => {
    const dom = new JSDOM(`<!doctype html><body>
      <div class="el-message-box__wrapper" id="box">
        <div class="el-message-box">
          <div class="el-message-box__title">สำเร็จ</div>
          <div class="el-message-box__content">บันทึก รายการถอน สำเร็จ</div>
          <div class="el-message-box__btns">
            <button type="button" class="el-button el-button--primary" id="ok-btn">ตกลง</button>
          </div>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    const box = document.getElementById('box');
    const btn = document.getElementById('ok-btn');
    btn.addEventListener('click', () => {
      box.hidden = true;
      box.style.display = 'none';
    });
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง', timeout_ms: 2000 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.equal(box.hidden, true);
  });

  it('finds ตกลง by success text even without el-message-box classes (no already_gone false OK)', async () => {
    // Live Jinbao: BootstrapVue card form + success popup that may lack Element UI classes.
    // Old logic returned already_gone:true after 400ms without clicking.
    const dom = new JSDOM(`<!doctype html><body>
      <div class="card">โอนเงินทางบัญชี สถานะ สำเร็จ <button type="button">บันทึก</button></div>
      <div id="success-pop">
        <div>สำเร็จ</div>
        <div>บันทึก รายการถอน สำเร็จ</div>
        <button type="button" class="btn btn-primary" id="ok-btn">ตกลง</button>
      </div>
    </body>`);
    global.document = dom.window.document;
    let clicks = 0;
    const pop = document.getElementById('success-pop');
    document.getElementById('ok-btn').addEventListener('click', () => {
      clicks += 1;
      if (pop && pop.parentNode) pop.remove();
    });
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง|OK', timeout_ms: 2500 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.ok(clicks >= 1, 'must click ตกลง');
    assert.equal(!!document.getElementById('success-pop'), false);
    assert.notEqual(res.already_gone, true);
  });

  it('prefers MessageBox ตกลง over บันทึก in the underlying withdrawal dialog', async () => {
    const dom = new JSDOM(`<!doctype html><body>
      <div class="el-dialog" role="dialog">
        <div>โอนเงินทางบัญชี สถานะการถอน สำเร็จ</div>
        <button type="button" class="el-button el-button--primary" id="save-btn">บันทึก</button>
      </div>
      <div class="v-custom-alert" id="box">
        <div>สำเร็จ</div>
        <p>บันทึก รายการถอน สำเร็จ</p>
        <button type="button" id="ok-btn">ตกลง</button>
      </div>
    </body>`);
    global.document = dom.window.document;
    let saveClicks = 0;
    let okClicks = 0;
    document.getElementById('save-btn').addEventListener('click', () => {
      saveClicks += 1;
    });
    document.getElementById('ok-btn').addEventListener('click', () => {
      okClicks += 1;
      const el = document.getElementById('box');
      if (el) el.remove();
    });
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง|OK', timeout_ms: 2500 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.ok(okClicks >= 1, 'must click ตกลง');
    assert.equal(saveClicks, 0, 'must not click บันทึก again');
  });

  it('auto-accepts window.alert triggered by the ตกลง handler', async () => {
    const dom = new JSDOM(`<!doctype html><body>
      <div id="box">
        <div>บันทึก รายการถอน สำเร็จ</div>
        <button type="button" id="ok-btn">ตกลง</button>
      </div>
    </body>`, { beforeParse(window) {
      // jsdom alert stub — would throw if dismiss did not override.
      window.alert = () => {
        throw new Error('native alert would block — override missing');
      };
    }});
    global.document = dom.window.document;
    let alertCalls = 0;
    document.getElementById('ok-btn').addEventListener('click', () => {
      // Page calls alert() then removes modal / reloads.
      dom.window.alert('done');
      alertCalls += 1;
      const el = document.getElementById('box');
      if (el) el.remove();
    });
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง', timeout_ms: 2500 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.ok(alertCalls >= 1, 'alert handler must run');
  });

  it('clicks SweetAlert2 .swal2-confirm (Jinbao live DOM) and does not already_gone early', async () => {
    // Exact structure from manage.jinbao356.com DevTools.
    const dom = new JSDOM(`<!doctype html><body>
      <div class="card">โอนเงินทางบัญชี <button type="button">บันทึก</button></div>
      <div class="swal2-container swal2-center swal2-backdrop-show" id="swal-root">
        <div class="swal2-popup swal2-modal swal2-icon-success swal2-show modal" role="dialog">
          <div class="swal2-icon swal2-success swal2-icon-show"></div>
          <h2 class="swal2-title">สำเร็จ</h2>
          <div class="swal2-html-container">บันทึก รายการถอน สำเร็จ</div>
          <div class="swal2-actions">
            <button type="button" class="swal2-confirm swal2-styled" id="ok-btn" style="display: inline-block;">ตกลง</button>
            <button type="button" class="swal2-deny swal2-styled" style="display: none;">No</button>
            <button type="button" class="swal2-cancel swal2-styled" style="display: none;">Cancel</button>
          </div>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    let clicks = 0;
    document.getElementById('ok-btn').addEventListener('click', () => {
      clicks += 1;
      const root = document.getElementById('swal-root');
      if (root) root.remove();
    });
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง|OK', timeout_ms: 3000 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.ok(clicks >= 1, 'must click .swal2-confirm');
    assert.equal(!!document.getElementById('swal-root'), false);
    assert.notEqual(res.already_gone, true);
  });

  it('waits for late SweetAlert2 instead of already_gone when success toast text flickers', async () => {
    const dom = new JSDOM(`<!doctype html><body>
      <div class="card">โอนเงินทางบัญชี <button type="button">บันทึก</button></div>
      <div id="toast">บันทึก รายการถอน สำเร็จ</div>
    </body>`);
    global.document = dom.window.document;
    let clicks = 0;

    // Toast clears quickly (old bug: already_gone), then Swal appears.
    setTimeout(() => {
      const toast = document.getElementById('toast');
      if (toast) toast.remove();
    }, 50);
    setTimeout(() => {
      const wrap = document.createElement('div');
      wrap.className = 'swal2-container';
      wrap.id = 'swal-root';
      wrap.innerHTML =
        '<div class="swal2-popup swal2-icon-success">' +
        '<div class="swal2-html-container">บันทึก รายการถอน สำเร็จ</div>' +
        '<div class="swal2-actions">' +
        '<button type="button" class="swal2-confirm swal2-styled" id="ok-btn" style="display:inline-block">ตกลง</button>' +
        '</div></div>';
      document.body.appendChild(wrap);
      document.getElementById('ok-btn').addEventListener('click', () => {
        clicks += 1;
        wrap.remove();
      });
    }, 400);

    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง|OK', timeout_ms: 3000 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.ok(clicks >= 1, 'must wait for Swal then click');
    assert.notEqual(res.already_gone, true);
  });

  it('uses window.Swal.clickConfirm when available', async () => {
    const dom = new JSDOM(`<!doctype html><body>
      <div class="swal2-container" id="swal-root">
        <div class="swal2-popup">
          <div class="swal2-html-container">บันทึก รายการถอน สำเร็จ</div>
          <button type="button" class="swal2-confirm" id="ok-btn">ตกลง</button>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    let apiCalls = 0;
    dom.window.Swal = {
      clickConfirm() {
        apiCalls += 1;
        const root = document.getElementById('swal-root');
        if (root) root.remove();
      },
    };
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง', timeout_ms: 2000 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.ok(apiCalls >= 1, 'must use Swal.clickConfirm');
  });

  it('never returns ok/already_gone while .swal2-container remains in the DOM', async () => {
    // Confirm button exists but clicking it does NOT remove the container (broken v1.0.30 case).
    const dom = new JSDOM(`<!doctype html><body>
      <div class="swal2-container swal2-center swal2-backdrop-show" id="swal-root">
        <div class="swal2-popup swal2-modal swal2-icon-success swal2-show" role="dialog">
          <div class="swal2-html-container">บันทึก รายการถอน สำเร็จ</div>
          <div class="swal2-actions">
            <button type="button" class="swal2-confirm swal2-styled" id="ok-btn" style="display: inline-block;">ตกลง</button>
          </div>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    let clicks = 0;
    // Deliberately no removal — mimics the click not reaching the page's Swal handler.
    document.getElementById('ok-btn').addEventListener('click', () => {
      clicks += 1;
    });
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง|OK', timeout_ms: 700 },
      {},
      document
    );
    assert.equal(res.ok, false, JSON.stringify(res));
    assert.equal(res.reason, 'dismiss_dialog_still_open', JSON.stringify(res));
    assert.notEqual(res.already_gone, true);
    assert.ok(clicks >= 1, 'must have attempted the confirm click');
    assert.ok(document.querySelector('.swal2-container'), 'container must still be present');
  });

  it('closes SweetAlert2 via the injected MAIN-world script (page Swal.clickConfirm)', async () => {
    // runScripts lets the injected page-context <script> actually execute (like the real page).
    const dom = new JSDOM(`<!doctype html><body>
      <div class="swal2-container" id="swal-root">
        <div class="swal2-popup swal2-icon-success">
          <div class="swal2-html-container">บันทึก รายการถอน สำเร็จ</div>
          <div class="swal2-actions">
            <button type="button" class="swal2-confirm swal2-styled" id="ok-btn" style="display: inline-block;">ตกลง</button>
          </div>
        </div>
      </div>
      <script>
        window.__swalConfirmCalls = 0;
        window.Swal = {
          clickConfirm: function () {
            window.__swalConfirmCalls += 1;
            var root = document.getElementById('swal-root');
            if (root) root.remove();
          },
        };
      </script>
    </body>`, { runScripts: 'dangerously' });
    global.document = dom.window.document;
    // The confirm button has NO listener — only Swal.clickConfirm can close the popup.
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง|OK', timeout_ms: 3000 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.equal(res.via, 'swal2', JSON.stringify(res));
    assert.ok(dom.window.__swalConfirmCalls >= 1, 'page Swal.clickConfirm must be invoked');
    assert.equal(!!document.querySelector('.swal2-container'), false);
    assert.notEqual(res.already_gone, true);
  });

  it('clicks ตกลง on BootstrapVue save-confirm modal (ก่อน SweetAlert สำเร็จ)', async () => {
    const dom = new JSDOM(`<!doctype html><body>
      <div class="card">โอนเงินทางบัญชี <button type="button" class="btn btn-primary">บันทึก</button></div>
      <div class="modal show" id="modal-withdraw-transaction" style="display: block;">
        <div class="modal-dialog">
          <div class="modal-content">
            <div class="modal-header"><h5>ยืนยันการถอนรายการ</h5></div>
            <div class="modal-body">คุณแน่ใจใช่ไหมที่จะบันทึกรายการตอนนี้ ?</div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" id="cancel-btn">ยกเลิก</button>
              <button type="button" class="btn btn-primary" id="ok-btn">ตกลง</button>
            </div>
          </div>
        </div>
      </div>
    </body>`);
    global.document = dom.window.document;
    let ok = 0;
    let cancel = 0;
    document.getElementById('cancel-btn').addEventListener('click', () => {
      cancel += 1;
    });
    document.getElementById('ok-btn').addEventListener('click', () => {
      ok += 1;
      const m = document.getElementById('modal-withdraw-transaction');
      if (m) m.remove();
    });
    const res = await dismissMessageBox(
      { action: 'dismiss_dialog', match_text: 'ตกลง|OK', timeout_ms: 2500 },
      {},
      document
    );
    assert.equal(res.ok, true, JSON.stringify(res));
    assert.ok(ok >= 1, 'must click confirm ตกลง');
    assert.equal(cancel, 0, 'must not click ยกเลิก');
  });
});
