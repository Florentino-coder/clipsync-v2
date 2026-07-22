"""Slip event orchestrator: dedupe → match → auto-confirm → audit."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, MutableMapping, Optional

from clipsync.audit import append_audit
from clipsync.config import user_data_dir
from clipsync.matcher import load_used_refs, match_order, save_used_refs, should_auto_confirm
from clipsync.seen_events import SeenEvents, default_seen_events_path

logger = logging.getLogger(__name__)

CONFIRM_TIMEOUT_DEFAULT = 15.0

SendAckCallback = Callable[[str], Awaitable[None] | None]


def default_used_refs_path() -> Path:
    return user_data_dir() / "used_refs.json"


def _verify_slip_payload_sig(
    shared_secret: str, payload: Mapping[str, Any], sig: str
) -> bool:
    if not sig:
        return False
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    expected = hmac.new(
        shared_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# Backwards-compatible helpers — same on-disk format as SeenEvents.
def load_seen_events(path: Path | str) -> set[str]:
    return SeenEvents(path=path).event_ids


def save_seen_events(event_ids: set[str], path: Path | str) -> None:
    """Persist as ``{"event_ids": [...]}`` (SeenEvents format)."""
    seen = SeenEvents(path=path)
    seen.replace_all(event_ids)


def _normalize_order(order: Mapping[str, Any]) -> dict[str, Any]:
    order_id = order.get("order_id")
    if order_id is None:
        order_id = order.get("orderId")
    last4 = order.get("account_last4")
    if last4 is None:
        last4 = order.get("accountLast4")
    if last4 is None:
        acct = order.get("member_bank_account") or order.get("account") or ""
        last4 = str(acct)[-4:] if acct else ""
    return {
        "order_id": str(order_id) if order_id is not None else "",
        "amount": order.get("amount"),
        "account_last4": str(last4) if last4 is not None else "",
    }


class SlipOrchestrator:
    """Wires slip events through matcher → chrome bridge → audit trail."""

    def __init__(
        self,
        cfg: Mapping[str, Any],
        *,
        chrome_bridge: Any,
        shared_secret: str,
        audit_path: Optional[Path | str] = None,
        seen_events_path: Optional[Path | str] = None,
        used_refs_path: Optional[Path | str] = None,
        confirm_timeout: float = CONFIRM_TIMEOUT_DEFAULT,
        send_ack: Optional[SendAckCallback] = None,
        seen_events: Optional[SeenEvents] = None,
    ) -> None:
        self._cfg: MutableMapping[str, Any] = dict(cfg)
        self._bridge = chrome_bridge
        self._shared_secret = shared_secret
        self._audit_path = Path(audit_path) if audit_path is not None else None
        self._used_refs_path = (
            Path(used_refs_path) if used_refs_path is not None else default_used_refs_path()
        )
        self._confirm_timeout = float(confirm_timeout)
        self._send_ack = send_ack

        if seen_events is not None:
            self._seen = seen_events
        else:
            path = (
                Path(seen_events_path)
                if seen_events_path is not None
                else default_seen_events_path()
            )
            self._seen = SeenEvents(path=path)

        self._used_refs: set[str] = load_used_refs(self._used_refs_path)
        self._pending_orders: list[dict[str, Any]] = []
        self._confirm_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def update_config(self, cfg: Mapping[str, Any]) -> None:
        self._cfg = dict(cfg)

    def set_send_ack(self, send_ack: Optional[SendAckCallback]) -> None:
        """Attach/replace the phone ack callback (USB or relay transport)."""
        self._send_ack = send_ack

    def on_pending_orders(self, data: Mapping[str, Any] | None) -> None:
        """Chrome-bridge callback — must never raise (keeps WS alive)."""
        try:
            if not isinstance(data, Mapping):
                return
            orders = data.get("orders") or []
            if not isinstance(orders, list):
                return
            self._pending_orders = [
                _normalize_order(o) for o in orders if isinstance(o, Mapping)
            ]
        except Exception:
            logger.exception("on_pending_orders failed")

    def on_confirm_result(self, data: Mapping[str, Any] | None) -> None:
        """Chrome-bridge callback — must never raise (keeps WS alive)."""
        try:
            if not isinstance(data, Mapping):
                return
            order_id = data.get("orderId")
            if order_id is None:
                order_id = data.get("order_id")
            if order_id is None:
                return
            key = str(order_id)
            fut = self._confirm_waiters.get(key)
            if fut is not None and not fut.done():
                fut.set_result(dict(data))
        except Exception:
            logger.exception("on_confirm_result failed")

    async def _emit_ack(self, event_id: str) -> None:
        if not event_id or self._send_ack is None:
            return
        result = self._send_ack(event_id)
        if asyncio.iscoroutine(result):
            await result

    async def handle_slip_event(
        self,
        event: Mapping[str, Any],
        *,
        source: str = "usb",
        sig: Optional[str] = None,
    ) -> dict[str, Any]:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            return self._audit_and_return(event, None, "rejected", confirmed_by=None)

        if self._seen.is_duplicate(event_id):
            await self._emit_ack(event_id)
            return {"decision": "duplicate", "event_id": event_id, "order_id": None}

        if source == "relay":
            if not _verify_slip_payload_sig(self._shared_secret, event, sig or ""):
                # Still ack so the phone stops resending a rejected payload.
                result = self._audit_and_return(event, None, "rejected", confirmed_by=None)
                await self._emit_ack(event_id)
                return result

        self._seen.mark(event_id)

        matched = match_order(
            event, self._pending_orders, self._cfg, used_refs=self._used_refs
        )

        if not should_auto_confirm(event, matched, self._cfg):
            result = self._audit_and_return(
                event, matched, "pending_review", confirmed_by=None
            )
            await self._emit_ack(event_id)
            return result

        assert matched is not None
        order_id = str(matched["order_id"])
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._confirm_waiters[order_id] = fut
        try:
            await self._bridge.push_confirm_order(order_id)
            try:
                result = await asyncio.wait_for(fut, timeout=self._confirm_timeout)
            except asyncio.TimeoutError:
                out = self._audit_and_return(
                    event, matched, "confirm_failed", confirmed_by=None
                )
                await self._emit_ack(event_id)
                return out

            if result.get("ok"):
                ref = event.get("ref_number")
                if ref is not None:
                    self._used_refs.add(str(ref))
                    save_used_refs(self._used_refs, self._used_refs_path)
                out = self._audit_and_return(
                    event, matched, "auto_confirmed", confirmed_by="system"
                )
                await self._emit_ack(event_id)
                return out
            out = self._audit_and_return(
                event, matched, "confirm_failed", confirmed_by=None
            )
            await self._emit_ack(event_id)
            return out
        finally:
            self._confirm_waiters.pop(order_id, None)

    def _audit_and_return(
        self,
        event: Mapping[str, Any],
        order: Optional[Mapping[str, Any]],
        decision: str,
        *,
        confirmed_by: Optional[str],
    ) -> dict[str, Any]:
        record = {
            "event_id": event.get("event_id"),
            "ref_number": event.get("ref_number"),
            "amount": event.get("amount"),
            "order_id": order.get("order_id") if order else None,
            "decision": decision,
            "confirmed_by": confirmed_by,
        }
        append_audit(record, path=self._audit_path)
        return {
            "decision": decision,
            "event_id": event.get("event_id"),
            "order_id": record["order_id"],
        }
