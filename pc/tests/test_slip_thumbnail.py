"""PC slip view prefers relay thumbnail when USB image is unavailable."""

from __future__ import annotations

import base64

from clipsync.slip_image import decode_thumbnail_jpeg, ui_event_with_thumbnail


def test_ui_event_with_thumbnail_copies_field():
    payload = {"event_id": "e1", "amount": 10.0}
    out = ui_event_with_thumbnail(
        payload,
        {"decision": "pending_review", "order_id": None},
        thumbnail_jpeg_b64="abc",
        transport="relay",
    )
    assert out["event_id"] == "e1"
    assert out["thumbnail_jpeg_b64"] == "abc"
    assert out["transport"] == "relay"
    assert out["decision"] == "pending_review"


def test_decode_thumbnail_jpeg_roundtrip():
    # Minimal valid JPEG (1x1) — may fail decode with PIL; use PNG bytes mislabeled?
    # Generate with Pillow if available.
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (32, 40), color=(255, 0, 0)).save(buf, format="JPEG", quality=50)
    raw = buf.getvalue()
    b64 = base64.b64encode(raw).decode("ascii")
    decoded = decode_thumbnail_jpeg(b64)
    assert decoded is not None
    assert decoded[:2] == b"\xff\xd8"
