# Manual GUI Gate 5 — remaining

Automated unit tests cover row formatting, settings form mapping, transport
indicator labels, and audit.jsonl day/type filters. The following Gate 5
checks still need a human with the live Tk UI:

1. Screenshot Slip tab updating in real time with green / yellow / red row badges
   for `auto_confirmed`, `pending_review`, and `rejected`/`confirm_failed`.
2. Change threshold (or auto-confirm) in Settings → Save → inject a slip event
   and confirm behavior changes without restarting (Activity log shows reload).
3. Audit tab "ประวัติ" separates `system` vs `admin_manual` and loads historical
   lines from `%APPDATA%\ClipSync\audit.jsonl`.

How to drive a fake row into the Slip tab without a phone:

```python
app.push_slip_ui_event({
    "ts": "2026-07-22T12:00:00+00:00",
    "bank": "SCB",
    "amount": 100,
    "ref_number": "XX123456",
    "order_id": "ORD-1",
    "transport": "usb",
    "decision": "pending_review",
    "event_id": "demo-1",
})
```

View-slip image popup remains stubbed until USB `slip_fetcher` is wired into
`ClipSyncApp`.
