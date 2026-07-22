# Customer Onboarding — Chrome Extension Site Profile

Repeat this checklist for every new back-office customer. Profiles are pushed from PC via WebSocket; extension behavior is driven entirely by JSON — no extension release required per customer.

## Step 1 — Capture and validate

1. Obtain written partner permission for admin UI automation (see `docs/OPEN_ITEMS.md`).
2. Save a sanitized HTML snapshot of the order / withdrawal list page.
3. Add the snapshot under `fixtures/` and wire engine tests (`npm test` in `pc/chrome-extension`).
4. Author a new profile JSON (`profile_id`, `domain_patterns`, selector hints, keywords).
5. Push profile from PC with **`dry_run: true`** (default).

**Exit criteria:** `node --test tests/engine.test.js` passes against the customer fixture.

## Step 2 — Dry-run observation (1–2 days)

1. Admin uses the real back office with ClipSync paired and extension loaded.
2. Confirm every auto-confirm attempt shows a **red outline** on the correct control (no real clicks).
3. Monitor PC canary / health: `canary_ok` and `logged_in` stay true during the observation window.
4. Fix profile hints if outlines miss or hit wrong rows — re-push profile from PC.

**Exit criteria:** ≥ 10 consecutive dry-run outlines on correct targets (Gate 4 video evidence).

## Step 3 — Live click, manual gate

1. Set **`dry_run: false`** on the profile (popup toggle or PC push).
2. Keep **`auto_confirm.enabled`** false on PC — use debug panel “confirm manually” for each test click.
3. Verify post-click: extension returns **`verified: true`**; PC audit shows confirmed state.
4. Exercise slow-network case (DevTools throttle) — MutationObserver should wait, not fail early.

**Exit criteria:** Live confirm + post-click verify on representative orders without false positives.

## Step 4 — Enable auto-confirm

1. Complete E2E for this customer (mobile slip → PC match → extension confirm).
2. Enable **`auto_confirm.enabled`** on PC for this profile / merchant scope.
3. Document profile version, onboarding date, and permission letter reference (outside repo).

**Exit criteria:** Gate 4 evidence bundle complete for this customer (see plan Gate 4 checklist).

## When API recon is available

If DevTools HAR shows a stable in-session list + approve API:

1. Fill `api.list_pending` and `api.approve` in the profile (approve URL from HAR — not `TODO`).
2. Prefer API path for pending list polling; fall back to DOM scrape if API errors.
3. If approve payload is unclear, keep approve stubbed and use `close_job_workflow` DOM steps instead.

## Related docs

- `docs/OPEN_ITEMS.md` — Gate 4 blockers (permission letter, real domain, HAR approve)
- `fixtures/order_list.html` — synthetic list stub until partner snapshot arrives
- `fixtures/close_job_popup.html` — synthetic multi-step popup stub
