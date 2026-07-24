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
  function makeDom() {
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
              acct.innerHTML =
                '<option disabled value="">--- กรุณาเลือก ---</option>' +
                '<option value="a1">2262610449</option>';
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
        fallback_match_pattern: '^[0-9]{8,}$',
        timeout_ms: 2000,
      },
      {},
      document
    );
    assert.equal(acct.ok, true, JSON.stringify(acct));
    assert.equal(document.getElementById('account').value, 'a1');
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
