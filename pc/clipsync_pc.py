"""
clipsync_pc.py - ClipSync PC Client (Basic)
Install: pip install websockets pyperclip
         Linux extra: sudo apt install xclip
Run:     python clipsync_pc.py
Edit:    RELAY_HOST = "your VPS IP"
"""

import asyncio
import argparse
import json
import os
import random
import sys
import threading
import time

import pyperclip
import websockets

DEFAULT_RELAY_HOST = "YOUR_VPS_IP"
RELAY_PORT = 8765
BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)
ID_FILE = os.path.join(BASE_DIR, "clipsync.id")
CONFIG_FILE = os.path.join(BASE_DIR, "clipsync_pc_config.json")
CONFIG_NAME = os.path.basename(CONFIG_FILE)

RELAY_HOST = DEFAULT_RELAY_HOST
RELAY_URL = ""

_ws = None
_loop = None


def load_or_create_id() -> str:
    """Load the PC ID from disk, or create one on first run."""
    if os.path.exists(ID_FILE):
        with open(ID_FILE, encoding="utf-8") as f:
            d = f.read().strip().replace("-", "")
            if d.isdigit() and len(d) == 9:
                return d

    new_id = "".join(str(random.randint(0, 9)) for _ in range(9))
    with open(ID_FILE, "w", encoding="utf-8") as f:
        f.write(new_id)
    return new_id


def fmt(d: str) -> str:
    return f"{d[:3]}-{d[3:6]}-{d[6:]}"


MY_ID = load_or_create_id()


def load_config_host() -> str:
    if not os.path.exists(CONFIG_FILE):
        return ""

    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        host = str(data.get("relay_host", "")).strip()
        return host
    except Exception:
        return ""


def load_config_url() -> str:
    if not os.path.exists(CONFIG_FILE):
        return ""

    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        url = str(data.get("relay_url", "")).strip()
        return url
    except Exception:
        return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ClipSync PC Client")
    parser.add_argument(
        "--relay-host",
        help="Relay server IP or hostname. Overrides env/config/default.",
    )
    parser.add_argument(
        "--relay-url",
        help="Full WebSocket URL, e.g. wss://clipsync-relay.onrender.com.",
    )
    parser.add_argument(
        "--relay-port",
        type=int,
        default=RELAY_PORT,
        help=f"Relay server port. Default: {RELAY_PORT}",
    )
    return parser.parse_args()


def configure_relay() -> None:
    global RELAY_HOST, RELAY_PORT, RELAY_URL

    args = parse_args()
    configured_url = (
        args.relay_url
        or os.getenv("CLIPSYNC_RELAY_URL", "")
        or load_config_url()
    ).strip()

    if configured_url:
        RELAY_URL = configured_url
        RELAY_HOST = configured_url
        return

    RELAY_HOST = (
        args.relay_host
        or os.getenv("CLIPSYNC_RELAY_HOST", "")
        or load_config_host()
        or DEFAULT_RELAY_HOST
    ).strip()
    RELAY_PORT = args.relay_port
    RELAY_URL = f"ws://{RELAY_HOST}:{RELAY_PORT}"


async def send_clip(text: str) -> None:
    if _ws:
        try:
            await _ws.send(
                json.dumps({"action": "clip", "text": text}, ensure_ascii=False)
            )
        except Exception:
            pass


def watch_clipboard() -> None:
    """Thread: poll clipboard every 300 ms."""
    last = ""
    while True:
        try:
            now = pyperclip.paste()
            if now and now != last:
                last = now
                preview = now.replace("\n", " ")[:60]
                print(f"  -> {preview}{'...' if len(now) > 60 else ''}")
                if _loop:
                    asyncio.run_coroutine_threadsafe(send_clip(now), _loop)
        except Exception:
            pass
        time.sleep(0.3)


async def ws_loop() -> None:
    global _ws

    while True:
        try:
            async with websockets.connect(
                RELAY_URL,
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                _ws = ws
                await ws.send(json.dumps({"action": "register", "id": MY_ID}))
                print("  OK connected")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    t = msg.get("type") or msg.get("status")

                    if t == "registered":
                        n = msg.get("phones", 0)
                        print(f"  ID : {fmt(MY_ID)}")
                        print(f"  Phones connected: {n}")
                        print("  Waiting for copy...\n")

                    elif t == "phone_joined":
                        print(
                            f"  [+] Phone connected. Total {msg.get('count', '')}"
                        )

                    elif t == "kicked":
                        print("  [!] This ID is being used elsewhere")

        except Exception as e:
            print(f"  Disconnected: {e}")

        _ws = None
        print("  Reconnecting in 3 seconds...")
        await asyncio.sleep(3)


async def main() -> None:
    global _loop
    configure_relay()
    _loop = asyncio.get_running_loop()

    print("=" * 38)
    print("  ClipSync PC")
    print("=" * 38)
    print(f"  Relay : {RELAY_HOST}:{RELAY_PORT}")

    if RELAY_HOST == DEFAULT_RELAY_HOST:
        print("\n  RELAY_HOST is not configured yet.")
        print("  Run: ClipSyncPC.exe --relay-url wss://YOUR_SERVER_URL")
        print("  Run: ClipSyncPC.exe --relay-host YOUR_SERVER_IP")
        print(
            f"  Or create {CONFIG_NAME} next to the exe: "
            '{"relay_url":"wss://YOUR_SERVER_URL"}'
        )
        return

    threading.Thread(target=watch_clipboard, daemon=True).start()
    await ws_loop()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\n  Stopped")
