"""Unit tests for slip debug panel row formatting (no Tk display required)."""

from __future__ import annotations

from clipsync.ui.debug_panel import (
    STATUS_TAG_COLORS,
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
    assert status_display_label("auto_confirmed") == "auto-confirmed"
    assert status_display_label("pending_review") == "pending review"
    assert status_display_label("confirm_failed") == "confirm_failed"


def test_format_slip_row_extracts_columns_and_tag():
    event = {
        "ts": "2026-07-22T10:15:30+00:00",
        "bank": "SCB",
        "amount": 150.5,
        "ref_number": "ABCDEF123456",
        "order_id": "ORD-9",
        "transport": "usb",
        "decision": "pending_review",
        "event_id": "evt-1",
    }
    row = format_slip_row(event)
    assert row.values == (
        "10:15:30",
        "SCB",
        "150.50",
        "123456",
        "ORD-9",
        "usb",
        "pending review",
    )
    assert row.tag == "warn"
    assert row.event_id == "evt-1"
    assert row.ref_number == "ABCDEF123456"


def test_format_slip_row_short_ref_and_missing_fields():
    row = format_slip_row({"decision": "rejected", "ref_number": "12"})
    assert row.values[3] == "12"
    assert row.values[1] == "-"
    assert row.values[2] == "-"
    assert row.values[4] == "-"
    assert row.values[5] == "-"
    assert row.tag == "error"
