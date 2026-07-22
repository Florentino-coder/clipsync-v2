"""ClipSync slip auto-confirm configuration schema and loader."""

from __future__ import annotations

import json
import os
import secrets
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "clipsync-config.json"
CONFIG_ENV = "CLIPSYNC_CONFIG"

# Chrome bridge pairing token is hex (32 chars) — distinct from clip pairing ID (9 digits).
_PAIRING_TOKEN_BYTES = 16

_PREFERRED_MODES = frozenset({"auto", "usb", "relay"})


def user_data_dir() -> Path:
    if sys.platform.startswith("win"):
        root = os.getenv("APPDATA") or str(Path.home())
        return Path(root) / "ClipSync"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ClipSync"
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "clipsync"


def default_config_path() -> Path:
    override = os.getenv(CONFIG_ENV)
    if override:
        return Path(override)
    return user_data_dir() / CONFIG_FILENAME


def default_config() -> dict[str, Any]:
    data_dir = user_data_dir()
    return {
        "transport": {
            "preferred_mode": "auto",
        },
        "relay_url": "wss://clipsync-relay.onrender.com",
        "auto_confirm": {
            "enabled": False,
            "min_ocr_confidence": 0.90,
            "require_manual_review": {
                "enabled": True,
                "amount_threshold": 5000.0,
            },
        },
        "chrome_bridge": {
            "pairing_token": "",
            "ws_port": 8765,
        },
        "matching": {
            "require_account_last4_match": True,
            "prevent_duplicate_ref_number": True,
        },
        "license": {
            "token_path": str(data_dir / "license.token"),
            "refresh_interval_days": 3,
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any], path: str = "") -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        key_path = f"{path}.{key}" if path else key
        if key not in merged:
            merged[key] = deepcopy(value)
            continue
        if isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value, key_path)
        else:
            merged[key] = deepcopy(value)
    return merged


def _expect_type(value: Any, expected: type | tuple[type, ...], path: str) -> None:
    if expected is float and isinstance(value, bool):
        raise ValueError(f"{path} must be float, got {type(value).__name__}")
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        return
    if expected is int and isinstance(value, bool):
        raise ValueError(f"{path} must be int, got {type(value).__name__}")
    if not isinstance(value, expected):
        names = (
            expected.__name__
            if isinstance(expected, type)
            else " | ".join(t.__name__ for t in expected)
        )
        raise ValueError(f"{path} must be {names}, got {type(value).__name__}")


def _validate(cfg: dict[str, Any]) -> None:
    _expect_type(cfg.get("transport"), dict, "transport")
    mode = cfg["transport"].get("preferred_mode")
    _expect_type(mode, str, "transport.preferred_mode")
    if mode not in _PREFERRED_MODES:
        raise ValueError(
            f"transport.preferred_mode must be one of {sorted(_PREFERRED_MODES)}, got {mode!r}"
        )

    _expect_type(cfg.get("relay_url"), str, "relay_url")

    ac = cfg.get("auto_confirm")
    _expect_type(ac, dict, "auto_confirm")
    _expect_type(ac.get("enabled"), bool, "auto_confirm.enabled")
    _expect_type(ac.get("min_ocr_confidence"), float, "auto_confirm.min_ocr_confidence")
    review = ac.get("require_manual_review")
    _expect_type(review, dict, "auto_confirm.require_manual_review")
    _expect_type(review.get("enabled"), bool, "auto_confirm.require_manual_review.enabled")
    _expect_type(
        review.get("amount_threshold"),
        float,
        "auto_confirm.require_manual_review.amount_threshold",
    )

    bridge = cfg.get("chrome_bridge")
    _expect_type(bridge, dict, "chrome_bridge")
    _expect_type(bridge.get("pairing_token"), str, "chrome_bridge.pairing_token")
    _expect_type(bridge.get("ws_port"), int, "chrome_bridge.ws_port")

    matching = cfg.get("matching")
    _expect_type(matching, dict, "matching")
    _expect_type(
        matching.get("require_account_last4_match"),
        bool,
        "matching.require_account_last4_match",
    )
    _expect_type(
        matching.get("prevent_duplicate_ref_number"),
        bool,
        "matching.prevent_duplicate_ref_number",
    )

    license_cfg = cfg.get("license")
    _expect_type(license_cfg, dict, "license")
    _expect_type(license_cfg.get("token_path"), str, "license.token_path")
    _expect_type(
        license_cfg.get("refresh_interval_days"),
        int,
        "license.refresh_interval_days",
    )


def _ensure_pairing_token(cfg: dict[str, Any]) -> bool:
    """Generate chrome_bridge.pairing_token if missing. Returns True if mutated."""
    token = cfg["chrome_bridge"].get("pairing_token") or ""
    if isinstance(token, str) and token.strip():
        return False
    cfg["chrome_bridge"]["pairing_token"] = secrets.token_hex(_PAIRING_TOKEN_BYTES)
    return True


def _save_config(path: Path, cfg: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save_config(cfg: dict[str, Any], path: Path | str | None = None) -> Path:
    """Validate and persist config. Returns the path written."""
    config_path = Path(path) if path is not None else default_config_path()
    _validate(cfg)
    _save_config(config_path, cfg)
    return config_path


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load config, merge defaults, validate types, and ensure pairing_token exists.

    Resolution order for path:
      1. Explicit ``path`` argument
      2. ``CLIPSYNC_CONFIG`` environment variable
      3. ``%APPDATA%\\ClipSync\\clipsync-config.json`` (platform-appropriate user data dir)
    """
    config_path = Path(path) if path is not None else default_config_path()
    raw: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {config_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"config root must be object, got {type(loaded).__name__}")
        raw = loaded

    cfg = _deep_merge(default_config(), raw)
    _validate(cfg)
    mutated = _ensure_pairing_token(cfg)
    if mutated or not config_path.exists():
        _save_config(config_path, cfg)
    return cfg
