"""Tests for USB detection and TransportManager fallback logic."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from clipsync.transport._auth import auth_token, slip_payload_sig
from clipsync.transport.manager import TransportManager
from clipsync.transport.relay import RelayTransport
from clipsync.transport.usb import NotSupportedError, UsbTransport, find_usb_tether_phone_ip

SECRET = "abcdef0123456789abcdef0123456789"


async def blocked_sleep(_seconds: float) -> None:
    """Park the poll loop until TransportManager.stop() cancels it."""
    await asyncio.Event().wait()


class MockTransport:
    instances: list["MockTransport"] = []

    def __init__(self, name: str, *, phone_ip: str | None = None) -> None:
        self.name = name
        self.phone_ip = phone_ip
        self.started = False
        self.stopped = False
        self.on_slip_event = None
        MockTransport.instances.append(self)

    async def start(self, on_slip_event):
        self.started = True
        self.on_slip_event = on_slip_event

    async def fetch_slips(self, date_from, date_to):
        if self.name == "relay":
            raise NotSupportedError("fetch_slips is only available over USB transport")
        return [{"event_id": "evt-1"}]

    async def stop(self):
        self.stopped = True


def test_finds_gateway_of_tether_nic():
    with patch(
        "clipsync.transport.usb._list_candidates",
        return_value=[("Remote NDIS", "192.168.187.100", "255.255.255.0")],
    ):
        with patch(
            "clipsync.transport.usb._probe_phone",
            side_effect=lambda ip: ip == "192.168.187.1",
        ):
            assert find_usb_tether_phone_ip() == "192.168.187.1"


def test_returns_none_without_tether():
    with patch("clipsync.transport.usb._list_candidates", return_value=[]):
        assert find_usb_tether_phone_ip() is None


def test_tries_dot_129_when_dot_1_fails():
    with patch(
        "clipsync.transport.usb._list_candidates",
        return_value=[("Remote NDIS", "192.168.42.100", "255.255.255.0")],
    ):
        with patch(
            "clipsync.transport.usb._probe_phone",
            side_effect=lambda ip: ip == "192.168.42.129",
        ) as probe:
            assert find_usb_tether_phone_ip() == "192.168.42.129"
            assert [c.args[0] for c in probe.call_args_list] == [
                "192.168.42.1",
                "192.168.42.129",
            ]


def test_auth_token_matches_mobile_scheme():
    import hashlib
    import hmac

    expected = hmac.new(
        SECRET.encode("utf-8"),
        b"clipsync-slip",
        hashlib.sha256,
    ).hexdigest()
    assert auth_token(SECRET) == expected


@pytest.mark.asyncio
async def test_auto_mode_selects_usb_when_available():
    MockTransport.instances.clear()

    mgr = TransportManager(
        SECRET,
        find_usb_ip=lambda: "192.168.187.1",
        sleep=blocked_sleep,
        usb_transport_factory=lambda ip: MockTransport("usb", phone_ip=ip),
        relay_transport_factory=lambda: MockTransport("relay"),
    )
    await mgr.start(lambda _event: None)

    assert mgr.transport_name == "usb"
    assert MockTransport.instances[0].name == "usb"
    assert MockTransport.instances[0].started is True

    await mgr.stop()


@pytest.mark.asyncio
async def test_auto_mode_selects_relay_without_usb():
    MockTransport.instances.clear()

    mgr = TransportManager(
        SECRET,
        find_usb_ip=lambda: None,
        sleep=blocked_sleep,
        usb_transport_factory=lambda ip: MockTransport("usb", phone_ip=ip),
        relay_transport_factory=lambda: MockTransport("relay"),
    )
    await mgr.start(lambda _event: None)

    assert mgr.transport_name == "relay"
    assert MockTransport.instances[0].name == "relay"

    await mgr.stop()


@pytest.mark.asyncio
async def test_on_transport_changed_when_usb_lost():
    MockTransport.instances.clear()
    usb_up = {"value": True}
    changes: list[tuple[str | None, str]] = []

    mgr = TransportManager(
        SECRET,
        poll_interval=0.01,
        find_usb_ip=lambda: "192.168.187.1" if usb_up["value"] else None,
        probe_phone=lambda _ip: usb_up["value"],
        sleep=blocked_sleep,
        on_transport_changed=lambda old, new: changes.append((old, new)),
        usb_transport_factory=lambda ip: MockTransport("usb", phone_ip=ip),
        relay_transport_factory=lambda: MockTransport("relay"),
    )
    await mgr.start(lambda _event: None)
    assert mgr.transport_name == "usb"

    usb_up["value"] = False
    for _ in range(3):
        await mgr._evaluate_transport()

    assert mgr.transport_name == "relay"
    assert ("usb", "relay") in changes

    await mgr.stop()


@pytest.mark.asyncio
async def test_usb_returns_switches_back_from_relay():
    MockTransport.instances.clear()
    usb_available = {"value": False}
    changes: list[tuple[str | None, str]] = []

    mgr = TransportManager(
        SECRET,
        poll_interval=0.01,
        find_usb_ip=lambda: "192.168.187.1" if usb_available["value"] else None,
        probe_phone=lambda _ip: True,
        sleep=blocked_sleep,
        on_transport_changed=lambda old, new: changes.append((old, new)),
        usb_transport_factory=lambda ip: MockTransport("usb", phone_ip=ip),
        relay_transport_factory=lambda: MockTransport("relay"),
    )
    await mgr.start(lambda _event: None)
    assert mgr.transport_name == "relay"

    usb_available["value"] = True
    await mgr._evaluate_transport()

    assert mgr.transport_name == "usb"
    assert ("relay", "usb") in changes

    await mgr.stop()


@pytest.mark.asyncio
async def test_relay_fetch_slips_not_supported():
    relay = RelayTransport(SECRET)
    with pytest.raises(NotSupportedError):
        await relay.fetch_slips(datetime.now(timezone.utc), datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_relay_forwards_valid_slip_event():
    relay = RelayTransport(SECRET)
    received: list[dict] = []

    await relay.start(received.append)
    payload = {"event_id": "evt-42", "amount": 100.0}
    sig = slip_payload_sig(SECRET, payload)
    forwarded = await relay.handle_incoming(
        {"type": "slip_event", "payload": payload, "sig": sig}
    )

    assert forwarded is True
    assert received == [payload]


@pytest.mark.asyncio
async def test_relay_rejects_bad_sig():
    relay = RelayTransport(SECRET)
    received: list[dict] = []

    await relay.start(received.append)
    payload = {"event_id": "evt-42", "amount": 100.0}
    forwarded = await relay.handle_incoming(
        {"type": "slip_event", "payload": payload, "sig": "bad-signature"}
    )

    assert forwarded is False
    assert received == []


def test_usb_transport_is_reachable_uses_probe():
    transport = UsbTransport(
        "192.168.187.1",
        SECRET,
        probe_phone=lambda ip: ip == "192.168.187.1",
    )
    assert transport.is_reachable() is True
    assert UsbTransport("192.168.187.2", SECRET, probe_phone=lambda _ip: False).is_reachable() is False
