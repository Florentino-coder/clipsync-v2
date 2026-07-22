"""Append-only JSONL audit trail for slip auto-confirm decisions."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


def default_audit_path() -> Path:
    """``%APPDATA%\\ClipSync\\audit.jsonl`` on Windows; platform-appropriate elsewhere."""
    if sys.platform.startswith("win"):
        root = os.getenv("APPDATA") or str(Path.home())
        return Path(root) / "ClipSync" / "audit.jsonl"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ClipSync" / "audit.jsonl"
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "clipsync" / "audit.jsonl"


def append_audit(event: Mapping[str, Any], path: Optional[Path | str] = None) -> Path:
    """Append one JSON object as a line. Path is injectable for tests."""
    audit_path = Path(path) if path is not None else default_audit_path()
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(event)
    if "ts" not in record:
        record["ts"] = datetime.now(timezone.utc).isoformat()
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return audit_path
