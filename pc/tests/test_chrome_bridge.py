"""Tests for Chrome Bridge localhost WebSocket server (token auth)."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets
from websockets.exceptions import ConnectionClosed

from clipsync.chrome_bridge import ChromeBridge


TOKEN = "a" * 32


def _cfg(port: int = 0) -> dict:
    return {
        "chrome_bridge": {
            "pairing_token": TOKEN,
            "ws_port": port,
        }
    }


async def _start_bridge(**kwargs) -> ChromeBridge:
    bridge = ChromeBridge(_cfg(port=0), **kwargs)
    await bridge.start()
    return bridge


@pytest.fixture
async def bridge():
    b = await _start_bridge()
    try:
        yield b
    finally:
        await b.stop()


async def test_correct_auth_token_returns_auth_success(bridge: ChromeBridge):
    uri = f"ws://127.0.0.1:{bridge.port}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
        raw = await asyncio.wait_for(ws.recv(), timeout=2)
        assert json.loads(raw) == {"type": "auth_success"}


async def test_wrong_auth_token_closes_connection(bridge: ChromeBridge):
    uri = f"ws://127.0.0.1:{bridge.port}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type": "auth", "token": "wrong-token"}))
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(ws.recv(), timeout=2)


async def test_push_confirm_order_reaches_authenticated_client(bridge: ChromeBridge):
    uri = f"ws://127.0.0.1:{bridge.port}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
        assert json.loads(await ws.recv())["type"] == "auth_success"

        await bridge.push_confirm_order("1234")
        raw = await asyncio.wait_for(ws.recv(), timeout=2)
        assert json.loads(raw) == {"type": "confirm_order", "orderId": "1234"}


async def test_unauthenticated_client_receives_nothing_on_push(bridge: ChromeBridge):
    uri = f"ws://127.0.0.1:{bridge.port}"
    async with websockets.connect(uri) as unauth:
        await bridge.push_confirm_order("1234")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(unauth.recv(), timeout=0.3)


async def test_pending_orders_and_confirm_result_invoke_callbacks():
    pending: list[dict] = []
    results: list[dict] = []

    bridge = await _start_bridge(
        on_pending_orders=pending.append,
        on_confirm_result=results.append,
    )
    try:
        uri = f"ws://127.0.0.1:{bridge.port}"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
            await ws.recv()

            orders_msg = {
                "type": "pending_orders",
                "orders": [{"orderId": "9", "amount": 100.0}],
            }
            await ws.send(json.dumps(orders_msg))

            result_msg = {
                "type": "confirm_result",
                "orderId": "1234",
                "ok": True,
                "reason": None,
            }
            await ws.send(json.dumps(result_msg))

            await asyncio.sleep(0.1)

        assert pending == [orders_msg]
        assert results == [result_msg]
    finally:
        await bridge.stop()


async def test_ping_sent_to_authenticated_clients():
    bridge = await _start_bridge(ping_interval=0.05)
    try:
        uri = f"ws://127.0.0.1:{bridge.port}"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
            assert json.loads(await ws.recv())["type"] == "auth_success"

            raw = await asyncio.wait_for(ws.recv(), timeout=1)
            assert json.loads(raw) == {"type": "ping"}
    finally:
        await bridge.stop()


async def test_binds_loopback_only(bridge: ChromeBridge):
    socks = bridge.server.sockets
    assert socks, "server should expose listening sockets"
    for sock in socks:
        host, _port = sock.getsockname()[:2]
        assert host in ("127.0.0.1", "::1")
