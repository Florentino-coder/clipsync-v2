"""Shared HMAC helpers for ClipSync transport auth."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping


def auth_token(shared_secret: str) -> str:
    """X-Auth header / WS auth token (matches mobile LocalSlipServer.authToken)."""
    return hmac.new(
        shared_secret.encode("utf-8"),
        b"clipsync-slip",
        hashlib.sha256,
    ).hexdigest()


def slip_payload_sig(shared_secret: str, payload: Mapping[str, Any]) -> str:
    """HMAC-SHA256 of canonical JSON payload (relay slip_event verification)."""
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hmac.new(
        shared_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_slip_payload_sig(
    shared_secret: str,
    payload: Mapping[str, Any],
    sig: str,
) -> bool:
    if not sig:
        return False
    expected = slip_payload_sig(shared_secret, payload)
    return hmac.compare_digest(expected, sig)
