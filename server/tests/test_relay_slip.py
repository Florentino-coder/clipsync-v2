"""Tests for relay phone → PC slip_event forwarding."""

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


async def test_slip_event_phone_to_pc(client):
    """Subscribed phone slip_event is forwarded to the registered PC (sig opaque)."""
    payload = {
        "event_id": "evt-1",
        "captured_at": "2026-07-22T11:00:00Z",
        "bank": "kbank",
        "amount": 150.0,
        "sender_name": "Alice",
        "receiver_account_last4": "1234",
        "ref_number": "REF001",
        "ocr_confidence": 0.95,
        "parse_failed": False,
    }
    sig = "deadbeefhmac"

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
                    "sig": sig,
                }
            )

            msg = await asyncio.wait_for(pc_ws.receive_json(), timeout=1.0)
            assert msg == {
                "type": "slip_event",
                "payload": payload,
                "sig": sig,
            }


async def test_slip_event_without_subscribe_is_silent(client):
    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "123456789"})
        await pc_ws.receive_json()

        async with client.ws_connect("/") as phone_ws:
            await phone_ws.send_json(
                {
                    "action": "slip_event",
                    "payload": {"event_id": "x"},
                    "sig": "sig",
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(pc_ws.receive_json(), timeout=0.2)


async def test_slip_event_when_pc_offline_is_silent(client):
    async with client.ws_connect("/") as phone_ws:
        await phone_ws.send_json({"action": "subscribe", "target": "123456789"})
        await phone_ws.receive_json()

        await phone_ws.send_json(
            {
                "action": "slip_event",
                "payload": {"event_id": "x"},
                "sig": "sig",
            }
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(phone_ws.receive_json(), timeout=0.2)
