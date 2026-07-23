"""Serve / hand off ClipSync APK to a phone over USB tether (no internet, no ADB)."""

from __future__ import annotations

import logging
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from clipsync.ext_installer import app_base_dir
from clipsync.transport.usb import TETHER_NIC_HINTS

logger = logging.getLogger(__name__)

DEFAULT_APK_PORT = 8788
APK_FILENAMES = (
    "ClipSync-slip-debug.apk",
    "ClipSync.apk",
    "app-debug.apk",
    "app-release.apk",
)


def find_bundled_apk(base: Path | None = None) -> Path | None:
    """Locate APK next to the PC app, under pc/apk, AppData, or artifacts."""
    roots: list[Path] = []
    app = Path(base) if base is not None else app_base_dir()
    roots.append(app)
    roots.append(app / "apk")
    # source checkout: pc/ -> repo artifacts/
    roots.append(app.parent / "artifacts")
    roots.append(app / "artifacts")
    try:
        from clipsync.config import user_data_dir

        roots.append(user_data_dir() / "apk")
    except Exception:
        pass

    seen: set[Path] = set()
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen:
            continue
        seen.add(root)
        if root.is_file() and root.suffix.lower() == ".apk":
            return root
        if not root.is_dir():
            continue
        for name in APK_FILENAMES:
            candidate = root / name
            if candidate.is_file():
                return candidate
        # any apk in folder
        matches = sorted(root.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


def find_usb_tether_pc_ip() -> str | None:
    """PC IPv4 on an up USB-tether NIC (for phone to download from)."""
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        st = stats.get(name)
        if not st or not st.isup:
            continue
        name_l = name.lower()
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = addr.address
            if ip.startswith("169.254") or ip.startswith("127."):
                continue
            tetherish = any(h in name_l for h in TETHER_NIC_HINTS) or ip.startswith(
                "192.168.42."
            )
            if tetherish:
                return ip
    return None


def apk_download_url(pc_ip: str, filename: str, *, port: int = DEFAULT_APK_PORT) -> str:
    return f"http://{pc_ip}:{port}/{filename}"


class _ApkHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        logger.info("apk-http: " + fmt, *args)


class ApkHttpServer:
    """Tiny HTTP server that only serves one APK file at / and /filename."""

    def __init__(self, apk_path: Path, *, host: str, port: int = DEFAULT_APK_PORT) -> None:
        self.apk_path = Path(apk_path).resolve()
        if not self.apk_path.is_file():
            raise FileNotFoundError(str(self.apk_path))
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return apk_download_url(self.host, self.apk_path.name, port=self.port)

    def start(self) -> str:
        if self._httpd is not None:
            return self.url

        apk = self.apk_path
        directory = str(apk.parent)

        class Handler(_ApkHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=directory, **kwargs)

            def do_GET(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path in ("/", f"/{apk.name}"):
                    self.path = f"/{apk.name}"
                    return SimpleHTTPRequestHandler.do_GET(self)
                self.send_error(404, "Not found")

        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="apk-http", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None


_active_server: ApkHttpServer | None = None


def start_apk_share(
    apk_path: Path | None = None,
    *,
    port: int = DEFAULT_APK_PORT,
) -> dict[str, str]:
    """
    Start serving APK on the USB-tether PC IP.

    Returns dict with keys: url, apk_path, pc_ip
    """
    global _active_server
    path = Path(apk_path) if apk_path else find_bundled_apk()
    if path is None:
        raise FileNotFoundError(
            "ไม่พบไฟล์ APK — วาง ClipSync-slip-debug.apk ที่ pc/apk/ หรือ artifacts/"
        )
    pc_ip = find_usb_tether_pc_ip()
    if not pc_ip:
        raise RuntimeError(
            "ยังไม่เจอ USB tethering NIC — เปิด USB tethering บนมือถือก่อน "
            "(ไม่ต้องใช้เน็ตมือถือ ก็ได้)"
        )

    if _active_server is not None:
        _active_server.stop()
        _active_server = None

    server = ApkHttpServer(path, host=pc_ip, port=port)
    url = server.start()
    _active_server = server
    return {"url": url, "apk_path": str(path), "pc_ip": pc_ip}


def stop_apk_share() -> None:
    global _active_server
    if _active_server is not None:
        _active_server.stop()
        _active_server = None


def download_apk_from_url(
    url: str | None = None,
    *,
    dest_dir: Path | None = None,
    timeout: float = 120.0,
) -> Path:
    """Download APK from GitHub (or any URL) into AppData apk folder.

    Phone should NOT download from GitHub directly — PC pulls first, then USB share.
    """
    import urllib.request

    from clipsync.config import load_config, user_data_dir

    if not url:
        cfg = load_config()
        url = str((cfg.get("apk") or {}).get("download_url") or "").strip()
    if not url:
        raise ValueError("apk.download_url is empty")

    out_dir = Path(dest_dir) if dest_dir else (user_data_dir() / "apk")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").split("/")[-1] or "ClipSync-slip.apk"
    if not name.lower().endswith(".apk"):
        name = "ClipSync-slip.apk"
    dest = out_dir / name

    req = urllib.request.Request(url, headers={"User-Agent": "ClipSyncPC/0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if len(data) < 1000:
        raise RuntimeError(f"Download too small ({len(data)} bytes) — check release URL")
    dest.write_bytes(data)
    return dest


def start_apk_share_from_config() -> dict[str, str]:
    """Download if missing optional; always prefer local AppData apk then share over USB."""
    from clipsync.config import load_config

    cfg = load_config()
    apk_cfg = cfg.get("apk") or {}
    port = int(apk_cfg.get("share_port") or DEFAULT_APK_PORT)
    local = find_bundled_apk()
    if local is None:
        local = download_apk_from_url(str(apk_cfg.get("download_url") or ""))
    else:
        # Keep a stable copy in AppData
        local = copy_apk_to_appdata(local)
    return start_apk_share(local, port=port)


def open_apk_folder(apk_path: Path | None = None) -> Path:
    """Open Explorer on the APK so user can drag-drop via MTP file transfer."""
    path = Path(apk_path) if apk_path else find_bundled_apk()
    if path is None:
        raise FileNotFoundError("ไม่พบไฟล์ APK")
    folder = path.parent
    # Explorer select file
    import subprocess

    subprocess.Popen(["explorer", "/select,", str(path.resolve())])
    return path


def copy_apk_to_appdata(apk_path: Path | None = None) -> Path:
    """Copy APK into %APPDATA%\\ClipSync\\apk for a stable local path."""
    import shutil

    from clipsync.config import user_data_dir

    src = Path(apk_path) if apk_path else find_bundled_apk()
    if src is None:
        raise FileNotFoundError("ไม่พบไฟล์ APK")
    dest_dir = user_data_dir() / "apk"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest
