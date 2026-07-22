"""Unit tests for audit.jsonl load + day/type filters (no Tk)."""

from __future__ import annotations

from pathlib import Path

from clipsync.ui.audit_viewer import filter_audit_records, load_audit_records


def test_load_audit_records_skips_blank_and_bad_lines(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"ts":"2026-07-21T10:00:00+00:00","decision":"auto_confirmed","confirmed_by":"system"}',
                "",
                "not-json",
                '{"ts":"2026-07-22T11:00:00+00:00","decision":"overridden","confirmed_by":"admin_manual"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = load_audit_records(path)
    assert len(rows) == 2
    assert rows[0]["confirmed_by"] == "system"
    assert rows[1]["confirmed_by"] == "admin_manual"


def test_load_audit_records_missing_file_returns_empty(tmp_path: Path):
    assert load_audit_records(tmp_path / "missing.jsonl") == []


def test_filter_by_day():
    records = [
        {"ts": "2026-07-21T23:00:00+00:00", "confirmed_by": "system"},
        {"ts": "2026-07-22T01:00:00+00:00", "confirmed_by": "system"},
        {"ts": "2026-07-22T15:00:00+00:00", "confirmed_by": "admin_manual"},
    ]
    filtered = filter_audit_records(records, day="2026-07-22")
    assert len(filtered) == 2
    assert all(r["ts"].startswith("2026-07-22") for r in filtered)


def test_filter_by_confirmed_by_type():
    records = [
        {"ts": "2026-07-22T01:00:00+00:00", "confirmed_by": "system"},
        {"ts": "2026-07-22T02:00:00+00:00", "confirmed_by": "admin_manual"},
        {"ts": "2026-07-22T03:00:00+00:00", "confirmed_by": None},
        {"ts": "2026-07-22T04:00:00+00:00", "confirmed_by": "admin_manual"},
    ]
    only_system = filter_audit_records(records, confirmed_by="system")
    assert len(only_system) == 1
    only_admin = filter_audit_records(records, confirmed_by="admin_manual")
    assert len(only_admin) == 2


def test_filter_day_and_type_combined():
    records = [
        {"ts": "2026-07-21T01:00:00+00:00", "confirmed_by": "admin_manual"},
        {"ts": "2026-07-22T01:00:00+00:00", "confirmed_by": "admin_manual"},
        {"ts": "2026-07-22T02:00:00+00:00", "confirmed_by": "system"},
    ]
    filtered = filter_audit_records(
        records, day="2026-07-22", confirmed_by="admin_manual"
    )
    assert filtered == [records[1]]
