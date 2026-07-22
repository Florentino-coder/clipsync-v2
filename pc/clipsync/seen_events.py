"""Persisted slip event deduplication for PC-side delivery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from clipsync.config import user_data_dir

SeenEventCallback = Callable[[dict[str, Any]], None]
SendAckCallback = Callable[[str], None]


def default_seen_events_path() -> Path:
    return user_data_dir() / "seen_events.json"


class SeenEvents:
    """Load/save set of processed slip event IDs across restarts."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_seen_events_path()
        self._ids: set[str] = set()
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        if not self._path.exists():
            self._ids = set()
            return

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._ids = set()
            return

        raw = data.get("event_ids", [])
        if not isinstance(raw, list):
            self._ids = set()
            return

        self._ids = {item for item in raw if isinstance(item, str) and item}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"event_ids": sorted(self._ids)}
        self._path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def is_duplicate(self, event_id: str) -> bool:
        return event_id in self._ids

    def mark(self, event_id: str) -> None:
        if not event_id or event_id in self._ids:
            return
        self._ids.add(event_id)
        self.save()

    def process_slip_event(
        self,
        payload: dict[str, Any],
        *,
        on_new: SeenEventCallback,
        send_ack: SendAckCallback,
    ) -> bool:
        """Process a slip payload once; ack duplicates without reprocessing.

        Returns True when the event was a duplicate (acked only).
        """
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return False

        if self.is_duplicate(event_id):
            send_ack(event_id)
            return True

        on_new(payload)
        self.mark(event_id)
        send_ack(event_id)
        return False
