# Chrome Extension — Open Items (Gate 4 blockers)

Synthetic fixtures and stub API adapters are in place until partner recon is complete.

## BLOCKED for Gate 4

| Item | Status | Notes |
|------|--------|-------|
| Partner permission letter | **BLOCKED** | Written approval that automation may click admin UI on behalf of staff |
| Real customer domain | **BLOCKED** | Replace `admin.example.invalid` in profiles with production admin URL |
| HAR approve endpoint | **BLOCKED** | **Open Item:** partner HAR required for `api.approve` — capture POST URL + payload/headers (CSRF) once via DevTools while closing one real job |

## What works with stubs today

- Text-anchor engine tests run against `fixtures/order_list.html`
- Workflow engine tests run against `fixtures/close_job_popup.html`
- API adapter `list_pending` fetches with `credentials: 'include'` using recon URL template
- `api.approve` is stubbed (`TODO(HAR)` / `approve_endpoint_todo`) until partner HAR arrives — DOM `close_job_workflow` is the fallback

## Unblock checklist

1. Obtain partner permission letter (store path in onboarding record, not in repo)
2. Sanitize and commit real order-list HTML snapshot → update profile + fixtures
3. Record HAR for approve POST → fill `api.approve.url_template` and `payload_template` in profile
4. Re-run Gate 4 manual evidence (dry-run video, live confirm, canary UI change test)
