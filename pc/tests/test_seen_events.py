"""Tests for persisted slip event deduplication."""

from __future__ import annotations

import json
from pathlib import Path

from clipsync.seen_events import SeenEvents, default_seen_events_path


def test_is_duplicate_false_for_new_id(tmp_path: Path):
    path = tmp_path / "seen_events.json"
    seen = SeenEvents(path=path)

    assert seen.is_duplicate("evt-new") is False


def test_mark_persists_event_id(tmp_path: Path):
    path = tmp_path / "seen_events.json"
    seen = SeenEvents(path=path)

    seen.mark("evt-1")
    assert path.exists()

    reloaded = SeenEvents(path=path)
    assert reloaded.is_duplicate("evt-1") is True
    assert reloaded.is_duplicate("evt-2") is False


def test_mark_idempotent(tmp_path: Path):
    path = tmp_path / "seen_events.json"
    seen = SeenEvents(path=path)

    seen.mark("evt-dup")
    seen.mark("evt-dup")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["event_ids"].count("evt-dup") == 1


def test_default_path_under_appdata(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert default_seen_events_path() == tmp_path / "ClipSync" / "seen_events.json"


def test_process_duplicate_acks_without_callback(tmp_path: Path):
    path = tmp_path / "seen_events.json"
    seen = SeenEvents(path=path)
    seen.mark("evt-seen")

    acked: list[str] = []
    processed: list[dict] = []

    duplicate = seen.process_slip_event(
        {"event_id": "evt-seen", "amount": 100.0},
        on_new=lambda payload: processed.append(payload),
        send_ack=lambda event_id: acked.append(event_id),
    )

    assert duplicate is True
    assert acked == ["evt-seen"]
    assert processed == []


def test_process_new_event_marks_and_acks(tmp_path: Path):
    path = tmp_path / "seen_events.json"
    seen = SeenEvents(path=path)
    payload = {"event_id": "evt-new", "amount": 250.0}

    acked: list[str] = []
    processed: list[dict] = []

    duplicate = seen.process_slip_event(
        payload,
        on_new=lambda p: processed.append(p),
        send_ack=lambda event_id: acked.append(event_id),
    )

    assert duplicate is False
    assert processed == [payload]
    assert acked == ["evt-new"]
    assert seen.is_duplicate("evt-new") is True

    reloaded = SeenEvents(path=path)
    assert reloaded.is_duplicate("evt-new") is True
