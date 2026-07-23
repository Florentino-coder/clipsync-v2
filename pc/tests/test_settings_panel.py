"""Unit tests for settings form mapping + transport indicator (no Tk)."""

from __future__ import annotations

from clipsync.config import default_config
from clipsync.ui.settings_panel import (
    SettingsFormValues,
    apply_form_values,
    form_values_from_config,
    pairing_token_from_config,
    transport_indicator,
)


def test_form_values_from_config_reads_nested_fields():
    cfg = default_config()
    cfg["auto_confirm"]["enabled"] = True
    cfg["auto_confirm"]["min_ocr_confidence"] = 0.85
    cfg["auto_confirm"]["require_manual_review"]["enabled"] = False
    cfg["auto_confirm"]["require_manual_review"]["amount_threshold"] = 2500.0
    cfg["transport"]["preferred_mode"] = "usb"

    values = form_values_from_config(cfg)
    assert values == SettingsFormValues(
        auto_confirm_enabled=True,
        threshold_enabled=False,
        amount_threshold=2500.0,
        min_ocr_confidence=0.85,
        preferred_mode="usb",
    )


def test_apply_form_values_updates_config_copy():
    cfg = default_config()
    values = SettingsFormValues(
        auto_confirm_enabled=True,
        threshold_enabled=True,
        amount_threshold=9000.0,
        min_ocr_confidence=0.95,
        preferred_mode="relay",
    )
    updated = apply_form_values(cfg, values)
    assert updated is not cfg
    assert updated["auto_confirm"]["enabled"] is True
    assert updated["auto_confirm"]["min_ocr_confidence"] == 0.95
    assert updated["auto_confirm"]["require_manual_review"]["enabled"] is True
    assert updated["auto_confirm"]["require_manual_review"]["amount_threshold"] == 9000.0
    assert updated["transport"]["preferred_mode"] == "relay"
    # original untouched
    assert cfg["auto_confirm"]["enabled"] is False
    assert cfg["transport"]["preferred_mode"] == "auto"


def test_apply_form_values_rejects_bad_mode():
    cfg = default_config()
    values = SettingsFormValues(
        auto_confirm_enabled=False,
        threshold_enabled=True,
        amount_threshold=100.0,
        min_ocr_confidence=0.9,
        preferred_mode="wifi",
    )
    try:
        apply_form_values(cfg, values)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "preferred_mode" in str(exc)


def test_transport_indicator_usb_and_relay():
    text, color = transport_indicator("usb")
    assert "USB" in text
    assert color == "#19a94b"

    text, color = transport_indicator("relay")
    assert "Cloud Relay" in text
    assert color == "#e09c18"

    text, color = transport_indicator(None)
    assert "ไม่เชื่อมต่อ" in text or "disconnected" in text.lower() or text
    assert color == "#667085"


def test_pairing_token_from_config():
    cfg = default_config()
    cfg["chrome_bridge"]["pairing_token"] = "abc123deadbeef00abc123deadbeef00"
    assert pairing_token_from_config(cfg) == "abc123deadbeef00abc123deadbeef00"
    assert pairing_token_from_config({}) == ""
    assert pairing_token_from_config({"chrome_bridge": {}}) == ""
