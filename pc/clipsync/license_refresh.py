"""Periodic license refresh, force-update soft/hard gate, and revocation lock.

Offline-first: local Ed25519 verify runs before any network call. Refresh hits
``GET /license/check`` only when ``refresh_interval_days`` has elapsed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from clipsync.license import VerifyResult, verify_token

SOFT_UPDATE_DAYS = 7
YELLOW_DAYS_THRESHOLD = 5
DEFAULT_REFRESH_INTERVAL_DAYS = 3

FetchFn = Callable[[str], Mapping[str, Any]]


@dataclass(frozen=True)
class LicenseGateResult:
    allowed: bool
    reason: Optional[str] = None
    warning: Optional[str] = None
    update_url: Optional[str] = None
    days_left: Optional[int] = None
    badge: str = "green"
    refreshed: bool = False
    customer: Optional[str] = None


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def parse_version(value: str) -> tuple[int, int, int, int]:
    base, _, build = str(value or "").partition("+")
    parts: list[int] = []
    for raw in base.split("."):
        digits = "".join(ch for ch in raw if ch.isdigit())
        parts.append(int(digits or "0"))
    while len(parts) < 3:
        parts.append(0)
    build_digits = "".join(ch for ch in build if ch.isdigit())
    return parts[0], parts[1], parts[2], int(build_digits or "0")


def version_less(left: str, right: str) -> bool:
    return parse_version(left) < parse_version(right)


def license_check_url_from_relay(relay_url: str) -> str:
    """Map ``wss://host/...`` / ``ws://host`` to ``https://host/license/check``."""
    raw = (relay_url or "").strip()
    if raw.startswith("wss://"):
        raw = "https://" + raw[len("wss://") :]
    elif raw.startswith("ws://"):
        raw = "http://" + raw[len("ws://") :]
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid relay_url: {relay_url!r}")
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "/license/check", "", "", "")
    )


def _default_fetch(url: str) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "ClipSyncPC/license"})
    with urllib.request.urlopen(request, timeout=8) as response:
        body = response.read(64 * 1024)
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("license check response must be an object")
    return data


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _save_state(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(data), indent=2), encoding="utf-8")


def _badge_for(*, allowed: bool, days_left: Optional[int]) -> str:
    if not allowed:
        return "red"
    if days_left is not None and days_left <= YELLOW_DAYS_THRESHOLD:
        return "yellow"
    return "green"


def _refresh_due(state: Mapping[str, Any], *, now: datetime, interval_days: int) -> bool:
    raw = state.get("last_checked_at")
    if not raw:
        return True
    last = _parse_iso(str(raw))
    if last is None:
        return True
    return now >= last + timedelta(days=interval_days)


def _build_check_url(check_url: str, *, device_id: str, version: str) -> str:
    parts = urllib.parse.urlparse(check_url)
    query = urllib.parse.urlencode({"device_id": device_id, "version": version})
    return urllib.parse.urlunparse(
        (parts.scheme, parts.netloc, parts.path or "/license/check", "", query, "")
    )


def _apply_force_update(
    *,
    state: dict[str, Any],
    app_version: str,
    now: datetime,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Return (hard_blocked, warning, update_url)."""
    update_url = str(state.get("update_url") or "") or None
    min_required = str(state.get("min_required_version") or "")
    needs_update = bool(state.get("force_update"))
    if needs_update and min_required and not version_less(app_version, min_required):
        needs_update = False

    if not needs_update:
        state.pop("first_warned_at", None)
        state["force_update"] = False
        return False, None, update_url

    warned_raw = state.get("first_warned_at")
    first_warned = _parse_iso(str(warned_raw)) if warned_raw else None
    if first_warned is None:
        state["first_warned_at"] = now.isoformat()
        first_warned = now

    if now >= first_warned + timedelta(days=SOFT_UPDATE_DAYS):
        return True, None, update_url

    return (
        False,
        "Update required — please update soon (soft warning before hard block)",
        update_url,
    )


def evaluate_license_gate(
    *,
    token: str,
    device_id: str,
    app_version: str,
    check_url: str,
    state_path: Path,
    refresh_interval_days: int = DEFAULT_REFRESH_INTERVAL_DAYS,
    now: Optional[datetime] = None,
    public_key: Optional[Union[Ed25519PublicKey, bytes]] = None,
    fetch: Optional[FetchFn] = None,
) -> LicenseGateResult:
    check_now = _ensure_aware(now or datetime.now(timezone.utc))
    local: VerifyResult = verify_token(
        token,
        device_id=device_id,
        now=check_now,
        public_key=public_key,
    )

    if not local.valid:
        return LicenseGateResult(
            allowed=False,
            reason=local.reason or "invalid",
            days_left=local.days_left,
            badge="red",
            customer=local.customer,
        )

    state = _load_state(state_path)
    if state.get("revoked") is True:
        return LicenseGateResult(
            allowed=False,
            reason="revoked",
            days_left=local.days_left,
            badge="red",
            customer=local.customer,
        )

    refreshed = False
    fetch_fn = fetch or _default_fetch
    warning = local.warning

    if _refresh_due(state, now=check_now, interval_days=refresh_interval_days):
        url = _build_check_url(check_url, device_id=device_id, version=app_version)
        try:
            remote = dict(fetch_fn(url))
            refreshed = True
            state["last_checked_at"] = check_now.isoformat()
            state["revoked"] = bool(remote.get("revoked"))
            state["force_update"] = bool(remote.get("force_update"))
            state["min_required_version"] = str(
                remote.get("min_required_version") or state.get("min_required_version") or ""
            )
            state["update_url"] = str(remote.get("update_url") or state.get("update_url") or "")
            state["license_status"] = str(remote.get("license_status") or "ok")
            _save_state(state_path, state)

            if state["revoked"]:
                return LicenseGateResult(
                    allowed=False,
                    reason="revoked",
                    days_left=local.days_left,
                    badge="red",
                    refreshed=True,
                    customer=local.customer,
                    update_url=state.get("update_url") or None,
                )
        except (OSError, urllib.error.URLError, ValueError, TypeError, json.JSONDecodeError):
            refreshed = False

    hard_block, force_warning, update_url = _apply_force_update(
        state=state,
        app_version=app_version,
        now=check_now,
    )
    # Persist first_warned_at / cleared force_update even when refresh skipped.
    _save_state(state_path, state)

    if hard_block:
        return LicenseGateResult(
            allowed=False,
            reason="force_update",
            update_url=update_url,
            days_left=local.days_left,
            badge="red",
            refreshed=refreshed,
            customer=local.customer,
        )

    if force_warning:
        warning = force_warning

    return LicenseGateResult(
        allowed=True,
        reason=None,
        warning=warning,
        update_url=update_url if force_warning else (update_url if state.get("force_update") else None),
        days_left=local.days_left,
        badge=_badge_for(allowed=True, days_left=local.days_left),
        refreshed=refreshed,
        customer=local.customer,
    )
