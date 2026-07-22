"""Relay transport stub — verifies slip_event HMAC before forwarding."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from clipsync.transport._auth import verify_slip_payload_sig
from clipsync.transport.usb import NotSupportedError

SlipEventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
MessageSource = Callable[[], Any]


class RelayTransport:
    """Thin relay transport; full legacy WS wiring happens in the orchestrator."""

    name = "relay"

    def __init__(
        self,
        shared_secret: str,
        *,
        message_source: Optional[MessageSource] = None,
    ) -> None:
        self._shared_secret = shared_secret
        self._message_source = message_source
        self._on_slip_event: Optional[SlipEventCallback] = None
        self._listen_task: Optional[asyncio.Task[None]] = None

    async def start(self, on_slip_event: SlipEventCallback) -> None:
        self._on_slip_event = on_slip_event
        if self._message_source is not None:
            self._listen_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        assert self._message_source is not None
        async for message in self._message_source():
            await self.handle_incoming(message)

    async def handle_incoming(self, message: dict[str, Any]) -> bool:
        """Process a relay WS message; return True when a slip_event was forwarded."""
        if message.get("type") != "slip_event":
            return False

        payload = message.get("payload")
        sig = message.get("sig", "")
        if not isinstance(payload, dict):
            return False
        if not verify_slip_payload_sig(self._shared_secret, payload, sig):
            return False
        if self._on_slip_event is None:
            return False

        result = self._on_slip_event(payload)
        if asyncio.iscoroutine(result):
            await result
        return True

    async def fetch_slips(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        raise NotSupportedError("fetch_slips is only available over USB transport")

    async def stop(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
