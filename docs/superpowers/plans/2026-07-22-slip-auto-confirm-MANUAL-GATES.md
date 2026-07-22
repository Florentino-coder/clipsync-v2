# Slip Auto-Confirm — Manual Gates Checklist

Human-only verification before enabling `auto_confirm.enabled` in production.
Attach raw evidence (logs, screenshots, video) for each item.

**Target versions:** Android `0.9.0+21`, PC `0.9.0`, Extension `1.0.0`

---

## Gate 1 — Mobile slip capture + OCR

- [ ] Logcat shows slip event when real bank app (SCB) saves a transfer image
- [ ] OCR table: 20 slips, amount + ref correct ≥ 90%
- [ ] Airplane mode + app data usage: images never leave device automatically
- [ ] `curl "http://<phone-ip>:8790/slips?from=...&to=..." -H "X-Auth: <hmac>"` → 200; no header → 401
- [ ] `flutter test` green (attach raw CI or local output)

## Gate 2 — Transport (USB + relay fallback)

- [ ] USB tether + airplane mode on phone → PC still receives events (screenshot + log)
- [ ] Unplug USB mid-flow → log shows `transport changed usb -> relay`; pending events resend (count `event_id`)
- [ ] 10 slips while plugging/unplugging → PC receives exactly 10, no duplicates

## Gate 3 — PC matcher, bridge, installer

- [ ] `cd pc && pytest -v` green (attach raw output)
- [ ] `netstat -ano | findstr 8765` shows bind `127.0.0.1` only
- [ ] Threshold on/off smoke test with real run (attach log)
- [ ] Slip fetcher pulls dated images over USB (log + image count/size)
- [ ] Extension installer: screenshot `chrome://extensions` + clipboard has extension path

## Gate 4 — Chrome extension + customer backend

- [ ] Wrong bridge token → connection closed, popup `auth_failed`; correct token → `connected`
- [ ] HAR/API adapter fetches pending list via real session (redacted JSON sample)
- [ ] Dry-run workflow on real admin page: every step screenshot + bank/account/amount cross-check
- [ ] Mid-workflow popup close → stops with `failed_step`, no blind continue
- [ ] Engine tests pass on real HTML fixture (attach output)
- [ ] Dry-run video: red highlight on correct button ≥ 10 orders
- [ ] Live confirm video: post-click verify `verified:true`
- [ ] Slow 3G throttle → MutationObserver waits for button, no premature timeout
- [ ] UI change simulation (fixture) → canary alert red on PC, no silent fail
- [ ] Tab idle > 1 hour → confirm still works (SW keepalive)
- [ ] Simulated session expiry → red banner + PC `confirm_result reason:session_expired`
- [ ] Partner written approval for automation on file

## Gate 5 — PC debug UI (Tk)

- [ ] Slip tab updates live with green / yellow / red badges (`auto_confirmed`, `pending_review`, `rejected`/`confirm_failed`)
- [ ] Settings change (threshold / auto-confirm) → Save → behavior changes without restart (Activity log shows reload)
- [ ] Audit tab separates `system` vs `admin_manual` and loads `%APPDATA%\ClipSync\audit.jsonl` history

## Gate L — License (Ed25519, offline-first)

- [ ] `pytest` + `flutter test`: valid / expired / tampered / wrong-device on both platforms
- [ ] Offline grace period works, then locks (log timestamps)
- [ ] Refresh interval respected (network log, not too frequent)
- [ ] Device in `revoked_devices.json` → locked on next refresh
- [ ] Docs note: client-side check ≠ tamper-proof; server revocation is the backstop

---

## E2E — 20 scenarios (precondition: real phone + SCB, USB tether, Chrome admin logged in, extension paired)

| # | Scenario | Expected | Evidence |
|---|----------|----------|----------|
| 1 | Low amount, exact match | Auto-confirm ≤ ~5s, green badge | Video + audit `confirmed_by:system` |
| 2 | Amount over threshold (review on) | No confirm, yellow `pending_review` | Screenshot + audit |
| 3 | Review off, high amount | Auto-confirm | Log before/after config change |
| 4 | Low OCR confidence | No confirm, `pending_review` | Confidence in log |
| 5 | Duplicate ref replay | Rejected, audit duplicate, no double-click | Two audit lines |
| 6 | USB unplug mid-send | Relay within ≤15s, no duplicate | Transport log + event_id count |
| 7 | Airplane + USB + test inject | Event reaches PC | Airplane screenshot + PC log |
| 8 | Admin session expired | Red banner, `session_expired`, no silent fail | Screenshot + PC log |
| 9 | Fetch slips by date | Images in range only, ≤50 compressed | Count + payload size log |
| 10 | Admin override | Manual fix/reject works, audit `overridden`/`admin_manual` | Audit lines |
| 11 | Unknown bank parser | `bank: UNKNOWN`, `pending_review` | Log |
| 12 | Two orders same amount | `ambiguous` → `pending_review`, no auto-confirm | Audit |
| 13 | PC restart with unsent mobile events | Resend + PC dedupe | seen_events log |
| 14 | Relay down, no USB | UI disconnected, outbox drains when back | Log |
| 15 | Customer UI change | Canary alert, no bogus confirm | Screenshot |
| 16 | Slow 3G on confirm | Observer waits, click succeeds | Log |
| 17 | Dry-run mode | Red frame, no real click, `reason:dry_run` | Screenshot |
| 18 | Click but page unchanged | `clicked_but_unverified`, red badge | Log |
| 19 | Old mobile client + new relay message | No crash, clipboard sync OK | Log |
| 20 | Regression: clipboard sync | PC↔phone clip still works | Runtime test |

---

## Release steps (human)

- [ ] Merge `feat/slip-auto-confirm` → `master` (CI `unit-tests` + builds must pass)
- [ ] Tag release (e.g. `v0.9.0`) — **not automated in Stage 6**
- [ ] Install APK + PC installer on farm devices; smoke clipboard + slip inject
- [ ] Keep `auto_confirm.enabled: false` until E2E #1–20 + customer dry-run pass
