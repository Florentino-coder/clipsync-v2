"""Transport manager — USB-first with automatic relay fallback."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, Protocol

from clipsync.transport.relay import RelayTransport
from clipsync.transport.usb import UsbTransport, _probe_phone, find_usb_tether_phone_ip

logger = logging.getLogger(__name__)

SlipEventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
TransportChangedCallback = Callable[[Optional[str], str], None]
FindUsbIp = Callable[[], str | None]
ProbePhone = Callable[[str], bool]
SleepFn = Callable[[float], Awaitable[None]]


class Transport(Protocol):
    name: str

    async def start(self, on_slip_event: SlipEventCallback) -> None: ...
    async def fetch_slips(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]: ...
    async def stop(self) -> None: ...


class TransportManager:
    """Selects USB when tethered, otherwise relay; re-evaluates every poll interval."""

    def __init__(
        self,
        shared_secret: str,
        *,
        mode: str = "auto",
        poll_interval: float = 5.0,
        usb_fail_threshold: int = 3,
        find_usb_ip: FindUsbIp = find_usb_tether_phone_ip,
        probe_phone: ProbePhone = _probe_phone,
        sleep: Optional[SleepFn] = None,
        on_transport_changed: Optional[TransportChangedCallback] = None,
        usb_transport_factory: Optional[Callable[[str], Transport]] = None,
        relay_transport_factory: Optional[Callable[[], Transport]] = None,
    ) -> None:
        self._shared_secret = shared_secret
        self._mode = mode
        self._poll_interval = poll_interval
        self._usb_fail_threshold = usb_fail_threshold
        self._find_usb_ip = find_usb_ip
        self._probe_phone = probe_phone
        self._sleep = sleep or asyncio.sleep
        self._on_transport_changed = on_transport_changed
        self._usb_factory = usb_transport_factory or (
            lambda ip: UsbTransport(ip, shared_secret, probe_phone=probe_phone)
        )
        self._relay_factory = relay_transport_factory or (
            lambda: RelayTransport(shared_secret)
        )

        self._transport: Optional[Transport] = None
        self._current_phone_ip: Optional[str] = None
        self._on_slip_event: Optional[SlipEventCallback] = None
        self._poll_task: Optional[asyncio.Task[None]] = None
        self._usb_fail_count = 0

    @property
    def transport(self) -> Optional[Transport]:
        return self._transport

    @property
    def transport_name(self) -> Optional[str]:
        return self._transport.name if self._transport else None

    async def start(self, on_slip_event: SlipEventCallback) -> None:
        self._on_slip_event = on_slip_event
        await self._activate_initial_transport()
        if self._mode == "auto":
            self._poll_task = asyncio.create_task(self._poll_loop(), name="transport-poll")

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._transport is not None:
            await self._transport.stop()
            self._transport = None
            self._current_phone_ip = None

    async def fetch_slips(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        if self._transport is None:
            raise RuntimeError("TransportManager is not started")
        return await self._transport.fetch_slips(date_from, date_to)

    async def _activate_initial_transport(self) -> None:
        if self._mode == "relay":
            await self._switch_to("relay")
            return

        if self._mode == "usb":
            phone_ip = self._find_usb_ip()
            if not phone_ip:
                raise RuntimeError("USB mode requested but no tethered phone was found")
            await self._switch_to("usb", phone_ip)
            return

        phone_ip = self._find_usb_ip()
        if phone_ip:
            await self._switch_to("usb", phone_ip)
        else:
            await self._switch_to("relay")

    async def _switch_to(self, name: str, phone_ip: Optional[str] = None) -> None:
        old_name = self._transport.name if self._transport else None
        if (
            old_name == name
            and (name != "usb" or self._current_phone_ip == phone_ip)
        ):
            return

        if self._transport is not None:
            await self._transport.stop()

        if name == "usb":
            if not phone_ip:
                raise ValueError("phone_ip is required for USB transport")
            self._transport = self._usb_factory(phone_ip)
            self._current_phone_ip = phone_ip
        else:
            self._transport = self._relay_factory()
            self._current_phone_ip = None

        assert self._on_slip_event is not None
        await self._transport.start(self._on_slip_event)
        self._usb_fail_count = 0

        if old_name != name and self._on_transport_changed is not None:
            self._on_transport_changed(old_name, name)
            logger.info("transport changed %s -> %s", old_name or "none", name)

    async def _poll_loop(self) -> None:
        while True:
            await self._sleep(self._poll_interval)
            await self._evaluate_transport()

    async def _evaluate_transport(self) -> None:
        current = self._transport.name if self._transport else None

        if current == "usb":
            reachable = bool(
                self._current_phone_ip and self._probe_phone(self._current_phone_ip)
            )
            if reachable:
                self._usb_fail_count = 0
                return

            self._usb_fail_count += 1
            if self._usb_fail_count >= self._usb_fail_threshold:
                await self._switch_to("relay")
            return

        phone_ip = self._find_usb_ip()
        if phone_ip:
            await self._switch_to("usb", phone_ip)
