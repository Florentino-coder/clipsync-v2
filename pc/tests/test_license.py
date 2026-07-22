"""Ed25519 offline license token issue/verify."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from clipsync.license import GRACE_DAYS, issue_token, verify_token


def _utcnow() -> datetime:
    return datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def _keypair():
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def test_valid_token():
    private_key, public_key = _keypair()
    now = _utcnow()
    token = issue_token(
        private_key,
        device_id="dev_abc123",
        customer="cust_001",
        days=30,
        issued_at=now,
    )

    result = verify_token(
        token,
        device_id="dev_abc123",
        now=now,
        public_key=public_key,
    )

    assert result.valid is True
    assert result.days_left == 30
    assert result.reason is None
    assert result.warning is None


def test_expired_within_grace():
    private_key, public_key = _keypair()
    now = _utcnow()
    issued_at = now - timedelta(days=10)
    token = issue_token(
        private_key,
        device_id="dev_abc123",
        customer="cust_001",
        days=8,
        issued_at=issued_at,
    )
    # Expired 2 days ago; still within GRACE_DAYS (3).
    assert GRACE_DAYS == 3

    result = verify_token(
        token,
        device_id="dev_abc123",
        now=now,
        public_key=public_key,
    )

    assert result.valid is True
    assert result.warning is not None
    assert "grace" in result.warning.lower()


def test_expired_beyond_grace():
    private_key, public_key = _keypair()
    now = _utcnow()
    issued_at = now - timedelta(days=20)
    token = issue_token(
        private_key,
        device_id="dev_abc123",
        customer="cust_001",
        days=10,
        issued_at=issued_at,
    )
    # Expired 10 days ago — beyond 3-day grace.

    result = verify_token(
        token,
        device_id="dev_abc123",
        now=now,
        public_key=public_key,
    )

    assert result.valid is False
    assert result.reason == "expired"


def test_tampered_payload():
    private_key, public_key = _keypair()
    now = _utcnow()
    token = issue_token(
        private_key,
        device_id="dev_abc123",
        customer="cust_001",
        days=30,
        issued_at=now,
    )
    payload_b64, signature_b64 = token.split(".", 1)
    # Flip one character in the payload segment (1-byte-ish tamper of encoded form).
    chars = list(payload_b64)
    chars[0] = "A" if chars[0] != "A" else "B"
    tampered = "".join(chars) + "." + signature_b64

    result = verify_token(
        tampered,
        device_id="dev_abc123",
        now=now,
        public_key=public_key,
    )

    assert result.valid is False
    assert result.reason == "bad_signature"


def test_wrong_device():
    private_key, public_key = _keypair()
    now = _utcnow()
    token = issue_token(
        private_key,
        device_id="dev_abc123",
        customer="cust_001",
        days=30,
        issued_at=now,
    )

    result = verify_token(
        token,
        device_id="other_device",
        now=now,
        public_key=public_key,
    )

    assert result.valid is False
    assert result.reason == "device_mismatch"
