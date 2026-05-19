"""
relay_server.py - ClipSync Relay (Basic)
Install: pip install websockets
Run:     python3 relay_server.py
"""

import asyncio
import json
import logging
import os
import time
from collections import defaultdict

import websockets
from websockets.server import WebSocketServerProtocol

PORT = int(os.getenv("PORT", "8765"))
CONNECTION_TIMEOUT_SECONDS = int(os.getenv("CONNECTION_TIMEOUT_SECONDS", "1800"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))

logging.basicConfig(
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# id (9-digit str) -> PC WebSocket
pcs: dict[str, WebSocketServerProtocol] = {}

# id -> set of phone WebSockets waiting for that PC
phones: dict[str, set[WebSocketServerProtocol]] = defaultdict(set)

# WebSocket -> connection metadata
connections: dict[WebSocketServerProtocol, dict] = {}


def clean(raw: str) -> str | None:
    """Return a normalized 9-digit ID, or None when invalid."""
    d = raw.replace("-", "").strip()
    return d if (len(d) == 9 and d.isdigit()) else None


def fmt(d: str) -> str:
    return f"{d[:3]}-{d[3:6]}-{d[6:]}"


async def send(ws: WebSocketServerProtocol, data: dict) -> None:
    try:
        await ws.send(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def touch(ws: WebSocketServerProtocol) -> None:
    info = connections.setdefault(ws, {"role": "", "id": "", "last_seen": 0.0})
    info["last_seen"] = time.monotonic()


async def notify_phone_count(pid: str) -> None:
    pc = pcs.get(pid)
    if pc:
        await send(pc, {"type": "phone_count", "count": len(phones.get(pid, set()))})


async def unregister(ws: WebSocketServerProtocol, reason: str = "closed") -> None:
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
            try:
                await ws.close(code=4000, reason="heartbeat_timeout")
            except Exception:
                pass


async def handler(ws: WebSocketServerProtocol) -> None:
    peer_id = None  # PC id
    sub_id = None  # phone target id
    touch(ws)

    try:
        async for raw in ws:
            touch(ws)
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            action = msg.get("action", "")

            # PC registration
            if action == "register":
                pid = clean(msg.get("id", ""))
                if not pid:
                    await send(ws, {"error": "invalid_id"})
                    continue

                # Kick the old PC connection for this id, if any.
                old = pcs.get(pid)
                if old and old is not ws:
                    await send(old, {"type": "kicked"})
                    try:
                        await old.close()
                    except Exception:
                        pass

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
                        await ph.send(payload)
                    except Exception:
                        dead.add(ph)

                phones[peer_id] -= dead

                log.info(
                    'CLIP  %s -> %s phone(s)  "%s"',
                    fmt(peer_id),
                    len(phones.get(peer_id, set())),
                    text[:50],
                )

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:
        await unregister(ws)


async def process_request(path: str, request_headers):
    """Return a tiny health response for normal HTTP checks."""
    upgrade = request_headers.get("Upgrade", "").lower()
    if upgrade == "websocket":
        return None

    if path not in {"/", "/health"}:
        body = b"Not Found\n"
        return (
            404,
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
            body,
        )

    body = b"OK\n" if path == "/health" else b"ClipSync Relay OK\n"
    return (
        200,
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
        body,
    )


async def main() -> None:
    log.info("ClipSync Relay  port=%s", PORT)
    async with websockets.serve(
        handler,
        "0.0.0.0",
        PORT,
        process_request=process_request,
        ping_interval=55,
        ping_timeout=20,
        max_size=200 * 1024,
    ):
        asyncio.create_task(cleanup_stale_connections())
        log.info("Ready")
        await asyncio.Future()


asyncio.run(main())
