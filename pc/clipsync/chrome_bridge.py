"""Chrome Extension bridge — localhost-only WebSocket server with token auth."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Mapping, Optional, Set

import websockets
from websockets.server import WebSocketServer, WebSocketServerProtocol

logger = logging.getLogger(__name__)

PING_INTERVAL_DEFAULT = 20.0

MessageCallback = Callable[[dict[str, Any]], Any]


class ChromeBridge:
    """WS server bound to 127.0.0.1 for the ClipSync Chrome extension."""

    def __init__(
        self,
        cfg: Mapping[str, Any],
        *,
        on_pending_orders: Optional[MessageCallback] = None,
        on_confirm_result: Optional[MessageCallback] = None,
        ping_interval: float = PING_INTERVAL_DEFAULT,
    ) -> None:
        bridge_cfg = cfg["chrome_bridge"]
        self._token = str(bridge_cfg["pairing_token"])
        self._port = int(bridge_cfg["ws_port"])
        self._on_pending_orders = on_pending_orders
        self._on_confirm_result = on_confirm_result
        self._ping_interval = float(ping_interval)

        self._server: Optional[WebSocketServer] = None
        self._clients: Set[WebSocketServerProtocol] = set()
        self._ping_task: Optional[asyncio.Task[None]] = None

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            return self._port
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def server(self) -> WebSocketServer:
        if self._server is None:
            raise RuntimeError("ChromeBridge is not started")
        return self._server

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await websockets.serve(
            self._handler,
            "127.0.0.1",
            self._port,
        )
        self._ping_task = asyncio.create_task(self._ping_loop(), name="chrome-bridge-ping")

    async def stop(self) -> None:
        if self._ping_task is not None:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        self._clients.clear()

    async def push_confirm_order(self, order_id: str) -> None:
        payload = json.dumps({"type": "confirm_order", "orderId": str(order_id)})
        await self._broadcast(payload)

    async def _broadcast(self, payload: str) -> None:
        if not self._clients:
            return
        dead: list[WebSocketServerProtocol] = []
        for ws in list(self._clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _ping_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._ping_interval)
                await self._broadcast(json.dumps({"type": "ping"}))
        except asyncio.CancelledError:
            raise

    async def _invoke(self, callback: Optional[MessageCallback], data: dict[str, Any]) -> None:
        if callback is None:
            return
        result = callback(data)
        if isinstance(result, Awaitable):
            await result

    async def _handler(self, websocket: WebSocketServerProtocol) -> None:
        authenticated = False
        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    logger.debug("chrome_bridge: ignoring non-JSON message")
                    continue
                if not isinstance(data, dict):
                    continue

                msg_type = data.get("type")
                if not authenticated:
                    if msg_type != "auth":
                        continue
                    if data.get("token") == self._token:
                        authenticated = True
                        self._clients.add(websocket)
                        await websocket.send(json.dumps({"type": "auth_success"}))
                    else:
                        await websocket.close()
                        return
                    continue

                if msg_type == "pending_orders":
                    await self._invoke(self._on_pending_orders, data)
                elif msg_type == "confirm_result":
                    await self._invoke(self._on_confirm_result, data)
                elif msg_type == "pong":
                    continue
        finally:
            self._clients.discard(websocket)
