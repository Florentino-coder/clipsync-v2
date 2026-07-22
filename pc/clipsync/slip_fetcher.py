"""Fetch slip images from phone — USB only (never via relay)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class SlipTransport(Protocol):
    """Minimal transport surface used by SlipFetcher (avoids hard coupling)."""

    name: str

    async def fetch_slips(
        self, date_from: datetime, date_to: datetime
    ) -> list[dict[str, Any]]: ...


class UsbRequiredError(Exception):
    """Raised when slip image fetch is attempted over relay."""

    code = "usb_required"

    def __init__(self, message: str = "usb_required") -> None:
        super().__init__(message)
        self.code = "usb_required"


class SlipFetcher:
    """Wraps transport.fetch_slips; rejects relay because images must stay local."""

    def __init__(self, transport: SlipTransport) -> None:
        self._transport = transport

    async def fetch_slips(
        self, date_from: datetime, date_to: datetime
    ) -> list[dict[str, Any]]:
        if getattr(self._transport, "name", None) == "relay":
            raise UsbRequiredError("usb_required")
        return await self._transport.fetch_slips(date_from, date_to)
