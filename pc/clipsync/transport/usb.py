"""USB tether detection and transport."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional
from urllib.request import urlopen

import aiohttp
import psutil
import websockets

from clipsync.transport._auth import auth_token

logger = logging.getLogger(__name__)

TETHER_NIC_HINTS = ("ndis", "remote ndis", "usb", "ethernet")
DEFAULT_PORT = 8790

SlipEventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


def _list_candidates() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if not stats.get(name) or not stats[name].isup:
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("169.254"):
                if any(h in name.lower() for h in TETHER_NIC_HINTS) or addr.address.startswith(
                    "192.168.42."
                ):
                    out.append((name, addr.address, addr.netmask or "255.255.255.0"))
    return out


def _probe_phone(ip: str, port: int = DEFAULT_PORT, timeout: float = 1.5) -> bool:
    try:
        with urlopen(f"http://{ip}:{port}/ping", timeout=timeout) as response:
            return b'"app":"clipsync"' in response.read().replace(b" ", b"")
    except Exception:
        return False


def find_usb_tether_phone_ip() -> str | None:
    for _name, addr, mask in _list_candidates():
        net = ipaddress.ip_network(f"{addr}/{mask}", strict=False)
        for cand in (str(net.network_address + 1), str(net.network_address + 129)):
            if cand != addr and _probe_phone(cand):
                return cand
    return None


class NotSupportedError(Exception):
    """Raised when an operation is unavailable on the active transport."""


class UsbTransport:
    name = "usb"

    def __init__(
        self,
        phone_ip: str,
        shared_secret: str,
        *,
        port: int = DEFAULT_PORT,
        probe_phone: Callable[[str], bool] = _probe_phone,
    ) -> None:
        self._phone_ip = phone_ip
        self._shared_secret = shared_secret
        self._port = port
        self._probe_phone = probe_phone
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._listen_task: Optional[Any] = None
        self._on_slip_event: Optional[SlipEventCallback] = None

    @property
    def phone_ip(self) -> str:
        return self._phone_ip

    def is_reachable(self) -> bool:
        return self._probe_phone(self._phone_ip)

    async def start(self, on_slip_event: SlipEventCallback) -> None:
        self._on_slip_event = on_slip_event
        uri = f"ws://{self._phone_ip}:{self._port}/"
        self._ws = await websockets.connect(uri)
        await self._ws.send(
            json.dumps({"type": "auth", "token": auth_token(self._shared_secret)})
        )
        raw = await self._ws.recv()
        decoded = json.loads(raw)
        if decoded.get("type") != "auth_ok":
            await self._ws.close()
            self._ws = None
            raise RuntimeError("USB WebSocket auth failed")

        self._listen_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if data.get("type") != "slip_event":
                    continue
                payload = data.get("payload")
                if not isinstance(payload, dict) or self._on_slip_event is None:
                    continue
                result = self._on_slip_event(payload)
                if asyncio.iscoroutine(result):
                    await result
        except websockets.ConnectionClosed:
            logger.debug("USB WebSocket closed for %s", self._phone_ip)

    async def send_ack(self, event_id: str) -> None:
        """Send ``{"type":"slip_ack","event_id":...}`` to the phone over USB WS."""
        if not event_id or self._ws is None:
            return
        await self._ws.send(json.dumps({"type": "slip_ack", "event_id": event_id}))

    async def fetch_slips(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        url = f"http://{self._phone_ip}:{self._port}/slips"
        params = {"from": date_from.isoformat(), "to": date_to.isoformat()}
        headers = {"X-Auth": auth_token(self._shared_secret)}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                if isinstance(data, list):
                    return data
                return []

    async def stop(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
