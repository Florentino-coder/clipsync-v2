"""PC withdraw normalize + emit helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from clipsync.orchestrator import SlipOrchestrator, _normalize_order
from clipsync.withdraw_notify import (
    SLIP_STATUS_MESSAGE_TH,
    build_slip_status_payload,
    build_withdraw_notify_payload,
    format_amount_display,
    new_orders_since,
    resolve_order_id_for_slip_status,
    should_emit_slip_status,
)


def test_normalize_order_keeps_full_account_and_name():
    # order_id must be >=4 chars (is_reliable_order_id); plan's "W-9" is rejected.
    raw = {
        "order_id": "W-99",
        "amount": "1,464.00",
        "account": "4774090171",
        "bank": "KBANK",
        "name": "สมชาย ใจดี",
    }
    out = _normalize_order(raw)
    assert out["order_id"] == "W-99"
    assert out["account"] == "4774090171"
    assert out["account_last4"] == "0171"
    assert out["account_name"] == "สมชาย ใจดี"
    assert out["bank"] == "KBANK"
    assert out["amount"] == "1,464.00" or out["amount"] == "1464.00"


def test_normalize_order_account_name_aliases():
    out = _normalize_order(
        {
            "ref": "REF-22",
            "amount": 100,
            "member_bank_account": "1234567890",
            "account_name": "Alice",
            "bank_name_th": "กสิกร",
        }
    )
    assert out["account"] == "1234567890"
    assert out["account_last4"] == "7890"
    assert out["account_name"] == "Alice"
    assert out["bank"]


def test_normalize_order_empty_name_ok_when_missing():
    out = _normalize_order(
        {"order_id": "OID1", "amount": 50, "account": "9999888877", "bank": "SCB"}
    )
    assert out["account_name"] == ""
    assert out["account"] == "9999888877"


def test_format_amount_display():
    assert format_amount_display("100.00") == "100.00"
    assert format_amount_display(100) == "100.00"
    assert format_amount_display("1,464.50") == "1464.50"


def test_new_orders_since_returns_only_unseen_ids():
    prev = [{"order_id": "A", "amount": 1, "account": "1", "bank": "KBANK", "account_name": ""}]
    curr = [
        {"order_id": "A", "amount": 1, "account": "1", "bank": "KBANK", "account_name": ""},
        {"order_id": "B", "amount": 2, "account": "22", "bank": "SCB", "account_name": "Bob"},
    ]
    added = new_orders_since(prev, curr)
    assert [o["order_id"] for o in added] == ["B"]


def test_build_withdraw_notify_payload_fields():
    order = {
        "order_id": "W-1",
        "amount": 100,
        "account": "4774090171",
        "bank": "KBANK",
        "account_name": "A",
    }
    payload = build_withdraw_notify_payload(order, ts=1720000000)
    assert payload == {
        "action": "withdraw_notify",
        "order_id": "W-1",
        "amount": "100.00",
        "account": "4774090171",
        "bank": "KBANK",
        "account_name": "A",
        "ts": 1720000000,
    }


def test_on_pending_orders_emits_only_new(monkeypatch):
    sent: list[dict] = []
    logs: list[str] = []

    def fake_emit(payload: dict) -> None:
        sent.append(payload)

    orch = SlipOrchestrator(
        {
            "auto_confirm": {"enabled": True, "min_ocr_confidence": 0.9,
                             "require_manual_review": {"enabled": False, "amount_threshold": 99999}},
            "matching": {"require_account_last4_match": True, "prevent_duplicate_ref_number": True},
        },
        chrome_bridge=MagicMock(),
        shared_secret="x" * 32,
        send_withdraw_notify=fake_emit,
        activity_log=logs.append,
    )
    # order_id must be >=4 chars (is_reliable_order_id); plan's "A"/"B" are rejected.
    orch.on_pending_orders(
        {"orders": [{"order_id": "ORD-A", "amount": 10, "account": "1111222233", "bank": "KBANK"}]}
    )
    orch.on_pending_orders(
        {
            "orders": [
                {"order_id": "ORD-A", "amount": 10, "account": "1111222233", "bank": "KBANK"},
                {"order_id": "ORD-B", "amount": 20, "account": "4444555566", "bank": "SCB", "name": "B"},
            ]
        }
    )
    assert len(sent) == 2  # first snapshot all new + second only ORD-B
    assert sent[0]["order_id"] == "ORD-A"
    assert sent[1]["order_id"] == "ORD-B"
    assert sent[1]["account"] == "4444555566"
    assert any("Withdraw scrape: 1 order(s), 1 new" in m for m in logs)
    assert any("Withdraw scrape: 2 order(s), 1 new" in m for m in logs)


def test_on_pending_orders_logs_skip_empty_account():
    logs: list[str] = []
    orch = SlipOrchestrator(
        {
            "auto_confirm": {"enabled": True, "min_ocr_confidence": 0.9,
                             "require_manual_review": {"enabled": False, "amount_threshold": 99999}},
            "matching": {"require_account_last4_match": True, "prevent_duplicate_ref_number": True},
        },
        chrome_bridge=MagicMock(),
        shared_secret="x" * 32,
        send_withdraw_notify=lambda _p: None,
        activity_log=logs.append,
    )
    orch.on_pending_orders(
        {"source": "dom", "orders": [{"order_id": "ORD-Z", "amount": 10, "account": ""}]}
    )
    assert any("Withdraw scrape: 1 order(s), 1 new" in m for m in logs)
    assert any("WDRAW skip: empty order_id/account" in m for m in logs)


def test_build_slip_status_payload_done():
    payload = build_slip_status_payload(
        job_id="evt-001",
        order_id="1234",
        amount=350,
        stage="done",
        ts=1720000000,
    )
    assert payload == {
        "action": "slip_status",
        "job_id": "evt-001",
        "order_id": "1234",
        "amount": "350.00",
        "stage": "done",
        "message_th": "สำเร็จ",
        "reason": "",
        "ts": 1720000000,
    }


def test_slip_status_message_map_covers_spec_stages():
    assert SLIP_STATUS_MESSAGE_TH["received"] == "โอนเงินสำเร็จ"
    assert SLIP_STATUS_MESSAGE_TH["processing"] == "กำลังดำเนินการ — อย่าโอนซ้ำ"
    assert SLIP_STATUS_MESSAGE_TH["done"] == "สำเร็จ"
    assert SLIP_STATUS_MESSAGE_TH["failed"].startswith("ไม่สำเร็จ")


def test_resolve_order_id_prefers_order_id_fields():
    assert (
        resolve_order_id_for_slip_status(
            {"orderId": "9999", "matchKey": "350.00"},
            pending=[{"order_id": "9999", "amount": "350.00"}],
        )
        == "9999"
    )


def test_resolve_order_id_falls_back_to_pending_amount_match():
    assert (
        resolve_order_id_for_slip_status(
            {"ok": True, "matchKey": "350.00", "amount": 350},
            pending=[{"order_id": "1234", "amount": "350.00"}],
        )
        == "1234"
    )


def test_should_emit_slip_status_dedupes_within_2s():
    assert should_emit_slip_status(
        "j|o|done", job_id="j", order_id="o", stage="done", now=10.0, last_at=9.0
    ) is False
    assert should_emit_slip_status(
        "j|o|done", job_id="j", order_id="o", stage="done", now=12.5, last_at=10.0
    ) is True
    assert should_emit_slip_status(
        None, job_id="j", order_id="o", stage="done", now=1.0, last_at=0.0
    ) is True
