"""Offline-first Ed25519 license token issue and verify.

The embedded PUBLIC_KEY_RAW is a **development/test** keypair public half.
Replace it with the production public key before shipping; keep the matching
private key outside the repository (see CLIPSYNC_LICENSE_KEY for issuing).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from clipsync.device_id import get_device_id

GRACE_DAYS = 3

# DEV/TEST ONLY — replace with production Ed25519 public key (32 raw bytes) before release.
PUBLIC_KEY_RAW = bytes.fromhex(
    "9903b6effe3c85d1abb1c1e5718f635ae6ac8a4aba5a571aaa00a01fba477fdb"
)


@dataclass(frozen=True)
class VerifyResult:
    valid: bool
    days_left: Optional[int] = None
    warning: Optional[str] = None
    reason: Optional[str] = None
    customer: Optional[str] = None
    expires_at: Optional[datetime] = None


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso(value: str) -> datetime:
    # Accept trailing Z.
    normalized = value.replace("Z", "+00:00")
    return _ensure_aware(datetime.fromisoformat(normalized))


def _public_key_from(
    public_key: Optional[Union[Ed25519PublicKey, bytes]] = None,
) -> Ed25519PublicKey:
    if public_key is None:
        return Ed25519PublicKey.from_public_bytes(PUBLIC_KEY_RAW)
    if isinstance(public_key, (bytes, bytearray)):
        return Ed25519PublicKey.from_public_bytes(bytes(public_key))
    return public_key


def issue_token(
    private_key: Ed25519PrivateKey,
    *,
    device_id: str,
    customer: str,
    days: int,
    issued_at: Optional[datetime] = None,
) -> str:
    """Create a compact token: ``base64url(payload).base64url(signature)``."""
    if days < 1:
        raise ValueError("days must be >= 1")
    issued = _ensure_aware(issued_at or datetime.now(timezone.utc))
    expires = issued + timedelta(days=days)
    payload: Mapping[str, Any] = {
        "customer": customer,
        "device_id": device_id,
        "expires_at": expires.isoformat(),
        "issued_at": issued.isoformat(),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    signature = private_key.sign(payload_bytes)
    return f"{_b64encode(payload_bytes)}.{_b64encode(signature)}"


def verify_token(
    token: str,
    *,
    device_id: Optional[str] = None,
    now: Optional[datetime] = None,
    public_key: Optional[Union[Ed25519PublicKey, bytes]] = None,
) -> VerifyResult:
    """Verify an offline license token.

    ``device_id`` may be injected for tests; otherwise uses ``get_device_id()``.
    ``public_key`` may be injected for tests; otherwise uses embedded PUBLIC_KEY_RAW.
    """
    expected_device = device_id if device_id is not None else get_device_id()
    check_now = _ensure_aware(now or datetime.now(timezone.utc))
    pub = _public_key_from(public_key)

    try:
        payload_b64, signature_b64 = token.split(".", 1)
        payload_bytes = _b64decode(payload_b64)
        signature = _b64decode(signature_b64)
    except (ValueError, TypeError):
        return VerifyResult(valid=False, reason="bad_signature")

    try:
        pub.verify(signature, payload_bytes)
    except InvalidSignature:
        return VerifyResult(valid=False, reason="bad_signature")
    except Exception:
        return VerifyResult(valid=False, reason="bad_signature")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
        token_device = str(payload["device_id"])
        customer = str(payload["customer"])
        expires_at = _parse_iso(str(payload["expires_at"]))
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return VerifyResult(valid=False, reason="bad_signature")

    if token_device != expected_device:
        return VerifyResult(valid=False, reason="device_mismatch")

    days_left = (expires_at - check_now).days

    if check_now <= expires_at:
        return VerifyResult(
            valid=True,
            days_left=days_left,
            customer=customer,
            expires_at=expires_at,
        )

    grace_deadline = expires_at + timedelta(days=GRACE_DAYS)
    if check_now <= grace_deadline:
        return VerifyResult(
            valid=True,
            days_left=days_left,
            warning="License expired; within grace period",
            customer=customer,
            expires_at=expires_at,
        )

    return VerifyResult(
        valid=False,
        reason="expired",
        days_left=days_left,
        customer=customer,
        expires_at=expires_at,
    )
