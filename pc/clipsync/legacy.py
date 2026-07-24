"""
ClipSync PC

Install: pip install -r requirements.txt
Run:     python clipsync_pc.py
Build:   .\build_exe.ps1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import random
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import ctypes
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

import pyperclip
import qrcode
import websockets

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception:  # pragma: no cover - used only when Tk is unavailable.
    tk = None
    ttk = None
    messagebox = None

from clipsync.audit import append_audit
from clipsync.bootstrap import start_slip_bootstrap
from clipsync.ui.audit_viewer import AuditViewer
from clipsync.ui.debug_panel import DebugPanel
from clipsync.ui.settings_panel import SettingsPanel

APP_NAME = "ClipSync PC"
APP_VERSION = "0.9.0"
AUTHOR_NAME = "Florentino356"
DEFAULT_RELAY_URL = "wss://clipsync-relay.onrender.com"
UPDATE_MANIFEST_URL = (
    "https://github.com/Florentino-coder/clipsync/releases/download/"
    "android-latest/version.json"
)
CONFIG_NAME = "clipsync_pc_config.json"
POLL_INTERVAL_SECONDS = 0.05
HEARTBEAT_INTERVAL_SECONDS = 10 * 60
MAX_CLIP_BYTES = 100 * 1024
RECONNECT_STEPS_SECONDS = (2, 5, 10, 30, 60)
UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
MAX_UPDATE_MANIFEST_BYTES = 64 * 1024


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", app_base_dir()))
    return root / relative


def user_data_dir() -> Path:
    if sys.platform.startswith("win"):
        root = os.getenv("APPDATA") or str(Path.home())
        return Path(root) / "ClipSync"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ClipSync"
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "clipsync"


BASE_DIR = app_base_dir()
CONFIG_FILE = BASE_DIR / CONFIG_NAME
ID_FILE = user_data_dir() / "clipsync.id"
SECRET_FILE = user_data_dir() / "clipsync.secret"
UPDATE_STATE_FILE = user_data_dir() / "update_state.json"


def clean_id(raw: str) -> str | None:
    value = raw.replace("-", "").strip()
    return value if len(value) == 9 and value.isdigit() else None


def fmt_id(value: str) -> str:
    digits = value.replace("-", "")
    if len(digits) != 9:
        return value
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def load_or_create_shared_secret() -> str:
    if SECRET_FILE.is_file():
        secret = SECRET_FILE.read_text(encoding="utf-8").strip()
        if len(secret) == 32 and all(ch in "0123456789abcdef" for ch in secret):
            return secret
    secret = secrets.token_hex(16)
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(secret, encoding="utf-8")
    return secret


def pairing_url(pc_id: str) -> str:
    clean = pc_id.replace("-", "")
    secret = load_or_create_shared_secret()
    return f"clipsync://pair?id={clean}&secret={secret}"


def next_reconnect_delay(step: int) -> int:
    return RECONNECT_STEPS_SECONDS[min(step, len(RECONNECT_STEPS_SECONDS) - 1)]


def parse_version(value: str) -> tuple[int, int, int, int]:
    base, _, build = value.partition("+")
    parts = []
    for raw in base.split("."):
        digits = "".join(ch for ch in raw if ch.isdigit())
        parts.append(int(digits or "0"))
    while len(parts) < 3:
        parts.append(0)
    build_digits = "".join(ch for ch in build if ch.isdigit())
    return parts[0], parts[1], parts[2], int(build_digits or "0")


def is_newer_version(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def generate_id() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(9))


def load_or_create_id() -> str:
    ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ID_FILE.exists():
        existing = clean_id(ID_FILE.read_text(encoding="utf-8"))
        if existing:
            return existing

    new_id = generate_id()
    ID_FILE.write_text(new_id, encoding="utf-8")
    return new_id


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_update_state() -> dict[str, Any]:
    if not UPDATE_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(UPDATE_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_update_state(data: dict[str, Any]) -> None:
    try:
        UPDATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def should_check_update(force: bool = False) -> bool:
    if force:
        return True
    last_checked = float(load_update_state().get("last_checked", 0) or 0)
    return time.time() - last_checked >= UPDATE_CHECK_INTERVAL_SECONDS


def fetch_update_manifest() -> dict[str, Any]:
    request = urllib.request.Request(
        UPDATE_MANIFEST_URL,
        headers={"User-Agent": f"ClipSyncPC/{APP_VERSION}"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        length = int(response.headers.get("content-length", "0") or "0")
        if length > MAX_UPDATE_MANIFEST_BYTES:
            raise ValueError("update manifest is too large")
        body = response.read(MAX_UPDATE_MANIFEST_BYTES + 1)
        if len(body) > MAX_UPDATE_MANIFEST_BYTES:
            raise ValueError("update manifest is too large")
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("update manifest is invalid")
    save_update_state({"last_checked": time.time()})
    return data


def pc_update_from_manifest(manifest: dict[str, Any]) -> dict[str, Any] | None:
    pc = manifest.get("pc")
    if not isinstance(pc, dict):
        return None
    latest = str(pc.get("version", "")).strip()
    if not latest or not is_newer_version(latest, APP_VERSION):
        return None
    url = (
        str(pc.get("installer_url", "")).strip()
        or str(pc.get("url", "")).strip()
        or str(pc.get("portable_url", "")).strip()
    )
    if not url:
        return None
    return {
        "version": latest,
        "url": url,
        "notes": str(pc.get("notes", "")).strip(),
    }


def resolve_relay_url(args: argparse.Namespace) -> str:
    config = load_config()
    configured_url = (
        args.relay_url
        or os.getenv("CLIPSYNC_RELAY_URL", "")
        or str(config.get("relay_url", ""))
        or DEFAULT_RELAY_URL
    ).strip()
    if configured_url:
        return configured_url

    host = (
        args.relay_host
        or os.getenv("CLIPSYNC_RELAY_HOST", "")
        or str(config.get("relay_host", ""))
    ).strip()
    port = args.relay_port
    if not host:
        return DEFAULT_RELAY_URL
    return f"ws://{host}:{port}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--cli", action="store_true", help="Run in console mode.")
    parser.add_argument("--relay-url", help="Full WebSocket URL.")
    parser.add_argument("--relay-host", help="Relay host for ws://HOST:PORT.")
    parser.add_argument("--relay-port", type=int, default=8765)
    return parser.parse_args()


EventHandler = Callable[[str, dict[str, Any]], None]


class ClipSyncClient:
    def __init__(self, relay_url: str, pc_id: str, on_event: EventHandler) -> None:
        self.relay_url = relay_url
        self.pc_id = pc_id
        self.on_event = on_event
        self.running = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.ws: Any = None
        self.phone_count = 0
        self.sent_count = 0
        self.generation = 0
        self.reconnect_step = 0
        self._slip_message_handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    def set_slip_message_handler(
        self, handler: Callable[[dict[str, Any]], Awaitable[None]] | None
    ) -> None:
        self._slip_message_handler = handler

    async def send_slip_ack(self, event_id: str) -> None:
        if not self.ws or not event_id:
            return
        await self.ws.send(json.dumps({"action": "slip_ack", "event_id": event_id}))

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.generation += 1
        generation = self.generation
        self.reconnect_step = 0
        self.thread = threading.Thread(
            target=self._thread_main,
            args=(generation,),
            daemon=True,
        )
        self.thread.start()
        self.on_event("status", {"message": "Starting"})

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self.generation += 1
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self.loop)
        self.on_event("status", {"message": "Stopped"})

    def _thread_main(self, generation: int) -> None:
        try:
            asyncio.run(self._run(generation))
        except Exception as exc:
            self.on_event("error", {"message": str(exc)})

    def _is_current(self, generation: int) -> bool:
        return self.running and generation == self.generation

    async def _run(self, generation: int) -> None:
        self.loop = asyncio.get_running_loop()
        await asyncio.gather(
            self._ws_loop(generation),
            self._clipboard_loop(generation),
            self._heartbeat_loop(generation),
        )

    async def _ws_loop(self, generation: int) -> None:
        while self._is_current(generation):
            try:
                self.on_event("status", {"message": "Connecting"})
                async with websockets.connect(
                    self.relay_url,
                    ping_interval=None,
                    ping_timeout=20,
                    max_size=200 * 1024,
                ) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({"action": "register", "id": self.pc_id}))
                    self.reconnect_step = 0
                    self.on_event("connected", {})

                    async for raw in ws:
                        if not self._is_current(generation):
                            break
                        self._handle_server_message(raw)
            except Exception as exc:
                if self._is_current(generation):
                    self.on_event("disconnected", {"message": str(exc)})
            finally:
                self.ws = None

            if self._is_current(generation):
                delay = next_reconnect_delay(self.reconnect_step)
                if self.reconnect_step < len(RECONNECT_STEPS_SECONDS) - 1:
                    self.reconnect_step += 1
                self.on_event("reconnecting", {"delay": delay})
                await self._sleep_reconnect(delay, generation)

    async def _sleep_reconnect(self, delay: int, generation: int) -> None:
        end_at = time.monotonic() + delay
        while self._is_current(generation) and time.monotonic() < end_at:
            await asyncio.sleep(0.2)

    def _handle_server_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return

        msg_type = msg.get("type") or msg.get("status")
        if msg_type == "registered":
            self.phone_count = int(msg.get("phones", 0) or 0)
            self.on_event("registered", {"phones": self.phone_count})
        elif msg_type == "phone_joined":
            new_count = int(msg.get("count", self.phone_count) or 0)
            changed = new_count != self.phone_count
            self.phone_count = new_count
            self.on_event(
                "phone_joined" if changed else "phone_count",
                {"phones": self.phone_count},
            )
        elif msg_type == "phone_count":
            self.phone_count = int(msg.get("count", self.phone_count) or 0)
            self.on_event("phone_count", {"phones": self.phone_count})
        elif msg_type == "heartbeat_ack":
            return
        elif msg_type == "kicked":
            self.on_event("kicked", {})
        elif msg_type == "slip_event":
            handler = self._slip_message_handler
            if handler is not None and self.loop is not None:
                asyncio.run_coroutine_threadsafe(self._dispatch_slip(msg), self.loop)
        elif msg.get("error"):
            self.on_event("error", {"message": str(msg.get("error"))})

    async def _dispatch_slip(self, msg: dict[str, Any]) -> None:
        handler = self._slip_message_handler
        if handler is None:
            return
        try:
            await handler(msg)
        except Exception as exc:
            self.on_event("error", {"message": f"slip handler: {exc}"})

    async def _heartbeat_loop(self, generation: int) -> None:
        while self._is_current(generation):
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            if not self._is_current(generation) or not self.ws:
                continue
            try:
                await self.ws.send(json.dumps({"action": "heartbeat", "role": "pc"}))
            except Exception:
                pass

    async def _clipboard_loop(self, generation: int) -> None:
        last = self._read_clipboard()
        is_win = sys.platform.startswith("win")
        last_seq = 0
        if is_win:
            try:
                last_seq = ctypes.windll.user32.GetClipboardSequenceNumber()
            except Exception:
                is_win = False

        while self._is_current(generation):
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            if is_win:
                try:
                    current_seq = ctypes.windll.user32.GetClipboardSequenceNumber()
                    if current_seq == last_seq:
                        continue
                    last_seq = current_seq
                except Exception:
                    pass

            now = self._read_clipboard()
            if not now or now == last:
                continue
            last = now
            if len(now.encode("utf-8")) > MAX_CLIP_BYTES:
                self.on_event("skip", {"message": "Clipboard is larger than 100 KB"})
                continue
            await self._send_clip(now)

    def _read_clipboard(self) -> str:
        try:
            value = pyperclip.paste()
            return value if isinstance(value, str) else ""
        except Exception:
            return ""

    async def _send_clip(self, text: str) -> None:
        if not self.ws:
            self.on_event("offline_clip", {})
            return
        try:
            await self.ws.send(
                json.dumps({"action": "clip", "text": text}, ensure_ascii=False)
            )
            self.sent_count += 1
            preview = text.replace("\r", " ").replace("\n", " ")[:80]
            self.on_event(
                "clip_sent",
                {
                    "preview": preview,
                    "chars": len(text),
                    "sent": self.sent_count,
                },
            )
        except Exception as exc:
            self.on_event("error", {"message": str(exc)})

    async def _close_ws(self) -> None:
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass


class ClipSyncApp(tk.Tk if tk is not None else object):  # type: ignore[misc]
    def __init__(self, relay_url: str, pc_id: str) -> None:
        super().__init__()
        self.relay_url = relay_url
        self.pc_id = pc_id
        self.client = ClipSyncClient(relay_url, pc_id, self._thread_event)
        self.online = False
        self.running = False
        self.phone_count = 0
        self.update_url = ""
        self.update_version = ""
        self.update_checking = False
        self.slip_event_queue: queue.Queue = queue.Queue()
        self.debug_panel: Optional[DebugPanel] = None
        self.settings_panel: Optional[SettingsPanel] = None
        self.audit_viewer: Optional[AuditViewer] = None
        self._slip_override_bridge: Any = None
        self._slip_orchestrator: Any = None
        self._slip_bootstrap: Any = None
        self._on_slip_config_reload: Optional[Callable[[dict[str, Any]], None]] = None

        self.title(APP_NAME)
        self.geometry("720x640")
        self.minsize(560, 560)
        self.configure(bg="#f6f8fb")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_window_icon()
        self._build_ui()
        self.after(350, self._start_sync)
        self.after(500, self._start_slip_stack)

    def _start_slip_stack(self) -> None:
        # Always start chrome bridge + slip stack so the extension can connect
        # even before a phone has paired. Shared secret is created on first run.
        try:
            shared_secret = load_or_create_shared_secret()
            self._slip_bootstrap = start_slip_bootstrap(self, self.client, shared_secret)
            self._append_log("Slip stack starting (Chrome bridge ws://127.0.0.1:8765)…")
        except Exception as exc:
            self._append_log(f"Slip bootstrap failed: {exc}")
            try:
                from tkinter import messagebox

                messagebox.showerror(
                    "ClipSync Slip",
                    f"Chrome bridge ไม่เปิดได้ — extension จะต่อไม่ได้\n\n{exc}",
                )
            except Exception:
                pass

    def _load_window_icon(self) -> None:
        icon_path = resource_path("assets/clipsync_icon.png")
        if icon_path.exists():
            try:
                self._icon_image = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, self._icon_image)
            except Exception:
                pass

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#667085")
        style.configure("Title.TLabel", background="#ffffff", foreground="#101828")
        style.configure("Primary.TButton", padding=(14, 9), font=("Segoe UI", 10, "bold"))
        style.configure("Secondary.TButton", padding=(10, 7), font=("Segoe UI", 9))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.notebook = notebook

        clipboard_tab = ttk.Frame(notebook, style="Card.TFrame")
        notebook.add(clipboard_tab, text="Clipboard")

        slip_tab = ttk.Frame(notebook, style="Card.TFrame")
        notebook.add(slip_tab, text="Slip")
        self.debug_panel = DebugPanel(
            slip_tab,
            self.slip_event_queue,
            on_manual_confirm=self._on_slip_manual_confirm,
            on_reject=self._on_slip_reject,
            on_view_slip=self._on_slip_view,
        )

        settings_tab = ttk.Frame(notebook, style="Card.TFrame")
        notebook.add(settings_tab, text="Settings")
        self.settings_panel = SettingsPanel(
            settings_tab,
            on_reload=self._on_settings_reload,
            on_push_profiles=self.push_site_profiles_to_extension,
        )

        audit_tab = ttk.Frame(notebook, style="Card.TFrame")
        notebook.add(audit_tab, text="ประวัติ")
        self.audit_viewer = AuditViewer(audit_tab)

        outer = ttk.Frame(clipboard_tab, style="Card.TFrame", padding=22)
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        header = ttk.Frame(outer, style="Card.TFrame")
        header.pack(fill="x")

        self.logo = tk.Canvas(
            header,
            width=44,
            height=44,
            bg="#ffffff",
            highlightthickness=0,
        )
        self.logo.pack(side="left")
        self._draw_logo(self.logo)

        title_box = ttk.Frame(header, style="Card.TFrame")
        title_box.pack(side="left", fill="x", expand=True, padx=(12, 0))
        ttk.Label(
            title_box,
            text=f"{APP_NAME}  v{APP_VERSION}",
            style="Title.TLabel",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            title_box,
            text=f"By {AUTHOR_NAME}",
            style="Muted.TLabel",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        self.status_canvas = tk.Canvas(
            header,
            width=112,
            height=30,
            bg="#ffffff",
            highlightthickness=0,
        )
        self.status_canvas.pack(side="right")

        ttk.Separator(outer).pack(fill="x", pady=18)

        ttk.Label(
            outer,
            text="PC ID",
            style="Muted.TLabel",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        id_row = ttk.Frame(outer, style="Card.TFrame")
        id_row.pack(fill="x", pady=(8, 14))
        self.id_var = tk.StringVar(value=fmt_id(self.pc_id))
        id_entry = ttk.Entry(
            id_row,
            textvariable=self.id_var,
            font=("Consolas", 22, "bold"),
            justify="center",
            state="readonly",
        )
        id_entry.pack(fill="x", expand=True)
        id_actions = ttk.Frame(outer, style="Card.TFrame")
        id_actions.pack(fill="x", pady=(0, 14))
        ttk.Button(
            id_actions,
            text="Copy ID",
            style="Secondary.TButton",
            command=self._copy_id,
        ).pack(side="left")
        ttk.Button(
            id_actions,
            text="Show QR",
            style="Secondary.TButton",
            command=self._show_qr,
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            outer,
            text="Relay",
            style="Muted.TLabel",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        self.relay_var = tk.StringVar(value=self.relay_url)
        ttk.Entry(
            outer,
            textvariable=self.relay_var,
            font=("Segoe UI", 9),
            state="readonly",
        ).pack(fill="x", pady=(8, 16))

        actions = ttk.Frame(outer, style="Card.TFrame")
        actions.pack(fill="x", pady=(0, 16))
        self.sync_button = ttk.Button(
            actions,
            text="Pause Sync",
            style="Primary.TButton",
            command=self._toggle_sync,
        )
        self.sync_button.pack(side="left")
        self.stats_var = tk.StringVar(value="Phones: 0   Sent: 0")
        ttk.Label(
            actions,
            textvariable=self.stats_var,
            style="Muted.TLabel",
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(16, 0))
        ttk.Button(
            actions,
            text="Check Update",
            style="Secondary.TButton",
            command=lambda: self._maybe_check_update(force=True),
        ).pack(side="right")

        self.status_var = tk.StringVar(value="Starting...")
        ttk.Label(
            outer,
            textvariable=self.status_var,
            style="Title.TLabel",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        self.update_frame = ttk.Frame(outer, style="Card.TFrame")
        self.update_var = tk.StringVar(value="")
        ttk.Label(
            self.update_frame,
            textvariable=self.update_var,
            style="Muted.TLabel",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", fill="x", expand=True)
        self.update_button = ttk.Button(
            self.update_frame,
            text="Install",
            style="Secondary.TButton",
            command=self._install_update,
        )
        self.update_button.pack(side="right", padx=(10, 0))

        self.last_var = tk.StringVar(value="Last clipboard: -")
        self.last_label = ttk.Label(
            outer,
            textvariable=self.last_var,
            style="Muted.TLabel",
            font=("Segoe UI", 9),
            wraplength=410,
        )
        self.last_label.pack(anchor="w", pady=(0, 12))

        ttk.Label(
            outer,
            text="Activity",
            style="Muted.TLabel",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        self.log = tk.Text(
            outer,
            height=11,
            relief="flat",
            bg="#f2f4f7",
            fg="#344054",
            padx=10,
            pady=8,
            font=("Consolas", 9),
            state="disabled",
            wrap="word",
        )
        self.log.pack(fill="both", expand=True, pady=(8, 0))

        self._set_status("Starting", "#e09c18")
        self._append_log(f"ID file: {ID_FILE}")
        self.after(1600, self._maybe_check_update)

    def _draw_logo(self, canvas: tk.Canvas) -> None:
        canvas.create_rectangle(2, 2, 42, 42, fill="#2646d8", outline="#2646d8", width=0)
        canvas.create_rectangle(12, 10, 31, 34, fill="#ffffff", outline="#ffffff")
        canvas.create_rectangle(15, 7, 28, 13, fill="#7cf2c8", outline="#7cf2c8")
        canvas.create_line(16, 19, 28, 19, fill="#2646d8", width=3)
        canvas.create_line(16, 25, 28, 25, fill="#2646d8", width=3)

    def push_slip_ui_event(self, event: Mapping[str, Any]) -> None:
        """Enqueue a slip decision for the Slip debug tab (thread-safe)."""
        self.slip_event_queue.put(dict(event))

    def set_slip_override_bridge(self, bridge: Any) -> None:
        """Optional ChromeBridge used by manual confirm from the Slip tab."""
        self._slip_override_bridge = bridge

    def push_site_profiles_to_extension(self) -> None:
        """Load non-example profiles and push to the connected Chrome extension."""
        from clipsync.ext_installer import site_profiles_dir
        from clipsync.site_profiles import load_profiles

        bridge = getattr(self, "_slip_override_bridge", None)
        if bridge is None:
            raise RuntimeError(
                "Chrome bridge ยังไม่พร้อม — รอสักครู่แล้วกดอีกครั้ง "
                "(ต้องมี pairing token และ extension ขึ้น connected)"
            )

        profiles_dir = site_profiles_dir()

        profiles: list[dict[str, Any]] = []
        for path in sorted(profiles_dir.glob("*.json")):
            if path.name.lower().startswith("example"):
                continue
            profiles.extend(load_profiles(path))
        if not profiles:
            profiles = load_profiles(profiles_dir)

        n_clients = bridge.schedule(bridge.push_site_profiles(profiles)).result(timeout=5)
        ids = ", ".join(p.get("profile_id", "?") for p in profiles)
        if not n_clients:
            raise RuntimeError(
                "ไม่มี extension ที่ connected — เปิด popup ให้ขึ้น connected ก่อน แล้วกด Push อีกครั้ง"
            )
        self._append_log(f"Pushed site profiles ({ids}) → {n_clients} extension(s)")
        if messagebox is not None:
            messagebox.showinfo(
                "Site Profiles",
                f"ส่งแล้ว: {ids}\nไปที่ popup extension — ควรเห็น Profiles ≥ 1",
            )

    def set_slip_orchestrator(self, orchestrator: Any) -> None:
        """Optional SlipOrchestrator for config hot-reload from Settings."""
        self._slip_orchestrator = orchestrator

    def set_slip_config_reload_handler(
        self, handler: Optional[Callable[[dict[str, Any]], None]]
    ) -> None:
        self._on_slip_config_reload = handler

    def on_transport_changed(self, old: Optional[str], new: str) -> None:
        """Forward TransportManager callback onto the Settings status strip."""
        if self.settings_panel is not None:
            self.settings_panel.on_transport_changed(old, new)

    def _on_settings_reload(self, cfg: dict[str, Any]) -> None:
        if self._slip_orchestrator is not None:
            update = getattr(self._slip_orchestrator, "update_config", None)
            if callable(update):
                update(cfg)
        if self._on_slip_config_reload is not None:
            self._on_slip_config_reload(cfg)
        mode = (cfg.get("transport") or {}).get("preferred_mode", "?")
        self._append_log(f"Slip config reloaded (transport={mode})")

    def _on_slip_view(self, event: Mapping[str, Any]) -> None:
        if messagebox is None:
            return
        event_id = event.get("event_id") or "-"
        thumb = event.get("thumbnail_jpeg_b64")
        if isinstance(thumb, str) and thumb:
            try:
                from clipsync.slip_image import decode_thumbnail_jpeg
                from PIL import Image, ImageTk
                import io

                raw = decode_thumbnail_jpeg(thumb)
                if raw is None:
                    raise ValueError("bad thumbnail")
                img = Image.open(io.BytesIO(raw))
                top = tk.Toplevel(self)
                top.title(f"สลิป {event_id}")
                photo = ImageTk.PhotoImage(img)
                top._photo = photo  # type: ignore[attr-defined]  # keep ref
                ttk.Label(top, image=photo).pack(padx=8, pady=8)
                return
            except Exception as exc:
                messagebox.showwarning(
                    "ดูรูปสลิป",
                    f"มี thumbnail แต่เปิดไม่ได้: {exc}",
                )
                return
        messagebox.showinfo(
            "ดูรูปสลิป",
            f"ยังไม่มีรูปสำหรับ event {event_id}\n"
            "(รอสลิปใหม่หลังอัปเดตแอป หรือเชื่อม USB + slip_fetcher)",
        )

    def _on_slip_manual_confirm(self, event: Mapping[str, Any]) -> None:
        order_id = event.get("order_id") or event.get("orderId")
        amount = event.get("amount")
        try:
            if amount is not None and str(amount).strip() != "":
                amount = f"{float(amount):.2f}"
        except (TypeError, ValueError):
            amount = str(amount).strip() if amount is not None else None
        ref_number = event.get("ref_number") or event.get("refNumber")
        slip_payload = {
            k: event.get(k)
            for k in (
                "amount",
                "bank",
                "bank_name",
                "bank_name_th",
                "account_number",
                "ref_number",
                "sender",
                "sender_name",
                "sender_account",
                "sender_account_last4",
                "sender_account_masked",
                "receiver",
                "receiver_account",
                "receiver_account_last4",
                "receiver_account_masked",
                "receiver_bank",
                "receiver_bank_name",
                "receiver_bank_name_th",
            )
            if event.get(k) is not None
        }
        # Normalize alternate keys from mobile OCR payload.
        if "bank_name" not in slip_payload and event.get("bank"):
            slip_payload["bank_name"] = event.get("bank")
        if "receiver_account_last4" not in slip_payload and event.get("receiverAccountLast4"):
            slip_payload["receiver_account_last4"] = event.get("receiverAccountLast4")
        if "receiver_bank" not in slip_payload and event.get("receiverBank"):
            slip_payload["receiver_bank"] = event.get("receiverBank")
        if "sender_name" not in slip_payload and event.get("senderName"):
            slip_payload["sender_name"] = event.get("senderName")
        # The external slip app forwards only the receiver account; the close-job
        # form needs the payer ("จาก") account. Recover it by OCR'ing the slip
        # image locally so we can auto-pick the correct rotating shop account.
        if not slip_payload.get("sender_account_last4"):
            thumb = event.get("thumbnail_jpeg_b64")
            if isinstance(thumb, str) and thumb:
                try:
                    from clipsync.slip_image import decode_thumbnail_jpeg
                    from clipsync.slip_ocr import extract_sender_account_last4

                    raw_img = decode_thumbnail_jpeg(thumb)
                    last4 = extract_sender_account_last4(raw_img) if raw_img else None
                    if last4:
                        slip_payload["sender_account_last4"] = last4
                        self._append_log(f"Slip OCR: payer account last4 = {last4}")
                    else:
                        self._append_log(
                            "Slip OCR: payer account not readable "
                            "(Tesseract missing or image unclear)"
                        )
                except Exception as exc:  # pragma: no cover - defensive
                    self._append_log(f"Slip OCR failed: {exc}")
        match_key = order_id or ref_number or amount
        record = {
            "event_id": event.get("event_id"),
            "ref_number": ref_number,
            "amount": amount,
            "order_id": order_id,
            "decision": "dry_run_sent" if match_key else "skipped",
            "confirmed_by": "admin_manual",
        }
        append_audit(record)

        bridge = getattr(self, "_slip_override_bridge", None)
        n_clients = 0
        if bridge is None:
            self._append_log("Manual confirm: Chrome bridge not ready")
            if messagebox is not None:
                messagebox.showwarning(
                    "ยืนยันเอง",
                    "Chrome bridge ยังไม่พร้อม — รอ extension connected แล้วลองใหม่",
                )
            return
        if not match_key:
            self._append_log("Manual confirm: no order/ref/amount to match on admin page")
            if messagebox is not None:
                messagebox.showwarning(
                    "ยืนยันเอง",
                    "สลิปนี้ไม่มี Order / Ref / จำนวน — จับแถวบนหลังบ้านไม่ได้",
                )
            return
        try:
            n_clients = bridge.schedule(
                bridge.push_confirm_order(
                    "" if order_id is None else str(order_id),
                    amount=amount,
                    ref_number=ref_number,
                    slip=slip_payload or None,
                )
            ).result(timeout=5)
        except Exception as exc:
            self._append_log(f"Manual confirm push failed: {exc}")
            if messagebox is not None:
                messagebox.showerror("ยืนยันเอง", str(exc))
            return

        if not n_clients:
            self._append_log("Manual confirm: no extension connected")
            if messagebox is not None:
                messagebox.showwarning(
                    "ยืนยันเอง",
                    "ไม่มี extension ที่ connected — เปิด popup ให้ขึ้น connected ก่อน",
                )
            return

        ui_event = {
            **dict(event),
            "decision": "pending review",
            "confirm_hint": "dry_run_sent",
            "confirmed_by": "admin_manual",
            "order_id": order_id,
        }
        self.push_slip_ui_event(ui_event)
        self._append_log(
            f"Admin dry-run confirm sent ({match_key}) → {n_clients} extension(s) "
            f"— ดูกรอบแดงบนหน้าหลังบ้าน"
        )
        if messagebox is not None:
            messagebox.showinfo(
                "ยืนยันเอง (dry-run)",
                f"ส่งไป extension แล้ว (จับด้วย: {match_key})\n"
                "กลับไปดูแท็บหลังบ้าน — ควรเห็นกรอบแดงรอบปุ่มตา/ปุ่มเป้าหมาย",
            )

    def _on_slip_reject(self, event: Mapping[str, Any]) -> None:
        record = {
            "event_id": event.get("event_id"),
            "ref_number": event.get("ref_number"),
            "amount": event.get("amount"),
            "order_id": event.get("order_id") or event.get("orderId"),
            "decision": "overridden",
            "confirmed_by": "admin_manual",
        }
        append_audit(record)
        ui_event = {**dict(event), "decision": "overridden", "confirmed_by": "admin_manual"}
        self.push_slip_ui_event(ui_event)
        self._append_log(f"Admin reject/override: {event.get('event_id')}")

    def _thread_event(self, name: str, data: dict[str, Any]) -> None:
        self.after(0, lambda: self._handle_event(name, data))

    def _handle_event(self, name: str, data: dict[str, Any]) -> None:
        if name == "status":
            self._set_status(data.get("message", "Working"), "#e09c18")
        elif name == "connected":
            self.online = True
            self._set_status("Connected", "#19a94b")
            self._append_log("Connected to relay")
        elif name == "registered":
            self.phone_count = int(data.get("phones", 0) or 0)
            self._set_status("Ready for copy", "#19a94b")
            self._append_log(f"Registered. Phones connected: {self.phone_count}")
        elif name == "phone_joined":
            self.phone_count = int(data.get("phones", self.phone_count) or 0)
            self._append_log(f"Phone connected. Total {self.phone_count}")
        elif name == "phone_count":
            self.phone_count = int(data.get("phones", self.phone_count) or 0)
        elif name == "clip_sent":
            preview = str(data.get("preview", ""))
            self.last_var.set(f"Last clipboard: {preview}")
            self._append_log(f"Sent {data.get('chars', 0)} chars")
        elif name == "disconnected":
            self.online = False
            self._set_status("Reconnecting", "#e09c18")
            self._append_log(f"Disconnected: {data.get('message', '')}")
        elif name == "reconnecting":
            self._set_status("Reconnecting", "#e09c18")
            self._append_log(f"Reconnect in {data.get('delay', '')} seconds")
        elif name == "kicked":
            self._set_status("ID is open on another PC", "#d92d20")
            self._append_log("This PC ID was kicked by another connection")
        elif name == "skip":
            self._append_log(str(data.get("message", "Skipped")))
        elif name == "offline_clip":
            self._append_log("Clipboard changed while relay is offline")
        elif name == "error":
            self._set_status("Error", "#d92d20")
            self._append_log(f"Error: {data.get('message', '')}")
        elif name == "update_available":
            self.update_version = str(data.get("version", ""))
            self.update_url = str(data.get("url", ""))
            self.update_var.set(f"Update available: v{self.update_version}")
            if not self.update_frame.winfo_ismapped():
                self.update_frame.pack(
                    fill="x",
                    pady=(0, 10),
                    before=self.last_label,
                )
            self._append_log(f"Update available v{self.update_version}")
        elif name == "update_status":
            self._append_log(str(data.get("message", "")))
        elif name == "update_none":
            self._append_log("Already up to date")
        elif name == "update_error":
            self._append_log(f"Update check failed: {data.get('message', '')}")
        elif name == "close_after_update":
            self.after(900, self._on_close)

        self.stats_var.set(f"Phones: {self.phone_count}   Sent: {self.client.sent_count}")

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_canvas.delete("all")
        fill = "#ecfdf3" if color == "#19a94b" else "#fff7e6" if color == "#e09c18" else "#fef3f2"
        self.status_canvas.create_rectangle(0, 2, 110, 28, fill=fill, outline=fill)
        self.status_canvas.create_oval(10, 11, 18, 19, fill=color, outline=color)
        self.status_canvas.create_text(60, 15, text=text[:13], fill=color, font=("Segoe UI", 9, "bold"))

    def _append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {message}\n")
        lines = self.log.get("1.0", "end-1c").splitlines()
        if len(lines) > 80:
            self.log.delete("1.0", f"{len(lines) - 80}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _copy_id(self) -> None:
        pyperclip.copy(fmt_id(self.pc_id))
        self._append_log("Copied PC ID")

    def _show_qr(self) -> None:
        win = tk.Toplevel(self)
        win.title("Pair Phone")
        win.configure(bg="#ffffff")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        frame = ttk.Frame(win, style="Card.TFrame", padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Scan this QR with ClipSync on your phone",
            style="Title.TLabel",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="center")
        ttk.Label(
            frame,
            text=fmt_id(self.pc_id),
            style="Muted.TLabel",
            font=("Consolas", 18, "bold"),
        ).pack(anchor="center", pady=(6, 10))

        canvas = tk.Canvas(
            frame,
            width=276,
            height=276,
            bg="#ffffff",
            highlightthickness=0,
        )
        canvas.pack()
        self._draw_qr(canvas, pairing_url(self.pc_id))

        ttk.Button(
            frame,
            text="Close",
            style="Secondary.TButton",
            command=win.destroy,
        ).pack(pady=(14, 0))

    def _draw_qr(self, canvas: tk.Canvas, data: str) -> None:
        qr = qrcode.QRCode(border=2, box_size=1)
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        cells = len(matrix)
        size = int(canvas["width"])
        quiet = 8
        cell = max(1, (size - quiet * 2) // cells)
        qr_size = cell * cells
        offset = (size - qr_size) // 2

        canvas.create_rectangle(0, 0, size, size, fill="#ffffff", outline="#ffffff")
        for y, row in enumerate(matrix):
            for x, value in enumerate(row):
                if not value:
                    continue
                left = offset + x * cell
                top = offset + y * cell
                canvas.create_rectangle(
                    left,
                    top,
                    left + cell,
                    top + cell,
                    fill="#101828",
                    outline="#101828",
                )

    def _maybe_check_update(self, force: bool = False) -> None:
        if self.update_checking or not should_check_update(force):
            return
        self.update_checking = True
        if force:
            self._append_log("Checking for updates")
        threading.Thread(
            target=self._check_update_thread,
            args=(force,),
            daemon=True,
        ).start()

    def _check_update_thread(self, force: bool) -> None:
        try:
            update = pc_update_from_manifest(fetch_update_manifest())
            if update:
                self._thread_event("update_available", update)
            elif force:
                self._thread_event("update_none", {})
        except Exception as exc:
            if force:
                self._thread_event("update_error", {"message": str(exc)})
        finally:
            self.update_checking = False

    def _install_update(self) -> None:
        if not self.update_url:
            return
        self.update_button.configure(state="disabled")
        self.update_var.set(f"Downloading v{self.update_version}...")
        threading.Thread(target=self._download_update_thread, daemon=True).start()

    def _download_update_thread(self) -> None:
        try:
            filename = Path(urllib.parse.urlparse(self.update_url).path).name
            if not filename:
                filename = "ClipSyncPC_Setup.exe"
            target = Path(tempfile.gettempdir()) / filename
            request = urllib.request.Request(
                self.update_url,
                headers={"User-Agent": f"ClipSyncPC/{APP_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                target.write_bytes(response.read())
            subprocess.Popen([str(target)], close_fds=True)
            self._thread_event(
                "update_status",
                {"message": "Installer opened. ClipSync will close now."},
            )
            self._thread_event("close_after_update", {})
        except Exception as exc:
            self._thread_event("update_error", {"message": str(exc)})
            self.after(0, lambda: self.update_button.configure(state="normal"))

    def _start_sync(self) -> None:
        self.running = True
        self.sync_button.configure(text="Pause Sync")
        self.client.start()

    def _toggle_sync(self) -> None:
        if self.running:
            self.client.stop()
            self.running = False
            self.sync_button.configure(text="Start Sync")
            self._set_status("Paused", "#e09c18")
        else:
            self._start_sync()

    def _on_close(self) -> None:
        bootstrap = self._slip_bootstrap
        if bootstrap is not None:
            bootstrap.stop()
        self.client.stop()
        self.after(250, self.destroy)


async def run_cli(relay_url: str, pc_id: str) -> None:
    def handle_event(name: str, data: dict[str, Any]) -> None:
        if name == "registered":
            print(f"ID: {fmt_id(pc_id)}")
            print(f"Phones connected: {data.get('phones', 0)}")
            print("Waiting for copy...")
        elif name == "clip_sent":
            print(f"Sent: {data.get('preview', '')}")
        elif name == "phone_joined":
            print(f"Phone connected. Total {data.get('phones', 0)}")
        elif name == "phone_count":
            print(f"Phones connected: {data.get('phones', 0)}")
        elif name == "disconnected":
            print(f"Disconnected: {data.get('message', '')}")
        elif name == "error":
            print(f"Error: {data.get('message', '')}")
        elif name == "kicked":
            print("This ID is being used elsewhere.")

    client = ClipSyncClient(relay_url, pc_id, handle_event)
    client.running = True
    client.generation += 1
    try:
        await client._run(client.generation)
    except KeyboardInterrupt:
        client.stop()


def main() -> None:
    args = parse_args()
    relay_url = resolve_relay_url(args)
    pc_id = load_or_create_id()

    if args.cli or tk is None:
        print(f"{APP_NAME} v{APP_VERSION} - By {AUTHOR_NAME}")
        print(f"Relay: {relay_url}")
        asyncio.run(run_cli(relay_url, pc_id))
        return

    app = ClipSyncApp(relay_url, pc_id)
    app.mainloop()


if __name__ == "__main__":
    main()
