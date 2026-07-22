"""
relay_server.py - ClipSync Relay
Install: pip install -r requirements.txt
Run:     python3 relay_server.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

PORT = int(os.getenv("PORT", "8765"))
CONNECTION_TIMEOUT_SECONDS = int(os.getenv("CONNECTION_TIMEOUT_SECONDS", "1800"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))
MAX_MESSAGE_BYTES = 200 * 1024

_SERVER_DIR = Path(__file__).resolve().parent
REVOKED_DEVICES_PATH = Path(
    os.getenv("REVOKED_DEVICES_PATH", str(_SERVER_DIR / "revoked_devices.json"))
)
LICENSE_MIN_REQUIRED_VERSION = os.getenv("LICENSE_MIN_REQUIRED_VERSION", "0.0.0")
LICENSE_UPDATE_URL = os.getenv(
    "LICENSE_UPDATE_URL",
    "https://github.com/Florentino-coder/clipsync/releases/latest",
)
LICENSE_FORCE_UPDATE = os.getenv("LICENSE_FORCE_UPDATE", "false").lower() in (
    "1",
    "true",
    "yes",
)
LICENSE_STATUS_DEFAULT = os.getenv("LICENSE_STATUS_DEFAULT", "ok")

logging.basicConfig(
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

Ws = web.WebSocketResponse

# id (9-digit str) -> PC WebSocket
pcs: dict[str, Ws] = {}

# id -> set of phone WebSockets waiting for that PC
phones: dict[str, set[Ws]] = defaultdict(set)

# WebSocket -> connection metadata
connections: dict[Ws, dict[str, Any]] = {}


def clean(raw: str) -> str | None:
    """Return a normalized 9-digit ID, or None when invalid."""
    d = raw.replace("-", "").strip()
    return d if (len(d) == 9 and d.isdigit()) else None


def fmt(d: str) -> str:
    return f"{d[:3]}-{d[3:6]}-{d[6:]}"


async def send(ws: Ws, data: dict[str, Any]) -> None:
    try:
        if not ws.closed:
            await ws.send_str(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def touch(ws: Ws) -> None:
    info = connections.setdefault(ws, {"role": "", "id": "", "last_seen": 0.0})
    info["last_seen"] = time.monotonic()


async def notify_phone_count(pid: str) -> None:
    pc = pcs.get(pid)
    if pc:
        await send(pc, {"type": "phone_count", "count": len(phones.get(pid, set()))})


async def unregister(ws: Ws, reason: str = "closed") -> None:
    info = connections.pop(ws, {})
    role = info.get("role", "")
    pid = info.get("id", "")
    tid = info.get("target", "")

    if role == "pc" and pid and pcs.get(pid) is ws:
        del pcs[pid]
        for ph in list(phones.get(pid, set())):
            await send(ph, {"type": "pc_offline"})
        log.info("OFF   %s  reason=%s", fmt(pid), reason)

    if role == "phone" and tid:
        before = len(phones.get(tid, set()))
        phones[tid].discard(ws)
        after = len(phones.get(tid, set()))
        if before != after:
            await notify_phone_count(tid)
            log.info("UNSUB phone -> %s  count=%s reason=%s", fmt(tid), after, reason)


async def cleanup_stale_connections() -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        now = time.monotonic()
        stale = [
            ws
            for ws, info in list(connections.items())
            if now - float(info.get("last_seen", 0.0) or 0.0)
            > CONNECTION_TIMEOUT_SECONDS
        ]
        for ws in stale:
            await unregister(ws, "heartbeat_timeout")
            with contextlib.suppress(Exception):
                await ws.close(code=4000, message=b"heartbeat_timeout")


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=55, max_msg_size=MAX_MESSAGE_BYTES)
    await ws.prepare(request)
    touch(ws)

    peer_id = None  # PC id
    sub_id = None  # phone target id

    try:
        async for item in ws:
            if item.type != WSMsgType.TEXT:
                continue

            touch(ws)
            try:
                msg = json.loads(item.data)
            except Exception:
                continue

            action = msg.get("action", "")

            # PC registration
            if action == "register":
                pid = clean(msg.get("id", ""))
                if not pid:
                    await send(ws, {"error": "invalid_id"})
                    continue

                old = pcs.get(pid)
                if old and old is not ws:
                    await send(old, {"type": "kicked"})
                    with contextlib.suppress(Exception):
                        await old.close()

                pcs[pid] = ws
                peer_id = pid
                connections[ws].update({"role": "pc", "id": pid, "target": ""})

                await send(
                    ws,
                    {
                        "status": "registered",
                        "id": fmt(pid),
                        "phones": len(phones.get(pid, set())),
                    },
                )

                for ph in list(phones.get(pid, set())):
                    await send(ph, {"type": "pc_online"})

                log.info("REG   %s", fmt(pid))

            # Phone subscription
            elif action == "subscribe":
                tid = clean(msg.get("target", ""))
                if not tid:
                    await send(ws, {"error": "invalid_target"})
                    continue

                if sub_id and sub_id != tid:
                    old_count = len(phones.get(sub_id, set()))
                    phones[sub_id].discard(ws)
                    if len(phones.get(sub_id, set())) != old_count:
                        await notify_phone_count(sub_id)

                phones[tid].add(ws)
                sub_id = tid
                connections[ws].update({"role": "phone", "id": "", "target": tid})
                online = tid in pcs

                await send(
                    ws,
                    {
                        "status": "subscribed",
                        "target": fmt(tid),
                        "online": online,
                    },
                )

                if online:
                    await send(
                        pcs[tid],
                        {
                            "type": "phone_joined",
                            "count": len(phones[tid]),
                        },
                    )

                log.info("SUB   phone -> %s  online=%s", fmt(tid), online)

            # Lightweight keepalive from PC or phone.
            elif action == "heartbeat":
                await send(ws, {"type": "heartbeat_ack"})

            # Phone → PC slip event (HMAC opaque to relay; forward only).
            elif action == "slip_event":
                if not sub_id:
                    continue

                pc = pcs.get(sub_id)
                if not pc:
                    continue

                await send(
                    pc,
                    {
                        "type": "slip_event",
                        "payload": msg.get("payload"),
                        "sig": msg.get("sig", ""),
                    },
                )

                log.info("SLIP  phone -> %s", fmt(sub_id))

            # PC → phone slip ack (after PC processed the event).
            elif action == "slip_ack":
                if not peer_id:
                    continue
                event_id = msg.get("event_id")
                if not event_id or not isinstance(event_id, str):
                    continue

                payload = json.dumps(
                    {"type": "slip_ack", "event_id": event_id},
                    ensure_ascii=False,
                )
                dead: set[Ws] = set()
                for ph in list(phones.get(peer_id, set())):
                    try:
                        await ph.send_str(payload)
                    except Exception:
                        dead.add(ph)
                phones[peer_id] -= dead
                log.info("ACK   %s -> phone(s)", fmt(peer_id))

            # PC clipboard update
            elif action == "clip":
                if not peer_id:
                    continue

                text = msg.get("text", "")
                if not text or not isinstance(text, str):
                    continue

                if len(text.encode("utf-8")) > 100 * 1024:
                    await send(ws, {"error": "too_large"})
                    continue

                payload = json.dumps(
                    {"type": "clip", "text": text},
                    ensure_ascii=False,
                )

                dead = set()
                for ph in list(phones.get(peer_id, set())):
                    try:
                        await ph.send_str(payload)
                    except Exception:
                        dead.add(ph)

                phones[peer_id] -= dead

                log.info(
                    'CLIP  %s -> %s phone(s)  "%s"',
                    fmt(peer_id),
                    len(phones.get(peer_id, set())),
                    text[:50],
                )

    finally:
        await unregister(ws)

    return ws


async def root_handler(request: web.Request) -> web.StreamResponse:
    upgrade = request.headers.get("Upgrade", "").lower()
    if upgrade == "websocket":
        return await websocket_handler(request)

    return web.Response(
        text="ClipSync Relay OK\n",
        content_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )


async def health_handler(request: web.Request) -> web.Response:
    return web.Response(
        text="OK\n",
        content_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )


def parse_version(value: str) -> tuple[int, int, int, int]:
    base, _, build = str(value or "").partition("+")
    parts: list[int] = []
    for raw in base.split("."):
        digits = "".join(ch for ch in raw if ch.isdigit())
        parts.append(int(digits or "0"))
    while len(parts) < 3:
        parts.append(0)
    build_digits = "".join(ch for ch in build if ch.isdigit())
    return parts[0], parts[1], parts[2], int(build_digits or "0")


def version_less(left: str, right: str) -> bool:
    return parse_version(left) < parse_version(right)


def load_revoked_devices(path: Path | None = None) -> set[str]:
    target = path or REVOKED_DEVICES_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()

    if isinstance(raw, list):
        return {str(item).strip() for item in raw if str(item).strip()}
    if isinstance(raw, dict):
        devices = raw.get("devices", [])
        if isinstance(devices, list):
            return {str(item).strip() for item in devices if str(item).strip()}
    return set()


async def license_check_handler(request: web.Request) -> web.Response:
    device_id = str(request.query.get("device_id", "") or "").strip()
    version = str(request.query.get("version", "") or "").strip()

    revoked = bool(device_id) and device_id in load_revoked_devices()
    force_update = False
    if LICENSE_FORCE_UPDATE and version:
        force_update = version_less(version, LICENSE_MIN_REQUIRED_VERSION)

    payload = {
        "min_required_version": LICENSE_MIN_REQUIRED_VERSION,
        "force_update": force_update,
        "update_url": LICENSE_UPDATE_URL,
        "license_status": "revoked" if revoked else LICENSE_STATUS_DEFAULT,
        "revoked": revoked,
    }
    return web.json_response(payload, headers={"Cache-Control": "no-store"})


async def start_background_tasks(app: web.Application) -> None:
    app["cleanup_task"] = asyncio.create_task(cleanup_stale_connections())


async def cleanup_background_tasks(app: web.Application) -> None:
    task = app.get("cleanup_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", root_handler, allow_head=True)
    app.router.add_get("/health", health_handler, allow_head=True)
    app.router.add_get("/license/check", license_check_handler, allow_head=True)
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    return app


if __name__ == "__main__":
    log.info("ClipSync Relay  port=%s", PORT)
    web.run_app(create_app(), host="0.0.0.0", port=PORT, access_log=None)
