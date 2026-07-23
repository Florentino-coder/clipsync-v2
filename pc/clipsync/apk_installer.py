"""Serve / hand off ClipSync APK to a phone over PC Mobile Hotspot (no ADB)."""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from clipsync.ext_installer import app_base_dir

logger = logging.getLogger(__name__)

DEFAULT_APK_PORT = 8788
# Windows Mobile Hotspot host is always this gateway on the 192.168.137.0/24 LAN.
WINDOWS_HOTSPOT_HOST_IP = "192.168.137.1"
APK_FILENAMES = (
    "ClipSync-slip.apk",
    "ClipSync-slip-debug.apk",
    "ClipSync.apk",
    "app-debug.apk",
    "app-release.apk",
)


def find_bundled_apk(base: Path | None = None) -> Path | None:
    """Locate APK next to the PC app, under pc/apk, AppData, or artifacts.

    When ``base`` is provided (tests), only search under that tree — do not
    fall through to the user's AppData / Downloads.
    """
    roots: list[Path] = []
    scoped = base is not None
    app = Path(base) if scoped else app_base_dir()
    roots.append(app)
    roots.append(app / "apk")
    if not scoped:
        roots.append(app.parent / "artifacts")
        roots.append(app / "artifacts")
        try:
            from clipsync.config import user_data_dir

            roots.append(user_data_dir() / "apk")
        except Exception:
            pass
        roots.append(Path.home() / "Downloads")

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
        matches = sorted(root.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


def find_hotspot_pc_ip() -> str | None:
    """Return Windows Mobile Hotspot host IPv4 when the hotspot NIC is up."""
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        st = stats.get(name)
        if not st or not st.isup:
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            if addr.address == WINDOWS_HOTSPOT_HOST_IP:
                return WINDOWS_HOTSPOT_HOST_IP
    return None


def apk_download_url(pc_ip: str, filename: str, *, port: int = DEFAULT_APK_PORT) -> str:
    return f"http://{pc_ip}:{port}/{filename}"


def make_apk_qr_png(url: str, dest: Path) -> Path:
    """Write a scannable QR PNG for the APK HTTP URL."""
    import qrcode

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = qrcode.make(url)
    img.save(dest)
    return dest


def open_mobile_hotspot_settings() -> None:
    """Open Windows Settings → Mobile hotspot (ms-settings URI)."""
    subprocess.Popen(
        ["cmd", "/c", "start", "", "ms-settings:network-mobilehotspot"],
        shell=False,
    )


class _ApkHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        logger.info("apk-http: " + fmt, *args)


class ApkHttpServer:
    """HTTP server bound to all interfaces; URL advertises the phone-facing IP."""

    def __init__(
        self,
        apk_path: Path,
        *,
        advertise_host: str,
        port: int = DEFAULT_APK_PORT,
        bind_host: str = "0.0.0.0",
    ) -> None:
        self.apk_path = Path(apk_path).resolve()
        if not self.apk_path.is_file():
            raise FileNotFoundError(str(self.apk_path))
        self.advertise_host = advertise_host
        self.bind_host = bind_host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return apk_download_url(self.advertise_host, self.apk_path.name, port=self.port)

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

        httpd = ThreadingHTTPServer((self.bind_host, self.port), Handler)
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
    Start serving APK for phones on the PC Mobile Hotspot LAN.

    Binds 0.0.0.0; advertises http://192.168.137.1:port/…
    Returns dict with keys: url, apk_path, pc_ip, qr_path
    """
    global _active_server
    path = Path(apk_path) if apk_path else find_bundled_apk()
    if path is None:
        raise FileNotFoundError(
            "ไม่พบไฟล์ APK — กดดาวน์โหลดจาก GitHub ก่อน หรือวางไฟล์ที่ Downloads / pc/apk/"
        )
    pc_ip = find_hotspot_pc_ip()
    if not pc_ip:
        raise RuntimeError(
            "ยังไม่เจอ Mobile Hotspot ของ PC\n"
            "1) เปิด Settings → Mobile hotspot\n"
            "2) เปิดสวิตช์ Mobile hotspot\n"
            "3) ให้มือถือต่อ Wi‑Fi ชื่อเครื่อง PC\n"
            "แล้วกดแชร์อีกครั้ง"
        )

    if _active_server is not None:
        _active_server.stop()
        _active_server = None

    server = ApkHttpServer(path, advertise_host=pc_ip, port=port, bind_host="0.0.0.0")
    url = server.start()
    _active_server = server

    from clipsync.config import user_data_dir

    qr_path = make_apk_qr_png(url, user_data_dir() / "apk" / "share-qr.png")
    return {
        "url": url,
        "apk_path": str(path),
        "pc_ip": pc_ip,
        "qr_path": str(qr_path),
    }


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

    Phone should NOT download from GitHub directly — PC pulls first, then hotspot share.
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
    name = url.rstrip("/").split("/")[-1] or "ClipSync.apk"
    if not name.lower().endswith(".apk"):
        name = "ClipSync.apk"
    # Normalize slip release name so Share finds ClipSync.apk too.
    if name.lower() == "clipsync-slip.apk":
        name = "ClipSync.apk"
    dest = out_dir / name

    req = urllib.request.Request(url, headers={"User-Agent": "ClipSyncPC/0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if len(data) < 1000:
        raise RuntimeError(f"Download too small ({len(data)} bytes) — check release URL")
    dest.write_bytes(data)
    # Also keep ClipSync-slip.apk alias if release used that name in URL.
    slip_alias = out_dir / "ClipSync-slip.apk"
    if dest.name == "ClipSync.apk" and "ClipSync-slip.apk" in url:
        slip_alias.write_bytes(data)
    return dest


def start_apk_share_from_config() -> dict[str, str]:
    """Download if missing; prefer AppData apk then share over hotspot."""
    from clipsync.config import load_config

    cfg = load_config()
    apk_cfg = cfg.get("apk") or {}
    port = int(apk_cfg.get("share_port") or DEFAULT_APK_PORT)
    local = find_bundled_apk()
    if local is None:
        local = download_apk_from_url(str(apk_cfg.get("download_url") or ""))
    else:
        local = copy_apk_to_appdata(local)
    return start_apk_share(local, port=port)


def open_apk_folder(apk_path: Path | None = None) -> Path:
    """Open Explorer on the APK folder."""
    path = Path(apk_path) if apk_path else find_bundled_apk()
    if path is None:
        raise FileNotFoundError("ไม่พบไฟล์ APK")
    import subprocess as sp

    sp.Popen(["explorer", "/select,", str(path.resolve())])
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
