# Chrome Extension — Open Items (Gate 4 blockers)

Synthetic fixtures and stub API adapters are in place until partner recon is complete.

## BLOCKED for Gate 4

| Item | Status | Notes |
|------|--------|-------|
| Partner permission letter | **BLOCKED** | Written approval that automation may click admin UI on behalf of staff |
| Real customer domain | **BLOCKED** | Replace `admin.example.invalid` in profiles with production admin URL |
| HAR approve endpoint | **BLOCKED** | DevTools recon: click through close-job flow once, capture POST approve endpoint + payload |

## What works with stubs today

- Text-anchor engine tests run against `fixtures/order_list.html`
- Workflow engine tests run against `fixtures/close_job_popup.html`
- API adapter `list_pending` uses recon URL template; `approve` returns stub until HAR is captured

## Unblock checklist

1. Obtain partner permission letter (store path in onboarding record, not in repo)
2. Sanitize and commit real order-list HTML snapshot → update profile + fixtures
3. Record HAR for approve POST → fill `api.approve.url_template` and `payload_template` in profile
4. Re-run Gate 4 manual evidence (dry-run video, live confirm, canary UI change test)
