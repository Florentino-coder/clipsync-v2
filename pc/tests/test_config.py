"""Tests for ClipSync slip config schema + loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipsync.config import CONFIG_FILENAME, default_config, load_config


def _write_partial(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_missing_file_fills_all_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLIPSYNC_CONFIG", str(tmp_path / "missing.json"))
    cfg = load_config()

    expected = default_config()
    # pairing_token is generated; compare structure excluding it
    assert cfg["transport"]["preferred_mode"] == expected["transport"]["preferred_mode"]
    assert cfg["relay_url"] == expected["relay_url"]
    assert cfg["auto_confirm"]["enabled"] is False
    assert cfg["auto_confirm"]["min_ocr_confidence"] == expected["auto_confirm"]["min_ocr_confidence"]
    assert (
        cfg["auto_confirm"]["require_manual_review"]
        == expected["auto_confirm"]["require_manual_review"]
    )
    assert cfg["matching"] == expected["matching"]
    assert cfg["license"] == expected["license"]
    assert cfg["chrome_bridge"]["ws_port"] == expected["chrome_bridge"]["ws_port"]
    assert isinstance(cfg["chrome_bridge"]["pairing_token"], str)
    assert len(cfg["chrome_bridge"]["pairing_token"]) == 32  # token_hex(16)


def test_auto_confirm_enabled_defaults_to_false(tmp_path: Path):
    cfg = load_config(path=tmp_path / "absent.json")
    assert cfg["auto_confirm"]["enabled"] is False


def test_partial_file_merges_defaults(tmp_path: Path):
    path = _write_partial(
        tmp_path / CONFIG_FILENAME,
        {"relay_url": "wss://example.test/relay", "auto_confirm": {"enabled": True}},
    )
    cfg = load_config(path=path)

    assert cfg["relay_url"] == "wss://example.test/relay"
    assert cfg["auto_confirm"]["enabled"] is True
    # unspecified nested keys keep defaults
    assert cfg["auto_confirm"]["min_ocr_confidence"] == 0.90
    assert cfg["auto_confirm"]["require_manual_review"]["enabled"] is True
    assert cfg["auto_confirm"]["require_manual_review"]["amount_threshold"] == 5000.0
    assert cfg["transport"]["preferred_mode"] == "auto"
    assert cfg["matching"]["require_account_last4_match"] is True
    assert cfg["matching"]["prevent_duplicate_ref_number"] is True
    assert cfg["chrome_bridge"]["ws_port"] == 8765
    assert cfg["license"]["refresh_interval_days"] == 3
    assert isinstance(cfg["license"]["token_path"], str)
    assert cfg["license"]["token_path"]


def test_wrong_type_raises_value_error(tmp_path: Path):
    path = _write_partial(
        tmp_path / CONFIG_FILENAME,
        {"auto_confirm": {"enabled": "yes"}},
    )
    with pytest.raises(ValueError, match="auto_confirm.enabled"):
        load_config(path=path)


def test_wrong_nested_type_raises_value_error(tmp_path: Path):
    path = _write_partial(
        tmp_path / CONFIG_FILENAME,
        {"auto_confirm": {"require_manual_review": {"amount_threshold": "high"}}},
    )
    with pytest.raises(ValueError, match="amount_threshold"):
        load_config(path=path)


def test_pairing_token_generated_once_and_persisted(tmp_path: Path):
    path = tmp_path / CONFIG_FILENAME
    cfg1 = load_config(path=path)
    token1 = cfg1["chrome_bridge"]["pairing_token"]
    assert path.exists()
    assert len(token1) == 32
    assert token1.isalnum()

    # Not a 9-digit clip pairing ID
    assert not (len(token1) == 9 and token1.isdigit())

    cfg2 = load_config(path=path)
    assert cfg2["chrome_bridge"]["pairing_token"] == token1

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["chrome_bridge"]["pairing_token"] == token1


def test_existing_pairing_token_preserved(tmp_path: Path):
    path = _write_partial(
        tmp_path / CONFIG_FILENAME,
        {"chrome_bridge": {"pairing_token": "a" * 32, "ws_port": 8765}},
    )
    cfg = load_config(path=path)
    assert cfg["chrome_bridge"]["pairing_token"] == "a" * 32


def test_default_config_path_uses_appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("CLIPSYNC_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from clipsync import config as config_mod

    assert config_mod.default_config_path() == tmp_path / "ClipSync" / CONFIG_FILENAME
