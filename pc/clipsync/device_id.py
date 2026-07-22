"""Stable hardware-bound device identifier for license binding."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from typing import Optional


def _windows_machine_guid() -> Optional[str]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except OSError:
        pass

    try:
        completed = subprocess.run(
            ["wmic", "csproduct", "get", "UUID"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        lines = [ln.strip() for ln in completed.stdout.splitlines() if ln.strip()]
        for line in lines:
            if line.upper() == "UUID":
                continue
            if line:
                return line
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _hardware_uuid() -> str:
    if sys.platform == "win32":
        guid = _windows_machine_guid()
        if guid:
            return guid
    # Fallback for non-Windows / missing MachineGuid (tests should inject).
    node = platform.node() or "unknown-host"
    return f"fallback:{node}"


def get_device_id(hardware_uuid: Optional[str] = None) -> str:
    """Return sha256(hardware uuid) hex truncated to 16 characters.

    Pass ``hardware_uuid`` to inject a value in tests without reading the OS.
    """
    raw = hardware_uuid if hardware_uuid is not None else _hardware_uuid()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
