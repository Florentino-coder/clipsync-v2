"""Tests for offline-first license gate + periodic refresh / force-update / revocation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from clipsync.license import issue_token
from clipsync.license_refresh import (
    SOFT_UPDATE_DAYS,
    LicenseGateResult,
    evaluate_license_gate,
)


def _utcnow() -> datetime:
    return datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def _keypair():
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def _token(private_key, *, days: int = 30, device_id: str = "dev_abc123", issued_at=None):
    return issue_token(
        private_key,
        device_id=device_id,
        customer="cust_001",
        days=days,
        issued_at=issued_at or _utcnow(),
    )


def _ok_response(**overrides: Any) -> dict[str, Any]:
    data = {
        "min_required_version": "0.9.0",
        "force_update": False,
        "update_url": "https://example.com/update.exe",
        "license_status": "ok",
        "revoked": False,
    }
    data.update(overrides)
    return data


def test_offline_valid_skips_network_when_not_due(tmp_path: Path):
    private_key, public_key = _keypair()
    token = _token(private_key)
    state_path = tmp_path / "license_state.json"
    state_path.write_text(
        json.dumps({"last_checked_at": _utcnow().isoformat()}),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fetch(_url: str) -> dict[str, Any]:
        calls.append(_url)
        raise AssertionError("must not hit network before refresh interval")

    result = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.9.0",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow(),
        public_key=public_key,
        fetch=fetch,
    )

    assert result.allowed is True
    assert result.refreshed is False
    assert calls == []
    assert result.badge == "green"


def test_refresh_due_hits_network_and_updates_state(tmp_path: Path):
    private_key, public_key = _keypair()
    token = _token(private_key)
    state_path = tmp_path / "license_state.json"
    # last check 4 days ago → due (interval 3)
    old = _utcnow() - timedelta(days=4)
    state_path.write_text(json.dumps({"last_checked_at": old.isoformat()}), encoding="utf-8")
    calls: list[str] = []

    def fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        return _ok_response()

    result = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.9.0",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow(),
        public_key=public_key,
        fetch=fetch,
    )

    assert result.allowed is True
    assert result.refreshed is True
    assert len(calls) == 1
    assert "device_id=dev_abc123" in calls[0]
    assert "version=0.9.0" in calls[0]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["last_checked_at"].startswith("2026-07-22")


def test_revoked_locks_immediately(tmp_path: Path):
    private_key, public_key = _keypair()
    token = _token(private_key)
    state_path = tmp_path / "license_state.json"

    def fetch(_url: str) -> dict[str, Any]:
        return _ok_response(revoked=True, license_status="revoked")

    result = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.9.0",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow(),
        public_key=public_key,
        fetch=fetch,
    )

    assert result.allowed is False
    assert result.reason == "revoked"
    assert result.badge == "red"
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["revoked"] is True


def test_cached_revoked_locks_without_network(tmp_path: Path):
    private_key, public_key = _keypair()
    token = _token(private_key)
    state_path = tmp_path / "license_state.json"
    state_path.write_text(
        json.dumps(
            {
                "last_checked_at": _utcnow().isoformat(),
                "revoked": True,
            }
        ),
        encoding="utf-8",
    )

    def fetch(_url: str) -> dict[str, Any]:
        raise AssertionError("revoked cache must not require network")

    result = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.9.0",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow(),
        public_key=public_key,
        fetch=fetch,
    )

    assert result.allowed is False
    assert result.reason == "revoked"


def test_force_update_soft_warn_then_hard_block(tmp_path: Path):
    private_key, public_key = _keypair()
    token = _token(private_key)
    state_path = tmp_path / "license_state.json"
    assert SOFT_UPDATE_DAYS == 7

    def fetch(_url: str) -> dict[str, Any]:
        return _ok_response(force_update=True, min_required_version="0.9.0")

    # First sighting → soft warn, still allowed
    first = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.8.3",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow(),
        public_key=public_key,
        fetch=fetch,
    )
    assert first.allowed is True
    assert first.warning is not None
    assert "update" in first.warning.lower() or "อัพเดท" in first.warning
    assert first.update_url == "https://example.com/update.exe"
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["first_warned_at"]

    # Still within soft window
    mid = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.8.3",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow() + timedelta(days=3),
        public_key=public_key,
        fetch=fetch,
    )
    assert mid.allowed is True
    assert mid.warning is not None

    # After soft period → hard block
    hard = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.8.3",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow() + timedelta(days=SOFT_UPDATE_DAYS),
        public_key=public_key,
        fetch=fetch,
    )
    assert hard.allowed is False
    assert hard.reason == "force_update"
    assert hard.badge == "red"
    assert hard.update_url == "https://example.com/update.exe"


def test_force_update_false_does_not_block_old_version(tmp_path: Path):
    private_key, public_key = _keypair()
    token = _token(private_key)
    state_path = tmp_path / "license_state.json"

    def fetch(_url: str) -> dict[str, Any]:
        return _ok_response(force_update=False, min_required_version="0.9.0")

    result = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.8.0",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow(),
        public_key=public_key,
        fetch=fetch,
    )
    assert result.allowed is True
    assert result.reason is None


def test_network_failure_keeps_offline_valid(tmp_path: Path):
    private_key, public_key = _keypair()
    token = _token(private_key)
    state_path = tmp_path / "license_state.json"

    def fetch(_url: str) -> dict[str, Any]:
        raise OSError("offline")

    result = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.9.0",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow(),
        public_key=public_key,
        fetch=fetch,
    )
    assert result.allowed is True
    assert result.refreshed is False


def test_invalid_local_token_blocks_without_network(tmp_path: Path):
    _, public_key = _keypair()
    state_path = tmp_path / "license_state.json"
    calls: list[str] = []

    def fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        return _ok_response()

    result = evaluate_license_gate(
        token="not.a.valid.token",
        device_id="dev_abc123",
        app_version="0.9.0",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=_utcnow(),
        public_key=public_key,
        fetch=fetch,
    )
    assert result.allowed is False
    assert result.reason == "bad_signature"
    assert calls == []
    assert result.badge == "red"


def test_badge_yellow_when_days_left_le_5(tmp_path: Path):
    private_key, public_key = _keypair()
    now = _utcnow()
    token = _token(private_key, days=5, issued_at=now)
    state_path = tmp_path / "license_state.json"
    state_path.write_text(
        json.dumps({"last_checked_at": now.isoformat()}),
        encoding="utf-8",
    )

    result = evaluate_license_gate(
        token=token,
        device_id="dev_abc123",
        app_version="0.9.0",
        check_url="https://relay.example/license/check",
        state_path=state_path,
        refresh_interval_days=3,
        now=now,
        public_key=public_key,
        fetch=lambda _u: (_ for _ in ()).throw(AssertionError("no net")),
    )
    assert result.allowed is True
    assert result.days_left == 5
    assert result.badge == "yellow"


def test_license_gate_result_type_exported():
    assert LicenseGateResult is not None
