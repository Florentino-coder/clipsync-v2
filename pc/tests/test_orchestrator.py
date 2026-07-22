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
            {"type": "confirm_result", "orderId": "1234", "ok": True, "reason": None}
        )

    reply_task = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="usb")
    await reply_task

    bridge.push_confirm_order.assert_awaited_once_with("1234")
    assert result["decision"] == "auto_confirmed"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "auto_confirmed" for a in audits)


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
            {"type": "confirm_result", "orderId": "1234", "ok": True, "reason": None}
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
            {"type": "confirm_result", "orderId": "1234", "ok": True, "reason": None}
        )

    t = asyncio.create_task(_reply())
    result = await orch.handle_slip_event(EVENT, source="relay", sig=_sig(EVENT))
    await t
    bridge.push_confirm_order.assert_awaited_once_with("1234")
    assert result["decision"] == "auto_confirmed"


@pytest.mark.asyncio
async def test_parse_failed_pending_review_no_confirm(tmp_path: Path):
    bridge = MagicMock()
    bridge.push_confirm_order = AsyncMock()
    orch = _make_orchestrator(tmp_path, bridge)
    orch.on_pending_orders(
        {
            "type": "pending_orders",
            "orders": [{"orderId": "1234", "amount": 350.0, "accountLast4": "6789"}],
        }
    )

    event = {**EVENT, "event_id": "evt-pf", "parse_failed": True}
    result = await orch.handle_slip_event(event, source="usb")

    bridge.push_confirm_order.assert_not_awaited()
    assert result["decision"] == "pending_review"
    audits = _audit_lines(tmp_path / "audit.jsonl")
    assert any(a.get("decision") == "pending_review" for a in audits)


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
            {"type": "confirm_result", "orderId": "1234", "ok": True, "reason": None}
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
