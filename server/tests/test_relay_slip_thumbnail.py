"""Relay forwards optional thumbnail beside signed slip payload."""

from __future__ import annotations

import asyncio

import pytest

import relay_server
from relay_server import create_app


@pytest.fixture
async def client(aiohttp_client):
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


async def test_slip_event_forwards_thumbnail(client):
    payload = {"event_id": "evt-1", "bank": "scb", "amount": 100.0, "parse_failed": False}
    thumb = "aGVsbG8="  # tiny base64

    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "123456789"})
        await pc_ws.receive_json()

        async with client.ws_connect("/") as phone_ws:
            await phone_ws.send_json({"action": "subscribe", "target": "123456789"})
            await phone_ws.receive_json()
            await pc_ws.receive_json()  # phone_joined

            await phone_ws.send_json(
                {
                    "action": "slip_event",
                    "payload": payload,
                    "sig": "sig",
                    "thumbnail_jpeg_b64": thumb,
                }
            )

            msg = await pc_ws.receive_json()
            assert msg["type"] == "slip_event"
            assert msg["payload"] == payload
            assert msg["sig"] == "sig"
            assert msg["thumbnail_jpeg_b64"] == thumb
