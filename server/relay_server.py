"""
relay_server.py - ClipSync Relay (Basic)
Install: pip install websockets
Run:     python3 relay_server.py
"""

import asyncio
import json
import logging
import os
from collections import defaultdict

import websockets
from websockets.server import WebSocketServerProtocol

PORT = int(os.getenv("PORT", "8765"))

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


async def handler(ws: WebSocketServerProtocol) -> None:
    peer_id = None  # PC id
    sub_id = None  # phone target id

    try:
        async for raw in ws:
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
                    phones[sub_id].discard(ws)

                phones[tid].add(ws)
                sub_id = tid
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
        if peer_id and pcs.get(peer_id) is ws:
            del pcs[peer_id]
            for ph in list(phones.get(peer_id, set())):
                await send(ph, {"type": "pc_offline"})
            log.info("OFF   %s", fmt(peer_id))

        if sub_id:
            phones[sub_id].discard(ws)


async def main() -> None:
    log.info("ClipSync Relay  port=%s", PORT)
    async with websockets.serve(
        handler,
        "0.0.0.0",
        PORT,
        ping_interval=25,
        ping_timeout=10,
        max_size=200 * 1024,
    ):
        log.info("Ready")
        await asyncio.Future()


asyncio.run(main())
