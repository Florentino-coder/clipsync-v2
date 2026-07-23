"""Tests for APK USB share helpers (no real HTTP bind required for most)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from clipsync.apk_installer import (
    apk_download_url,
    find_bundled_apk,
    find_usb_tether_pc_ip,
)


def test_find_bundled_apk_in_apk_subdir(tmp_path: Path):
    apk_dir = tmp_path / "apk"
    apk_dir.mkdir()
    apk = apk_dir / "ClipSync-slip-debug.apk"
    apk.write_bytes(b"fake-apk")
    assert find_bundled_apk(tmp_path) == apk


def test_find_bundled_apk_missing(tmp_path: Path):
    assert find_bundled_apk(tmp_path) is None


def test_apk_download_url():
    assert apk_download_url("192.168.42.129", "ClipSync.apk") == (
        "http://192.168.42.129:8788/ClipSync.apk"
    )


def test_download_apk_from_url(tmp_path: Path, monkeypatch):
    dest_dir = tmp_path / "apk"
    payload = b"PK" + (b"0" * 2000)  # pretend zip/apk

    class Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=120.0):
        assert "clipsync-v2" in getattr(req, "full_url", str(req))
        return Resp()

    import urllib.request as ur

    monkeypatch.setattr(ur, "urlopen", fake_urlopen)
    from clipsync.apk_installer import download_apk_from_url

    out = download_apk_from_url(
        "https://github.com/Florentino-coder/clipsync-v2/releases/download/slip-test-latest/ClipSync-slip.apk",
        dest_dir=dest_dir,
    )
    assert out == dest_dir / "ClipSync-slip.apk"
    assert out.read_bytes() == payload
    assert out.stat().st_size >= 1000


def test_find_usb_tether_pc_ip_uses_ndis():
    class Addr:
        def __init__(self, family, address, netmask="255.255.255.0"):
            self.family = family
            self.address = address
            self.netmask = netmask

    class Stat:
        isup = True

    import socket

    def fake_addrs():
        return {
            "Remote NDIS": [Addr(socket.AF_INET, "192.168.42.129")],
            "Wi-Fi": [Addr(socket.AF_INET, "30.31.3.202")],
        }

    def fake_stats():
        return {"Remote NDIS": Stat(), "Wi-Fi": Stat()}

    with patch("clipsync.apk_installer.psutil.net_if_addrs", fake_addrs):
        with patch("clipsync.apk_installer.psutil.net_if_stats", fake_stats):
            assert find_usb_tether_pc_ip() == "192.168.42.129"
