"""Characterization tests for relay_server v1 WebSocket protocol."""

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


async def test_pc_register_success(client):
    async with client.ws_connect("/") as ws:
        await ws.send_json({"action": "register", "id": "123456789"})
        msg = await ws.receive_json()

    assert msg == {
        "status": "registered",
        "id": "123-456-789",
        "phones": 0,
    }


async def test_pc_register_accepts_dashed_id(client):
    async with client.ws_connect("/") as ws:
        await ws.send_json({"action": "register", "id": "123-456-789"})
        msg = await ws.receive_json()

    assert msg["status"] == "registered"
    assert msg["id"] == "123-456-789"


async def test_pc_register_invalid_id(client):
    async with client.ws_connect("/") as ws:
        await ws.send_json({"action": "register", "id": "bad"})
        msg = await ws.receive_json()

    assert msg == {"error": "invalid_id"}


async def test_phone_subscribe_invalid_target(client):
    async with client.ws_connect("/") as ws:
        await ws.send_json({"action": "subscribe", "target": "nope"})
        msg = await ws.receive_json()

    assert msg == {"error": "invalid_target"}


async def test_register_subscribe_clip_fanout(client):
    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "123456789"})
        reg = await pc_ws.receive_json()
        assert reg == {
            "status": "registered",
            "id": "123-456-789",
            "phones": 0,
        }

        async with client.ws_connect("/") as phone_ws:
            await phone_ws.send_json({"action": "subscribe", "target": "123456789"})
            sub = await phone_ws.receive_json()
            assert sub == {
                "status": "subscribed",
                "target": "123-456-789",
                "online": True,
            }

            joined = await pc_ws.receive_json()
            assert joined == {"type": "phone_joined", "count": 1}

            await pc_ws.send_json({"action": "clip", "text": "hello relay"})
            clip = await phone_ws.receive_json()
            assert clip == {"type": "clip", "text": "hello relay"}


async def test_phone_subscribe_offline_then_pc_online(client):
    async with client.ws_connect("/") as phone_ws:
        await phone_ws.send_json({"action": "subscribe", "target": "987654321"})
        sub = await phone_ws.receive_json()
        assert sub == {
            "status": "subscribed",
            "target": "987-654-321",
            "online": False,
        }

        async with client.ws_connect("/") as pc_ws:
            await pc_ws.send_json({"action": "register", "id": "987654321"})
            reg = await pc_ws.receive_json()
            assert reg["phones"] == 1

            online = await phone_ws.receive_json()
            assert online == {"type": "pc_online"}


async def test_clip_fanout_to_multiple_phones(client):
    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "111222333"})
        await pc_ws.receive_json()

        async with client.ws_connect("/") as phone_a:
            await phone_a.send_json({"action": "subscribe", "target": "111222333"})
            await phone_a.receive_json()
            await pc_ws.receive_json()

            async with client.ws_connect("/") as phone_b:
                await phone_b.send_json({"action": "subscribe", "target": "111222333"})
                await phone_b.receive_json()
                await pc_ws.receive_json()

                await pc_ws.send_json({"action": "clip", "text": "broadcast"})

                clip_a = await phone_a.receive_json()
                clip_b = await phone_b.receive_json()
                assert clip_a == {"type": "clip", "text": "broadcast"}
                assert clip_b == {"type": "clip", "text": "broadcast"}


async def test_heartbeat_ack(client):
    async with client.ws_connect("/") as ws:
        await ws.send_json({"action": "heartbeat"})
        msg = await ws.receive_json()

    assert msg == {"type": "heartbeat_ack"}


async def test_clip_too_large(client):
    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "123456789"})
        await pc_ws.receive_json()

        oversized = "x" * (100 * 1024 + 1)
        await pc_ws.send_json({"action": "clip", "text": oversized})
        msg = await pc_ws.receive_json()

    assert msg == {"error": "too_large"}


async def test_clip_without_register_is_silent(client):
    async with client.ws_connect("/") as ws:
        await ws.send_json({"action": "clip", "text": "orphan"})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.receive_json(), timeout=0.2)


async def test_phone_clip_does_not_reach_pc(client):
    """Clip is PC -> phone only; phones cannot push clips to PC today."""
    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "123456789"})
        await pc_ws.receive_json()

        async with client.ws_connect("/") as phone_ws:
            await phone_ws.send_json({"action": "subscribe", "target": "123456789"})
            await phone_ws.receive_json()
            await pc_ws.receive_json()

            await phone_ws.send_json({"action": "clip", "text": "from phone"})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(pc_ws.receive_json(), timeout=0.2)
