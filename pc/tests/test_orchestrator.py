"""Tests for slip fetcher + orchestrator (matcher → chrome bridge → audit)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from clipsync.orchestrator import SlipOrchestrator
from clipsync.slip_fetcher import SlipFetcher, UsbRequiredError

SECRET = "abcdef0123456789abcdef0123456789"

CFG = {
    "auto_confirm": {
        "enabled": True,
        "min_ocr_confidence": 0.90,
        "require_manual_review": {"enabled": True, "amount_threshold": 5000.0},
    },
    "matching": {
        "require_account_last4_match": True,
        "prevent_duplicate_ref_number": True,
    },
}

EVENT = {
    "event_id": "evt-001",
    "amount": 350.0,
    "receiver_account_last4": "6789",
    "sender_account_last4": "1234",
    "ref_number": "202607221432001",
    "ocr_confidence": 0.97,
    "parse_failed": False,
    "bank": "SCB",
}


def _sig(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hmac.new(SECRET.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _audit_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FakeUsbTransport:
    name = "usb"

    def __init__(self, slips: list[dict[str, Any]] | None = None) -> None:
        self.slips = slips or [{"event_id": "s1"}]
        self.calls: list[tuple[datetime, datetime]] = []

    async def fetch_slips(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        self.calls.append((date_from, date_to))
        return list(self.slips)


class FakeRelayTransport:
    name = "relay"

    async def fetch_slips(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        raise AssertionError("relay.fetch_slips must not be called by SlipFetcher")


@pytest.mark.asyncio
async def test_slip_fetcher_usb_delegates():
    transport = FakeUsbTransport([{"event_id": "a"}])
    fetcher = SlipFetcher(transport)
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 2, tzinfo=timezone.utc)
    result = await fetcher.fetch_slips(start, end)
    assert result == [{"event_id": "a"}]
    assert transport.calls == [(start, end)]


@pytest.mark.asyncio
async def test_slip_fetcher_relay_raises_usb_required():
    fetcher = SlipFetcher(FakeRelayTransport())
    with pytest.raises(UsbRequiredError) as excinfo:
        await fetcher.fetch_slips(
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
    assert excinfo.value.code == "usb_required"


def _make_orchestrator(
    tmp_path: Path,
    bridge: Any | None = None,
    *,
    cfg: dict[str, Any] | None = None,
) -> SlipOrchestrator:
    if bridge is None:
        bridge = MagicMock()
        bridge.push_confirm_order = AsyncMock()
    return SlipOrchestrator(
        cfg or CFG,
        chrome_bridge=bridge,
        shared_secret=SECRET,
        audit_path=tmp_path / "audit.jsonl",
        seen_events_path=tmp_path / "seen_events.json",
        used_refs_path=tmp_path / "used_refs.json",
        confirm_timeout=0.2,
    )


@pytest.mark.asyncio
async def test_normal_event_calls_confirm(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {"type": "confirm_result", "orderId": "1234", "ok": True, "verified": True, "reason": None}
        )

    reply_task = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="usb")
    await reply_task

    bridge.push_confirm_order.assert_awaited_once()
    call = bridge.push_confirm_order.await_args
    assert call.args[0] == "1234"
    assert call.kwargs.get("amount") in (350.0, "350.00")
    assert isinstance(call.kwargs.get("slip"), dict)
    assert result["decision"] == "auto_confirmed"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "auto_confirmed" for a in audits)


@pytest.mark.asyncio
async def test_auto_confirmed_emits_slip_status_done(tmp_path: Path):
    statuses: list[dict] = []
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = SlipOrchestrator(
        CFG,
        chrome_bridge=bridge,
        shared_secret=SECRET,
        audit_path=tmp_path / "audit.jsonl",
        seen_events_path=tmp_path / "seen_events.json",
        used_refs_path=tmp_path / "used_refs.json",
        confirm_timeout=0.2,
        send_slip_status=lambda p: statuses.append(p),
    )
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [
                {
                    "orderId": "1234",
                    "amount": 350.0,
                    "account": "11116789",
                    "accountLast4": "6789",
                    "bank": "KBANK",
                }
            ],
        }
    )

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {
                "type": "confirm_result",
                "orderId": "1234",
                "ok": True,
                "verified": True,
                "reason": None,
            }
        )

    reply_task = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="usb")
    await reply_task

    assert result["decision"] == "auto_confirmed"
    assert statuses, "expected at least one slip_status emit"
    done = [s for s in statuses if s.get("stage") == "done"]
    assert done, statuses
    assert done[-1]["action"] == "slip_status"
    assert done[-1]["order_id"] == "1234"
    assert done[-1]["job_id"] == "evt-001"
    assert done[-1]["message_th"] == "สำเร็จ"


@pytest.mark.asyncio
async def test_over_threshold_pending_review_no_confirm(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "99", "amount": 6000.0, "accountLast4": "6789"}],
        }
    )

    event = {**EVENT, "event_id": "evt-hi", "amount": 6000.0}
    result = await orch.handle_slip_event(event, source="usb")

    bridge.push_confirm_order.assert_not_awaited()
    assert result["decision"] == "pending_review"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "pending_review" for a in audits)


@pytest.mark.asyncio
async def test_duplicate_event_skipped(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {"type": "confirm_result", "orderId": "1234", "ok": True, "verified": True, "reason": None}
        )

    t1 = asyncio.create_task(_reply())
    await orch.handle_slip_event(EVENT, source="usb")
    await t1

    bridge.push_confirm_order.reset_mock()
    result = await orch.handle_slip_event(EVENT, source="usb")
    bridge.push_confirm_order.assert_not_awaited()
    assert result["decision"] == "duplicate"


@pytest.mark.asyncio
async def test_relay_bad_hmac_rejected(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    result = await orch.handle_slip_event(EVENT, source="relay", sig="deadbeef")
    bridge.push_confirm_order.assert_not_awaited()
    assert result["decision"] == "rejected"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "rejected" for a in audits)


@pytest.mark.asyncio
async def test_relay_valid_hmac_proceeds(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {"type": "confirm_result", "orderId": "1234", "ok": True, "verified": True, "reason": None}
        )

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="relay", sig=_sig(EVENT))
    await t
    bridge.push_confirm_order.assert_awaited_once()
    assert bridge.push_confirm_order.await_args.args[0] == "1234"
    assert result["decision"] == "auto_confirmed"


@pytest.mark.asyncio
async def test_soft_parse_failed_null_ref_still_auto_confirms(tmp_path: Path):
    """Production: ref_number null → mobile parse_failed=true → must still auto.

    Live audits for 1006.00 had From last4 + amount but only pending_review /
    admin_manual — never auto_confirmed.
    """
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [
                {
                    "orderId": "acct:2864560012",
                    "amount": 1006.0,
                    "accountLast4": "0012",
                }
            ],
        }
    )

    event = {
        **EVENT,
        "event_id": "evt-1006",
        "amount": 1006.0,
        "receiver_account_last4": "0012",
        "sender_account_last4": "7476",
        "ref_number": None,
        "parse_failed": True,
        "ocr_confidence": 0.0,
    }

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {
                "type": "confirm_result",
                "orderId": "acct:2864560012",
                "ok": True,
                "verified": True,
                "reason": None,
            }
        )

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(event, source="usb")
    await t

    bridge.push_confirm_order.assert_awaited_once()
    assert result["decision"] == "auto_confirmed"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "auto_confirmed" for a in audits)


@pytest.mark.asyncio
async def test_auto_confirm_disabled_pending_review(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    cfg = {
        **CFG,
        "auto_confirm": {**CFG["auto_confirm"], "enabled": False},
    }
    orch = _make_orchestrator(tmp_path, bridge, cfg=cfg)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    event = {**EVENT, "event_id": "evt-off"}
    result = await orch.handle_slip_event(event, source="usb")

    bridge.push_confirm_order.assert_not_awaited()
    assert result["decision"] == "pending_review"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "pending_review" for a in audits)


@pytest.mark.asyncio
async def test_clicked_but_unverified_marks_confirm_failed(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {
                "type": "confirm_result",
                "orderId": "1234",
                "ok": True,
                "verified": False,
                "reason": "clicked_but_unverified",
            }
        )

    reply_task = asyncio.create_task(_reply())
    result = await orch.handle_slip_event({**EVENT, "event_id": "evt-unverified"}, source="usb")
    await reply_task

    assert result["decision"] == "confirm_failed"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "confirm_failed" for a in audits)


@pytest.mark.asyncio
async def test_confirm_timeout_marks_confirm_failed(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    result = await orch.handle_slip_event(EVENT, source="usb")
    assert result["decision"] == "confirm_failed"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "confirm_failed" for a in audits)


@pytest.mark.asyncio
async def test_seen_events_persist_across_instances(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch1 = _make_orchestrator(tmp_path, bridge)
    orch1.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch1.on_confirm_result(
            {"type": "confirm_result", "orderId": "1234", "ok": True, "verified": True, "reason": None}
        )

    t = asyncio.create_task(_reply())
    await orch1.handle_slip_event(EVENT, source="usb")
    await t

    bridge2 = MagicMock()
    bridge2.push_confirm_order = AsyncMock()
    orch2 = _make_orchestrator(tmp_path, bridge2)
    result = await orch2.handle_slip_event(EVENT, source="usb")
    bridge2.push_confirm_order.assert_not_awaited()
    assert result["decision"] == "duplicate"


def test_callback_exceptions_are_swallowed(tmp_path: Path):
    """Orchestrator chrome-bridge callbacks must not raise (keep WS alive)."""
    orch = _make_orchestrator(tmp_path)
    # Force an internal error path by feeding non-dict-like via monkeypatch is hard;
    # call wrappers that must never raise even with odd payloads.
    orch.on_pending_orders(None)  # type: ignore[arg-type]
    orch.on_confirm_result({"orderId": object()})  # type: ignore[dict-item]


@pytest.mark.asyncio
async def test_handle_slip_event_invokes_send_ack(tmp_path: Path):
    """PC must emit slip_ack (via send_ack) after processing a new slip."""
    acked: list[str] = []
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = SlipOrchestrator(
        CFG,
        chrome_bridge=bridge,
        shared_secret=SECRET,
        audit_path=tmp_path / "audit.jsonl",
        seen_events_path=tmp_path / "seen_events.json",
        used_refs_path=tmp_path / "used_refs.json",
        confirm_timeout=0.2,
        send_ack=lambda event_id: acked.append(event_id),
    )
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {"type": "confirm_result", "orderId": "1234", "ok": True, "verified": True, "reason": None}
        )

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="usb")
    await t

    assert result["decision"] == "auto_confirmed"
    assert acked == ["evt-001"]


@pytest.mark.asyncio
async def test_duplicate_event_still_invokes_send_ack(tmp_path: Path):
    """Duplicates must still ack so the phone can markSent and stop resending."""
    acked: list[str] = []
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = SlipOrchestrator(
        CFG,
        chrome_bridge=bridge,
        shared_secret=SECRET,
        audit_path=tmp_path / "audit.jsonl",
        seen_events_path=tmp_path / "seen_events.json",
        used_refs_path=tmp_path / "used_refs.json",
        confirm_timeout=0.2,
        send_ack=lambda event_id: acked.append(event_id),
    )
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {"type": "confirm_result", "orderId": "1234", "ok": True, "verified": True, "reason": None}
        )

    t = asyncio.create_task(_reply())
    await orch.handle_slip_event(EVENT, source="usb")
    await t
    acked.clear()

    result = await orch.handle_slip_event(EVENT, source="usb")
    assert result["decision"] == "duplicate"
    assert acked == ["evt-001"]


@pytest.mark.asyncio
async def test_auto_confirm_amount_only_when_pending_orders_empty(tmp_path: Path):
    """Scrape empty → still auto-push by amount+slip (same as manual confirm)."""
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    # No on_pending_orders — list stays empty (live Jinbao scrape often misses).

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {
                "type": "confirm_result",
                "orderId": "350.00",
                "matchKey": "350.00",
                "ok": True,
                "verified": True,
                "reason": None,
            }
        )

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="usb")
    await t

    bridge.push_confirm_order.assert_awaited_once()
    call = bridge.push_confirm_order.await_args
    assert call.args[0] == "350.00"
    assert isinstance(call.kwargs.get("slip"), dict)
    assert result["decision"] == "auto_confirmed"


@pytest.mark.asyncio
async def test_auto_confirm_when_ocr_confidence_is_zero_unknown(tmp_path: Path):
    """Production: ML Kit sends ocr_confidence=0.0 → must still auto-confirm.

    Audit showed order_id '' (amount_only match) + pending_review for 2900 while
    manual ยืนยันเอง succeeded — confidence gate was treating 0.0 as < 0.90.
    """
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    # Empty scrape → amount_only path (matches live Jinbao when scrape misses).

    event = {
        **EVENT,
        "event_id": "evt-2900",
        "amount": 2900.0,
        "receiver_account_last4": "0171",
        "sender_account_last4": "7476",
        "ocr_confidence": 0.0,
        "ref_number": None,
    }

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {
                "type": "confirm_result",
                "orderId": "2900.00",
                "matchKey": "2900.00",
                "ok": True,
                "verified": True,
                "reason": None,
            }
        )

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(event, source="usb")
    await t

    bridge.push_confirm_order.assert_awaited_once()
    assert result["decision"] == "auto_confirmed"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "auto_confirmed" for a in audits)


@pytest.mark.asyncio
async def test_pending_review_records_reason_when_disabled(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    cfg = {
        **CFG,
        "auto_confirm": {**CFG["auto_confirm"], "enabled": False},
    }
    orch = _make_orchestrator(tmp_path, bridge, cfg=cfg)
    result = await orch.handle_slip_event(EVENT, source="usb")
    assert result["decision"] == "pending_review"
    assert result.get("reason") == "auto_confirm_disabled"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert audits[-1].get("reason") == "auto_confirm_disabled"
    bridge.push_confirm_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_confirm_from_masked_sender_without_explicit_last4(tmp_path: Path):
    """Live bug: OCR had masked from-account but no sender_account_last4.

    Manual ยืนยันเอง derived last4 from the mask; auto previously skipped.
    """
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)

    event = {
        **EVENT,
        "event_id": "evt-mask",
        "sender_account_last4": None,
        "sender_account_masked": "xxxxxx7476",
        "ocr_confidence": 0.0,
    }

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {
                "type": "confirm_result",
                "orderId": "350.00",
                "matchKey": "350.00",
                "ok": True,
                "verified": True,
                "reason": None,
            }
        )

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(event, source="usb")
    await t

    bridge.push_confirm_order.assert_awaited_once()
    slip = bridge.push_confirm_order.await_args.kwargs.get("slip") or {}
    assert slip.get("sender_account_last4") == "7476"
    assert result["decision"] == "auto_confirmed"


@pytest.mark.asyncio
async def test_missing_sender_last4_pending_with_reason(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    event = {**EVENT, "event_id": "evt-nolast4", "sender_account_last4": None}
    event.pop("sender_account_masked", None)
    result = await orch.handle_slip_event(event, source="usb")
    assert result["decision"] == "pending_review"
    assert result.get("reason") == "missing_sender_last4"
    bridge.push_confirm_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_1900_low_ocr_confidence_still_auto_confirms(tmp_path: Path):
    """Production 2026-07-25 14:18:42 — amount 1900 From…7476 To…1588.

    Audit: pending_review reason=low_ocr_confidence, then admin_manual succeeded.
    ocr_confidence float was not audited; any (0, 0.9) reproduces the blocker.
    """
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)

    event = {
        "event_id": "f9d049bc-e1ab-46e3-b3d2-97ec9a27b06b",
        "amount": 1900.0,
        "ref_number": None,
        "sender_account_last4": "7476",
        "receiver_account_last4": "1588",
        "ocr_confidence": 0.42,
        "parse_failed": True,
        "bank": "SCB",
    }

    async def _reply() -> None:
        await asyncio.sleep(0.02)
        orch.on_confirm_result(
            {
                "type": "confirm_result",
                "orderId": "1900.00",
                "matchKey": "1900.00",
                "ok": True,
                "verified": True,
                "reason": None,
            }
        )

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(event, source="usb")
    await t

    bridge.push_confirm_order.assert_awaited_once()
    assert result["decision"] == "auto_confirmed"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert audits[-1]["decision"] == "auto_confirmed"
    assert audits[-1].get("ocr_confidence") == 0.42
    assert audits[-1].get("sender_account_last4") == "7476"


@pytest.mark.asyncio
async def test_live_1006_empty_from_pending_missing_sender_last4(tmp_path: Path):
    """Production 2026-07-25 14:17:58 — amount 1006 From '-' must stay pending.

    Must surface missing_sender_last4 (not low_ocr_confidence eating the gate).
    """
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)

    event = {
        "event_id": "5e29a94f-7f74-486b-91df-ccae602fc1f9",
        "amount": 1006.0,
        "ref_number": None,
        "receiver_account_last4": "1588",
        "ocr_confidence": 0.42,
        "parse_failed": True,
        "bank": "SCB",
    }
    result = await orch.handle_slip_event(event, source="usb")
    assert result["decision"] == "pending_review"
    assert result.get("reason") == "missing_sender_last4"
    bridge.push_confirm_order.assert_not_awaited()
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert audits[-1].get("reason") == "missing_sender_last4"
    assert audits[-1].get("ocr_confidence") == 0.42


@pytest.mark.asyncio
async def test_slip_prepare_pauses_search_and_requests_scrape(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    bridge.push_pause_approved_search = AsyncMock(return_value=1)
    bridge.push_request_pending_scrape = AsyncMock(return_value=1)
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    async def _reply() -> None:
        for _ in range(80):
            if bridge.push_confirm_order.await_count:
                orch.on_confirm_result(
                    {
                        "type": "confirm_result",
                        "orderId": "1234",
                        "ok": True,
                        "verified": True,
                        "reason": None,
                    }
                )
                return
            await asyncio.sleep(0.05)

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="usb")
    await t

    bridge.push_pause_approved_search.assert_awaited()
    bridge.push_request_pending_scrape.assert_awaited()
    assert result["decision"] == "auto_confirmed"


@pytest.mark.asyncio
async def test_no_match_retries_then_pending_review(tmp_path: Path):
    """Wrong last4 on scrape list → no_match; retry within 4s then review."""
    import time

    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    bridge.push_pause_approved_search = AsyncMock()
    bridge.push_request_pending_scrape = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "9999"}],
        }
    )

    t0 = time.monotonic()
    result = await orch.handle_slip_event(EVENT, source="usb")
    elapsed = time.monotonic() - t0

    bridge.push_confirm_order.assert_not_awaited()
    assert result["decision"] == "pending_review"
    assert result.get("reason") == "no_match"
    assert elapsed < 5.0
    assert elapsed >= 2.0  # at least second retry offset


@pytest.mark.asyncio
async def test_no_match_becomes_match_on_retry(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    bridge.push_pause_approved_search = AsyncMock()
    bridge.push_request_pending_scrape = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "9999"}],
        }
    )

    async def _fix_orders() -> None:
        await asyncio.sleep(1.0)
        orch.on_pending_orders(
            {
                "type": "pending_orders",
                "orders": [
                    {"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}
                ],
            }
        )

    async def _reply() -> None:
        for _ in range(80):
            if bridge.push_confirm_order.await_count:
                orch.on_confirm_result(
                    {
                        "type": "confirm_result",
                        "orderId": "1234",
                        "ok": True,
                        "verified": True,
                        "reason": None,
                    }
                )
                return
            await asyncio.sleep(0.05)

    t_fix = asyncio.create_task(_fix_orders())
    t_reply = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="usb")
    await t_fix
    await t_reply

    bridge.push_confirm_order.assert_awaited_once()
    assert result["decision"] == "auto_confirmed"


@pytest.mark.asyncio
async def test_over_threshold_does_not_spend_full_retry_budget(tmp_path: Path):
    import time

    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    bridge.push_pause_approved_search = AsyncMock()
    bridge.push_request_pending_scrape = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "99", "amount": 6000.0, "accountLast4": "6789"}],
        }
    )

    event = {**EVENT, "event_id": "evt-hi-fast", "amount": 6000.0}
    t0 = time.monotonic()
    result = await orch.handle_slip_event(event, source="usb")
    elapsed = time.monotonic() - t0

    assert result["decision"] == "pending_review"
    assert elapsed < 1.5  # prepare ~0.4 + one attempt; no 2.6s retries

