"""Pure helpers for approved-order withdraw_notify payloads."""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence


def format_amount_display(amount: Any) -> str:
    if amount is None:
        return ""
    text = str(amount).strip().replace(",", "")
    try:
        return f"{float(text):.2f}"
    except (TypeError, ValueError):
        return str(amount).strip()


def new_orders_since(
    previous: Sequence[Mapping[str, Any]],
    current: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    prev_ids = {str(o.get("order_id") or "").strip() for o in previous}
    prev_ids.discard("")
    out: list[dict[str, Any]] = []
    for o in current:
        oid = str(o.get("order_id") or "").strip()
        if not oid or oid in prev_ids:
            continue
        out.append(dict(o))
    return out


def build_withdraw_notify_payload(
    order: Mapping[str, Any], *, ts: int | None = None
) -> dict[str, Any]:
    return {
        "action": "withdraw_notify",
        "order_id": str(order.get("order_id") or "").strip(),
        "amount": format_amount_display(order.get("amount")),
        "account": str(order.get("account") or "").strip(),
        "bank": str(order.get("bank") or "").strip(),
        "account_name": str(order.get("account_name") or order.get("name") or "").strip(),
        "ts": int(ts if ts is not None else time.time()),
    }


SLIP_STATUS_MESSAGE_TH = {
    "received": "โอนเงินสำเร็จ",
    "processing": "กำลังดำเนินการ — อย่าโอนซ้ำ",
    "done": "สำเร็จ",
    "failed": "ไม่สำเร็จ",
}


def build_slip_status_payload(
    *,
    job_id: str,
    order_id: str,
    amount: Any,
    stage: str,
    reason: str = "",
    message_th: str | None = None,
    ts: int | None = None,
) -> dict[str, Any]:
    stage_s = str(stage or "").strip()
    if message_th is None:
        base = SLIP_STATUS_MESSAGE_TH.get(stage_s, "")
        if stage_s == "failed" and reason:
            message_th = f"{base}: {reason}"
        else:
            message_th = base
    return {
        "action": "slip_status",
        "job_id": str(job_id or "").strip(),
        "order_id": str(order_id or "").strip(),
        "amount": format_amount_display(amount),
        "stage": stage_s,
        "message_th": str(message_th or ""),
        "reason": str(reason or ""),
        "ts": int(ts if ts is not None else time.time()),
    }


def resolve_order_id_for_slip_status(
    data: Mapping[str, Any],
    *,
    pending: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    for key in ("order_id", "orderId"):
        val = str(data.get(key) or "").strip()
        if val and val not in ("-", "None"):
            return val
    amount_keys: set[str] = set()
    for key in ("amount", "matchKey"):
        raw = data.get(key)
        if raw is None or str(raw).strip() in ("", "-", "None"):
            continue
        text = str(raw).strip()
        amount_keys.add(text)
        try:
            amount_keys.add(f"{float(text.replace(',', '')):.2f}")
        except (TypeError, ValueError):
            pass
    for order in pending or ():
        oid = str(order.get("order_id") or "").strip()
        if not oid:
            continue
        oamt = format_amount_display(order.get("amount"))
        if oamt in amount_keys or str(order.get("amount") or "") in amount_keys:
            return oid
    return ""


def should_emit_slip_status(
    prev_key: str | None,
    *,
    job_id: str,
    order_id: str,
    stage: str,
    now: float,
    last_at: float,
) -> bool:
    key = f"{job_id}|{order_id}|{stage}"
    if prev_key == key and (now - last_at) < 2.0:
        return False
    return True
