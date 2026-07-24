// Auto-bundled default Site Profiles — seeded when storage is empty.
const BUNDLED_SITE_PROFILES = [
  {
    "profile_id": "jinbao356_v1",
    "domain_patterns": [
      "https://manage.jinbao356.com/*"
    ],
    "order_page_url_hint": "/withdraw/transaction",
    "row_selector_hints": [
      "tr.el-table__row",
      ".el-table__body tr",
      "tbody tr",
      "tr"
    ],
    "confirm_keywords": [
      "ยืนยัน",
      "confirm",
      "อนุมัติ",
      "approve",
      "สำเร็จ",
      "ปิดงาน"
    ],
    "already_confirmed_indicators": [
      "ยืนยันแล้ว",
      "confirmed",
      "สำเร็จแล้ว",
      "approved",
      "ปิดงานแล้ว"
    ],
    "logout_indicators": [
      "form[action*='login']",
      "input[type='password']",
      "[class*='login']"
    ],
    "order_list_canary_selector": "table, .el-table, [class*='order-list'], [class*='withdraw'], [class*='transaction']",
    "uses_iframe": false,
    "dry_run": true,
    "post_click_verify_timeout_ms": 15000,
    "click_wait_max_ms": 30000,
    "close_job_workflow": [
      {
        "action": "click",
        "target": {
          "in_row": true,
          "selector_hints": [
            "[class*='eye']",
            "[class*='view']",
            "svg",
            "button",
            "a",
            ".eye-btn"
          ],
          "nth_fallback": "last"
        }
      },
      {
        "action": "wait_for",
        "selector_hints": [
          ".el-dialog",
          "[class*='modal']",
          "[class*='dialog']",
          "[role='dialog']"
        ],
        "timeout_ms": 10000
      },
      {
        "action": "scroll_into_view",
        "scope": "popup",
        "match_text": "โอนเงินทางบัญชี|โอนเงินเรียบร้อยแล้ว|สถานะการถอน|เริ่มการถอนออโต้"
      },
      {
        "action": "wait_for",
        "scope": "popup",
        "match_text": "โอนเงินเรียบร้อยแล้ว|สถานะการถอน",
        "timeout_ms": 5000
      },
      {
        "action": "check",
        "scope": "popup",
        "match_text": "โอนเงินเรียบร้อยแล้ว"
      },
      {
        "action": "select_option",
        "scope": "popup",
        "field_hint": "สถานะการถอน",
        "match_text": "สำเร็จ",
        "timeout_ms": 5000
      },
      {
        "action": "select_option",
        "scope": "popup",
        "field_hint": "ชื่อธนาคาร",
        "value_from": "slip.bank_name_th",
        "value_from_fallbacks": [
          "slip.bank_name",
          "slip.bank"
        ],
        "timeout_ms": 8000
      },
      {
        "action": "wait_for",
        "scope": "popup",
        "match_text": "ธนาคารไทยพาณิชย์|ธนาคารกสิกรไทย|ธนาคารกรุงไทย|ธนาคารออมสิน|ธนาคารกรุงเทพ|ธนาคารทหารไทย",
        "timeout_ms": 5000
      },
      {
        "action": "select_option",
        "scope": "popup",
        "field_hint": "หมายเลขบัญชี",
        "fallback_match_pattern": "^[0-9]{8,}$",
        "timeout_ms": 8000
      },
      {
        "action": "click",
        "scope": "popup",
        "match_text": "บันทึก"
      },
      {
        "action": "wait_for",
        "selector_hints": [
          ".el-message-box",
          ".el-dialog",
          "[class*='message-box']",
          "[role='dialog']"
        ],
        "timeout_ms": 8000
      },
      {
        "action": "click",
        "scope": "popup",
        "match_text": "ตกลง"
      },
      {
        "action": "verify_result",
        "indicators": [
          "บันทึก รายการถอน สำเร็จ",
          "รายการถอน สำเร็จ",
          "ปิดงานสำเร็จ"
        ],
        "timeout_ms": 15000
      }
    ],
    "_notes": [
      "Target page: https://manage.jinbao356.com/withdraw/transaction",
      "Close-job form is below the fold in the withdrawal modal — scroll_into_view first.",
      "Shop payout account is selected by first numeric หมายเลขบัญชี option after bank.",
      "Keep dry_run true until live clicks are verified end-to-end."
    ]
  }
];

globalThis.BUNDLED_SITE_PROFILES = BUNDLED_SITE_PROFILES;
