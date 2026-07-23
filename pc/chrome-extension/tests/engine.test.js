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
  deepFindByText,
  findRow,
  findConfirmButton,
  checkCanary,
  scrapePendingOrders,
  apiAdapter,
  runWorkflow,
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
