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
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def connected_clients(self) -> int:
        return len(self._clients)

    def schedule(self, coro: Awaitable[Any]) -> Any:
        """Run a coroutine on the bridge event loop from another thread."""
        if self._loop is None:
            raise RuntimeError("ChromeBridge is not started")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

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
        self._loop = asyncio.get_running_loop()
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

    async def push_confirm_order(
        self,
        order_id: str,
        *,
        amount: Any = None,
        ref_number: Any = None,
        slip: Optional[Mapping[str, Any]] = None,
        event_id: Any = None,
    ) -> int:
        """Push confirm_order to extension clients. Returns connected client count."""
        payload: dict[str, Any] = {
            "type": "confirm_order",
            "orderId": "" if order_id is None else str(order_id),
        }
        if amount is not None and str(amount).strip():
            payload["amount"] = str(amount).strip()
        if ref_number is not None and str(ref_number).strip():
            payload["refNumber"] = str(ref_number).strip()
        if event_id is not None and str(event_id).strip():
            payload["event_id"] = str(event_id).strip()
        if slip:
            payload["slip"] = dict(slip)
        if not self._clients:
            return 0
        await self._broadcast(json.dumps(payload))
        return len(self._clients)

    async def push_site_profiles(self, profiles: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> int:
        """Push validated site profiles to authenticated extension clients.

        Returns the number of connected extension clients that were targeted.
        """
        from clipsync.site_profiles import build_site_profiles_message

        if not self._clients:
            return 0
        payload = json.dumps(build_site_profiles_message(profiles))
        await self._broadcast(payload)
        return len(self._clients)

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
        try:
            result = callback(data)
            if isinstance(result, Awaitable):
                await result
        except Exception:
            # Callback errors must not tear down the extension WebSocket.
            logger.exception("chrome_bridge callback failed for type=%s", data.get("type"))

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
