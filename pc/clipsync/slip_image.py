"""Helpers for slip preview images (relay thumbnails)."""

from __future__ import annotations

import base64
from typing import Any, Mapping, Optional


def ui_event_with_thumbnail(
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    thumbnail_jpeg_b64: Optional[str] = None,
    transport: Optional[str] = None,
) -> dict[str, Any]:
    """Merge orchestrator result + optional thumbnail for the Slip debug panel."""
    out: dict[str, Any] = {
        **dict(payload),
        "decision": result.get("decision"),
        "order_id": result.get("order_id"),
        "transport": transport,
    }
    if isinstance(thumbnail_jpeg_b64, str) and thumbnail_jpeg_b64:
        out["thumbnail_jpeg_b64"] = thumbnail_jpeg_b64
    return out


def decode_thumbnail_jpeg(thumbnail_jpeg_b64: str) -> Optional[bytes]:
    """Decode base64 JPEG bytes; return None on bad input."""
    try:
        raw = base64.b64decode(thumbnail_jpeg_b64, validate=False)
    except Exception:
        return None
    if len(raw) < 24:
        return None
    return raw
