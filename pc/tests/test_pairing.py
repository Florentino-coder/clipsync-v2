"""Tests for ClipSync pairing URL v2 (shared secret)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from clipsync import legacy


def test_pairing_url_includes_id_and_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    secret_file = tmp_path / "clipsync.secret"
    monkeypatch.setattr(legacy, "SECRET_FILE", secret_file)

    url = legacy.pairing_url("123-456-789")
    parsed = urlparse(url)
    assert parsed.scheme == "clipsync"
    assert parsed.hostname == "pair"
    qs = parse_qs(parsed.query)
    assert qs["id"] == ["123456789"]
    secret = qs["secret"][0]
    assert len(secret) == 32
    assert all(c in "0123456789abcdef" for c in secret)
    assert secret_file.read_text(encoding="utf-8").strip() == secret


def test_shared_secret_persisted_across_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    secret_file = tmp_path / "clipsync.secret"
    monkeypatch.setattr(legacy, "SECRET_FILE", secret_file)

    first = legacy.load_or_create_shared_secret()
    second = legacy.load_or_create_shared_secret()
    assert first == second
    assert len(first) == 32
    assert secret_file.read_text(encoding="utf-8").strip() == first


def test_pairing_url_reuses_existing_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    secret_file = tmp_path / "clipsync.secret"
    secret_file.write_text("abcdef0123456789abcdef0123456789", encoding="utf-8")
    monkeypatch.setattr(legacy, "SECRET_FILE", secret_file)

    url = legacy.pairing_url("987654321")
    qs = parse_qs(urlparse(url).query)
    assert qs["id"] == ["987654321"]
    assert qs["secret"] == ["abcdef0123456789abcdef0123456789"]
