"""Unit tests for slip debug panel row formatting (no Tk display required)."""

from __future__ import annotations

from clipsync.ui.debug_panel import (
    STATUS_TAG_COLORS,
    format_slip_details,
    format_slip_row,
    status_display_label,
    status_tag_for,
)


def test_status_tag_colors_cover_plan_badges():
    assert STATUS_TAG_COLORS["ok"] == "#d1fadf"  # green
    assert STATUS_TAG_COLORS["warn"] == "#fef0c7"  # yellow
    assert STATUS_TAG_COLORS["error"] == "#fee4e2"  # red


def test_status_tag_for_auto_confirmed_is_ok():
    assert status_tag_for("auto_confirmed") == "ok"
    assert status_tag_for("auto-confirmed") == "ok"


def test_status_tag_for_pending_review_is_warn():
    assert status_tag_for("pending_review") == "warn"
    assert status_tag_for("pending review") == "warn"


def test_status_tag_for_rejected_and_confirm_failed_are_error():
    assert status_tag_for("rejected") == "error"
    assert status_tag_for("confirm_failed") == "error"
    assert status_tag_for("overridden") == "error"


def test_status_display_label_humanizes():
    assert status_display_label("auto_confirmed") == "สำเร็จ"
    assert status_display_label("pending_review") == "รอตรวจ"
    assert status_display_label("confirm_failed") == "ล้มเหลว"


def test_format_slip_row_shows_from_to_last4_not_ref_order():
    event = {
        "ts": "2026-07-22T10:15:30+00:00",
        "bank": "SCB",
        "amount": 150.5,
        "ref_number": "ABCDEF123456",
        "order_id": "ORD-9",
        "transport": "usb",
        "decision": "pending_review",
        "event_id": "evt-1",
        "sender_account_last4": "7476",
        "receiver_account_last4": "1808",
    }
    row = format_slip_row(event)
    assert row.values == (
        "10:15:30",
        "SCB",
        "150.50",
        "…7476",
        "…1808",
        "รอตรวจ",
    )
    assert row.tag == "warn"
    assert row.event_id == "evt-1"
    assert row.ref_number == "ABCDEF123456"


def test_format_slip_row_missing_accounts():
    row = format_slip_row({"decision": "rejected", "ref_number": "12"})
    assert row.values[3] == "-"
    assert row.values[4] == "-"
    assert row.values[1] == "-"
    assert row.values[2] == "-"
    assert row.tag == "error"


def test_format_slip_details_includes_hidden_ref_order_and_names():
    text = format_slip_details(
        {
            "amount": 500,
            "bank": "SCB",
            "sender_name": "นางยุพิน",
            "sender_account_last4": "7518",
            "receiver_name": "บริษัท ดี พลัส",
            "receiver_account_last4": "2850",
            "receiver_bank": "KTB",
            "ref_number": "REF123",
            "order_id": "ORD9",
            "decision": "auto_confirmed",
            "transport": "usb",
        }
    )
    assert "นางยุพิน" in text
    assert "7518" in text
    assert "บริษัท ดี พลัส" in text
    assert "Ref: REF123" in text
    assert "Order: ORD9" in text
    assert "สำเร็จ" in text
