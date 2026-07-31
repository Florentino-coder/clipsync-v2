"""Unit tests for settings form mapping + transport indicator + layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from clipsync.config import default_config, save_config
from clipsync.ui.settings_panel import (
    SettingsFormValues,
    apply_form_values,
    extension_install_path_text,
    form_values_from_config,
    pairing_token_from_config,
    settings_use_two_columns,
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
    assert cfg["transport"]["preferred_mode"] == "relay"


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


def test_extension_install_path_text_is_full_stable_path(tmp_path: Path):
    path = extension_install_path_text(appdata=tmp_path)
    expected = str((tmp_path / "ClipSync" / "chrome-extension").resolve())
    assert path == expected
    assert "ClipSync" in path
    assert "chrome-extension" in path
    assert "_internal" not in path


def test_settings_use_two_columns_threshold():
    assert settings_use_two_columns(819) is False
    assert settings_use_two_columns(820) is True
    assert settings_use_two_columns(1200) is True


def test_settings_panel_scrollable_body_and_sticky_footer(tmp_path: Path):
    """Layout smoke test — needs a real Tk display; skip on headless CI."""
    tk = pytest.importorskip("tkinter")
    ttk = pytest.importorskip("tkinter.ttk")
    from clipsync.ui.settings_panel import SettingsPanel

    cfg_path = tmp_path / "clipsync-config.json"
    save_config(default_config(), path=cfg_path)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        # ubuntu-latest CI has no $DISPLAY; pure-logic tests above still run.
        pytest.skip(f"Tk display unavailable (headless CI): {exc}")
    root.withdraw()
    root.geometry("640x420")
    try:
        tab = ttk.Frame(root)
        tab.pack(fill="both", expand=True)
        panel = SettingsPanel(tab, config_path=cfg_path)
        root.update_idletasks()

        assert hasattr(panel, "_body_canvas")
        assert panel._body_canvas.winfo_exists()
        assert hasattr(panel, "_scroll_inner")
        assert panel._scroll_inner.winfo_exists()
        assert hasattr(panel, "_actions_frame")
        # Sticky footer lives on the outer frame, not inside the scroll canvas.
        assert str(panel._actions_frame.winfo_parent()) == str(panel.frame)
        assert str(panel._body_canvas.winfo_parent()) != str(panel._scroll_inner)
        # Extension path + Thai section titles still present after layout redesign.
        assert "chrome-extension" in panel._ext_path_var.get()

        def _labelframe_texts(widget) -> list[str]:
            texts: list[str] = []
            for child in widget.winfo_children():
                if child.winfo_class() in ("TLabelframe", "Labelframe"):
                    texts.append(str(child.cget("text")))
                texts.extend(_labelframe_texts(child))
            return texts

        kids_text = " ".join(_labelframe_texts(panel._scroll_inner))
        assert "Chrome" in kids_text or "extension" in kids_text.lower()
        assert "APK" in kids_text
    finally:
        root.destroy()
