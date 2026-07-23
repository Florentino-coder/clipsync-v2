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
          "[class*='modal']",
          "[class*='dialog']",
          "[class*='popup']",
          ".el-dialog",
          ".el-drawer"
        ],
        "timeout_ms": 10000
      },
      {
        "action": "select_option",
        "scope": "popup",
        "match_text": "สำเร็จ"
      },
      {
        "action": "select_option",
        "scope": "popup",
        "field_hint": "ธนาคาร",
        "value_from": "slip.bank_name_th"
      },
      {
        "action": "verify_or_fill",
        "scope": "popup",
        "field_hint": "เลขบัญชี",
        "value_from": "slip.account_number"
      },
      {
        "action": "click",
        "scope": "popup",
        "match_text": "ยืนยัน|บันทึก|ตกลง|Submit"
      },
      {
        "action": "verify_result",
        "indicators": [
          "ปิดงานสำเร็จ",
          "สำเร็จ",
          "success"
        ],
        "timeout_ms": 15000
      }
    ],
    "_notes": [
      "Target page: https://manage.jinbao356.com/withdraw/transaction?tab=1",
      "dry_run=true — extension outlines clicks only, does not submit.",
      "Tune selectors after first dry-run on a real withdrawal row.",
      "API list_pending left out until partner XHR/HAR is captured."
    ]
  }
];

globalThis.BUNDLED_SITE_PROFILES = BUNDLED_SITE_PROFILES;
