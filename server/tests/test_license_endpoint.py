"""Tests for GET /license/check (force-update + revocation)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import relay_server
from relay_server import create_app


@pytest.fixture
async def client(aiohttp_client, tmp_path, monkeypatch):
    revoked = tmp_path / "revoked_devices.json"
    revoked.write_text(json.dumps({"devices": ["revoked_dev_1"]}), encoding="utf-8")
    monkeypatch.setattr(relay_server, "REVOKED_DEVICES_PATH", revoked)
    monkeypatch.setattr(relay_server, "LICENSE_MIN_REQUIRED_VERSION", "0.9.0")
    monkeypatch.setattr(relay_server, "LICENSE_FORCE_UPDATE", True)
    monkeypatch.setattr(
        relay_server,
        "LICENSE_UPDATE_URL",
        "https://example.com/ClipSyncPC_Setup.exe",
    )
    monkeypatch.setattr(relay_server, "LICENSE_STATUS_DEFAULT", "ok")
    return await aiohttp_client(create_app())


@pytest.fixture(autouse=True)
def reset_relay_globals():
    relay_server.pcs.clear()
    relay_server.phones.clear()
    relay_server.connections.clear()
    yield
    relay_server.pcs.clear()
    relay_server.phones.clear()
    relay_server.connections.clear()


async def test_license_check_ok_not_revoked(client):
    resp = await client.get("/license/check", params={"device_id": "good_dev", "version": "0.9.0"})
    assert resp.status == 200
    data = await resp.json()
    assert data == {
        "min_required_version": "0.9.0",
        "force_update": False,
        "update_url": "https://example.com/ClipSyncPC_Setup.exe",
        "license_status": "ok",
        "revoked": False,
    }


async def test_license_check_force_update_when_version_below_min(client):
    resp = await client.get("/license/check", params={"device_id": "good_dev", "version": "0.8.3"})
    assert resp.status == 200
    data = await resp.json()
    assert data["force_update"] is True
    assert data["min_required_version"] == "0.9.0"
    assert data["revoked"] is False
    assert data["license_status"] == "ok"


async def test_license_check_revoked_device(client):
    resp = await client.get(
        "/license/check",
        params={"device_id": "revoked_dev_1", "version": "0.9.0"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["revoked"] is True
    assert data["license_status"] == "revoked"
    assert data["force_update"] is False


async def test_license_check_missing_params_still_returns_json(client):
    resp = await client.get("/license/check")
    assert resp.status == 200
    data = await resp.json()
    assert "min_required_version" in data
    assert "force_update" in data
    assert "update_url" in data
    assert "license_status" in data
    assert "revoked" in data
    assert data["revoked"] is False


async def test_license_check_does_not_break_websocket(client):
    async with client.ws_connect("/") as ws:
        await ws.send_json({"action": "register", "id": "123456789"})
        msg = await ws.receive_json()
    assert msg["status"] == "registered"


async def test_revoked_list_reloads_from_file(client, tmp_path):
    path: Path = relay_server.REVOKED_DEVICES_PATH
    path.write_text(json.dumps({"devices": ["newly_revoked"]}), encoding="utf-8")

    resp = await client.get(
        "/license/check",
        params={"device_id": "newly_revoked", "version": "1.0.0"},
    )
    data = await resp.json()
    assert data["revoked"] is True
