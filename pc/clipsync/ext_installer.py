"""Chrome extension installer helper and update checker."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

import pyperclip

try:
    import tkinter as tk
    from tkinter import messagebox
except Exception:  # pragma: no cover - used only when Tk is unavailable.
    tk = None
    messagebox = None

EXTENSION_DIRNAME = "chrome-extension"
MANIFEST_NAME = "manifest.json"
DEFAULT_VERSION_JSON = Path("release") / "version.json"

_LOAD_HINT = (
    "Enable Developer mode, then click Load unpacked and select the folder "
    "path copied to your clipboard."
)
_RELOAD_HINT = (
    "Extension updated. Open chrome://extensions and click Reload on ClipSync."
)

FetchBytesFn = Callable[[str], bytes]


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # clipsync/ext_installer.py -> pc/
    return Path(__file__).resolve().parent.parent


def extension_dir(base: Path | None = None) -> Path:
    root = Path(base) if base is not None else app_base_dir()
    return root / EXTENSION_DIRNAME


def local_manifest_version(extension_path: Path | None = None) -> str:
    path = Path(extension_path) if extension_path is not None else extension_dir()
    manifest_path = path / MANIFEST_NAME if path.name != MANIFEST_NAME else path
    if manifest_path.is_dir():
        manifest_path = manifest_path / MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest.json is invalid")
    version = str(data.get("version", "")).strip()
    if not version:
        raise ValueError("manifest.json missing version")
    return version


def copy_extension_path(base: Path | None = None) -> str:
    path = str(extension_dir(base=base).resolve())
    pyperclip.copy(path)
    return path


def open_chrome_extensions() -> str:
    """Open chrome://extensions; return instruction text (no blocking GUI without tk)."""
    success_msg = (
        f"Opened chrome://extensions/. {_LOAD_HINT}"
    )
    fallback_msg = (
        "Could not open Chrome automatically. Open chrome://extensions/ manually. "
        f"{_LOAD_HINT}"
    )
    try:
        subprocess.Popen(["start", "chrome", "chrome://extensions/"], shell=True)
        return success_msg
    except Exception:
        if tk is not None and messagebox is not None:  # pragma: no cover
            try:
                root = tk.Tk()
                root.withdraw()
                messagebox.showinfo("ClipSync Extension Install", fallback_msg)
                root.destroy()
            except Exception:
                pass
        return fallback_msg


def guide_install(base: Path | None = None) -> str:
    """Copy extension path to clipboard and open Chrome's extensions page."""
    path = copy_extension_path(base=base)
    open_note = open_chrome_extensions()
    return (
        f"Extension path copied to clipboard:\n{path}\n\n{open_note}"
    )


def _parse_version(value: str) -> tuple[int, ...]:
    base, _, build = value.partition("+")
    parts: list[int] = []
    for raw in base.split("."):
        digits = "".join(ch for ch in raw if ch.isdigit())
        parts.append(int(digits or "0"))
    while len(parts) < 3:
        parts.append(0)
    build_digits = "".join(ch for ch in build if ch.isdigit())
    return (*parts[:3], int(build_digits or "0"))


def is_newer_version(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _default_fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ClipSyncPC/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _resolve_extract_source(extracted: Path) -> Path:
    nested = extracted / EXTENSION_DIRNAME
    if nested.is_dir() and (nested / MANIFEST_NAME).is_file():
        return nested
    if (extracted / MANIFEST_NAME).is_file():
        return extracted
    for child in extracted.iterdir():
        if child.is_dir() and (child / MANIFEST_NAME).is_file():
            return child
    raise ValueError("extension zip has no manifest.json")


def extract_extension_zip(zip_source: bytes | Path, target_dir: Path) -> Path:
    """Extract extension zip bytes/path into ``target_dir`` (chrome-extension folder)."""
    target = Path(target_dir)
    if target.name != EXTENSION_DIRNAME:
        target = target / EXTENSION_DIRNAME

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        if isinstance(zip_source, bytes):
            zip_path = tmp_path / "extension.zip"
            zip_path.write_bytes(zip_source)
        else:
            zip_path = Path(zip_source)

        extract_root = tmp_path / "extracted"
        extract_root.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_root)

        source = _resolve_extract_source(extract_root)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    return target


def notify_extension_reload(message: str | None = None) -> str:
    msg = message or _RELOAD_HINT
    if tk is not None and messagebox is not None:  # pragma: no cover
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("ClipSync Extension Update", msg)
            root.destroy()
        except Exception:
            pass
    return msg


def default_version_json_path(base: Path | None = None) -> Path:
    root = Path(base) if base is not None else app_base_dir()
    # Prefer repo-root release/ next to pc/, then pc/release/.
    candidates = [
        root.parent / DEFAULT_VERSION_JSON,
        root / DEFAULT_VERSION_JSON,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _update_result(
    *,
    update_available: bool,
    local_version: str,
    remote_version: str | None,
    download_path: str | None,
    download_note: str | None,
    message: str,
    needs_reload: bool = False,
) -> dict[str, Any]:
    return {
        "update_available": update_available,
        "local_version": local_version,
        "remote_version": remote_version,
        "download_path": download_path,
        "download_note": download_note,
        "needs_reload": needs_reload,
        "message": message,
    }


def check_extension_update(
    *,
    version_json_path: Path | None = None,
    extension_path: Path | None = None,
    apply: bool = False,
    fetch_bytes: FetchBytesFn | None = None,
) -> dict[str, Any]:
    """Compare release/version.json extension.version vs local manifest.json."""
    ext_path = Path(extension_path) if extension_path is not None else extension_dir()
    local_version = local_manifest_version(ext_path)

    vpath = (
        Path(version_json_path)
        if version_json_path is not None
        else default_version_json_path(base=ext_path.parent)
    )
    if not vpath.is_file():
        return _update_result(
            update_available=False,
            local_version=local_version,
            remote_version=None,
            download_path=None,
            download_note=None,
            message=f"No release version file at {vpath}",
        )

    data = json.loads(vpath.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("version.json is invalid")
    ext_info = data.get("extension")
    if not isinstance(ext_info, dict):
        return _update_result(
            update_available=False,
            local_version=local_version,
            remote_version=None,
            download_path=None,
            download_note=None,
            message="version.json has no extension section",
        )

    remote_version = str(ext_info.get("version", "")).strip()
    download_url = str(
        ext_info.get("download_url")
        or ext_info.get("url")
        or ext_info.get("download_path")
        or ""
    ).strip()

    if not remote_version or not is_newer_version(remote_version, local_version):
        return _update_result(
            update_available=False,
            local_version=local_version,
            remote_version=remote_version or local_version,
            download_path=download_url or None,
            download_note=None,
            message=f"Extension is up to date ({local_version}).",
        )

    if download_url:
        download_note = f"Download update from: {download_url}"
        download_path: str | None = download_url
    else:
        # TODO: wire a real release download URL once published to GitHub Releases.
        download_note = (
            f"TODO: no download_url in version.json for extension {remote_version}; "
            "download stub not configured."
        )
        download_path = None

    if apply and download_url:
        try:
            fetch = fetch_bytes or _default_fetch_bytes
            zip_bytes = fetch(download_url)
            extract_extension_zip(zip_bytes, ext_path)
            updated_version = local_manifest_version(ext_path)
            reload_msg = notify_extension_reload()
            return _update_result(
                update_available=True,
                local_version=updated_version,
                remote_version=remote_version,
                download_path=download_path,
                download_note=download_note,
                needs_reload=True,
                message=(
                    f"Extension updated: {local_version} -> {updated_version}. "
                    f"{reload_msg}"
                ),
            )
        except (OSError, urllib.error.URLError, ValueError, zipfile.BadZipFile) as exc:
            return _update_result(
                update_available=True,
                local_version=local_version,
                remote_version=remote_version,
                download_path=download_path,
                download_note=download_note,
                message=(
                    f"Extension update available: {local_version} -> {remote_version}. "
                    f"Apply failed: {exc}"
                ),
            )

    return _update_result(
        update_available=True,
        local_version=local_version,
        remote_version=remote_version,
        download_path=download_path,
        download_note=download_note,
        message=(
            f"Extension update available: {local_version} -> {remote_version}. "
            f"{download_note}"
        ),
    )
