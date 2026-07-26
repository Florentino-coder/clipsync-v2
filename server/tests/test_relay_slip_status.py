"""PC slip_status fans out to all subscribed phones (typed, not clip)."""

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


STATUS = {
    "job_id": "evt-001",
    "order_id": "W-1001",
    "amount": "350.00",
    "stage": "done",
    "message_th": "สำเร็จ",
    "reason": "",
    "ts": 1720000000,
}


async def test_slip_status_fanout_to_all_phones(client):
    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "123456789"})
        await pc_ws.receive_json()

        async with client.ws_connect("/") as phone_a:
            await phone_a.send_json({"action": "subscribe", "target": "123456789"})
            await phone_a.receive_json()
            await pc_ws.receive_json()

            async with client.ws_connect("/") as phone_b:
                await phone_b.send_json({"action": "subscribe", "target": "123456789"})
                await phone_b.receive_json()
                await pc_ws.receive_json()

                await pc_ws.send_json({"action": "slip_status", **STATUS})

                for phone in (phone_a, phone_b):
                    msg = await phone.receive_json()
                    assert msg["type"] == "slip_status"
                    assert msg["order_id"] == "W-1001"
                    assert msg["job_id"] == "evt-001"
                    assert msg["stage"] == "done"
                    assert msg["message_th"] == "สำเร็จ"
                    assert msg["amount"] == "350.00"
                    assert msg["ts"] == 1720000000


async def test_slip_status_requires_registered_pc(client):
    async with client.ws_connect("/") as phone_ws:
        await phone_ws.send_json({"action": "subscribe", "target": "123456789"})
        await phone_ws.receive_json()

        async with client.ws_connect("/") as rogue:
            await rogue.send_json({"action": "slip_status", **STATUS})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(phone_ws.receive_json(), timeout=0.2)


async def test_slip_status_rejects_bad_stage(client):
    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "123456789"})
        await pc_ws.receive_json()

        async with client.ws_connect("/") as phone_ws:
            await phone_ws.send_json({"action": "subscribe", "target": "123456789"})
            await phone_ws.receive_json()
            await pc_ws.receive_json()

            bad = {**STATUS, "stage": "nope"}
            await pc_ws.send_json({"action": "slip_status", **bad})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(phone_ws.receive_json(), timeout=0.2)


async def test_slip_status_requires_job_or_order_id(client):
    async with client.ws_connect("/") as pc_ws:
        await pc_ws.send_json({"action": "register", "id": "123456789"})
        await pc_ws.receive_json()

        async with client.ws_connect("/") as phone_ws:
            await phone_ws.send_json({"action": "subscribe", "target": "123456789"})
            await phone_ws.receive_json()
            await pc_ws.receive_json()

            bad = {**STATUS, "job_id": "", "order_id": ""}
            await pc_ws.send_json({"action": "slip_status", **bad})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(phone_ws.receive_json(), timeout=0.2)
