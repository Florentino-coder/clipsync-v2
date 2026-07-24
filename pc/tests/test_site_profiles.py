"""Tests for Site Profile schema load / validate / store / push message."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipsync.site_profiles import (
    build_site_profiles_message,
    default_profile,
    load_profiles,
    store_profiles,
    validate_profile,
)


def _valid_profile(**overrides):
    base = {
        "profile_id": "customer_a_v1",
        "domain_patterns": ["https://admin.customer-a.example/*"],
        "order_page_url_hint": "/orders",
        "row_selector_hints": ["tr", "[class*='order']"],
        "confirm_keywords": ["ยืนยัน", "confirm", "อนุมัติ", "approve", "สำเร็จ"],
        "already_confirmed_indicators": ["ยืนยันแล้ว", "confirmed", "สำเร็จแล้ว", "approved"],
        "logout_indicators": ["form[action*='login']", "input[type='password']"],
        "order_list_canary_selector": "table, [class*='order-list'], [class*='order']",
        "uses_iframe": False,
        "dry_run": True,
        "post_click_verify_timeout_ms": 15000,
        "click_wait_max_ms": 30000,
    }
    base.update(overrides)
    return base


def test_default_profile_has_dry_run_true():
    profile = default_profile(profile_id="new_customer")
    assert profile["dry_run"] is True
    assert profile["profile_id"] == "new_customer"
    assert isinstance(profile["domain_patterns"], list)
    assert isinstance(profile["confirm_keywords"], list)


def test_validate_accepts_full_schema():
    validated = validate_profile(_valid_profile())
    assert validated["profile_id"] == "customer_a_v1"
    assert validated["dry_run"] is True
    assert validated["uses_iframe"] is False
    assert validated["post_click_verify_timeout_ms"] == 15000
    assert validated["click_wait_max_ms"] == 30000


def test_validate_defaults_dry_run_when_missing():
    raw = _valid_profile()
    del raw["dry_run"]
    validated = validate_profile(raw)
    assert validated["dry_run"] is True


def test_validate_rejects_missing_profile_id():
    raw = _valid_profile()
    del raw["profile_id"]
    with pytest.raises(ValueError, match="profile_id"):
        validate_profile(raw)


def test_validate_rejects_empty_domain_patterns():
    with pytest.raises(ValueError, match="domain_patterns"):
        validate_profile(_valid_profile(domain_patterns=[]))


def test_validate_rejects_wrong_types():
    with pytest.raises(ValueError, match="uses_iframe"):
        validate_profile(_valid_profile(uses_iframe="no"))
    with pytest.raises(ValueError, match="confirm_keywords"):
        validate_profile(_valid_profile(confirm_keywords="confirm"))
    with pytest.raises(ValueError, match="post_click_verify_timeout_ms"):
        validate_profile(_valid_profile(post_click_verify_timeout_ms="slow"))


def test_load_profiles_from_directory(tmp_path: Path):
    (tmp_path / "a.json").write_text(
        json.dumps(_valid_profile(profile_id="a")), encoding="utf-8"
    )
    (tmp_path / "b.json").write_text(
        json.dumps(_valid_profile(profile_id="b", dry_run=False)), encoding="utf-8"
    )
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    profiles = load_profiles(tmp_path)
    ids = sorted(p["profile_id"] for p in profiles)
    assert ids == ["a", "b"]
    by_id = {p["profile_id"]: p for p in profiles}
    assert by_id["a"]["dry_run"] is True
    assert by_id["b"]["dry_run"] is False


def test_load_profiles_from_single_file(tmp_path: Path):
    path = tmp_path / "example.json"
    path.write_text(json.dumps(_valid_profile()), encoding="utf-8")
    profiles = load_profiles(path)
    assert len(profiles) == 1
    assert profiles[0]["profile_id"] == "customer_a_v1"


def test_load_profiles_rejects_invalid_json_file(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_profiles(bad)


def test_store_profiles_writes_validated_json(tmp_path: Path):
    out = tmp_path / "stored"
    profiles = [
        _valid_profile(profile_id="one"),
        _valid_profile(profile_id="two", dry_run=False),
    ]
    store_profiles(out, profiles)

    written = sorted(out.glob("*.json"))
    assert len(written) == 2
    loaded = load_profiles(out)
    assert {p["profile_id"] for p in loaded} == {"one", "two"}


def test_build_site_profiles_message():
    profiles = [validate_profile(_valid_profile())]
    msg = build_site_profiles_message(profiles)
    assert msg["type"] == "site_profiles"
    assert msg["profiles"] == profiles
    assert msg["profiles"][0]["dry_run"] is True


def test_example_extension_profile_loads():
    """Synthetic example until Task 4.0 partner HAR/HTML fixture is available."""
    example = (
        Path(__file__).resolve().parents[1]
        / "chrome-extension"
        / "profiles"
        / "example.json"
    )
    profiles = load_profiles(example)
    assert len(profiles) == 1
    assert profiles[0]["profile_id"] == "example_synthetic_v1"
    assert profiles[0]["dry_run"] is True
    assert profiles[0]["domain_patterns"]
    assert profiles[0]["api"]["enabled"] is True
    assert "withdrawal-approve-search" in profiles[0]["api"]["list_pending"]["url_template"]
    assert "TODO" in profiles[0]["api"]["approve"]["url_template"]
    assert profiles[0]["close_job_workflow"][0]["action"] == "click"


def test_validate_optional_api_block():
    validated = validate_profile(
        _valid_profile(
            api={
                "enabled": True,
                "list_pending": {
                    "method": "GET",
                    "url_template": "/bo/withdrawal-approve-search?start_date={today}",
                    "fields_map": {"order_id": "id", "amount": "amount"},
                },
                "approve": {
                    "method": "POST",
                    "url_template": "TODO(HAR): partner approve endpoint unknown",
                    "payload_template": {},
                },
            }
        )
    )
    assert validated["api"]["enabled"] is True
    assert validated["api"]["approve"]["url_template"].startswith("TODO")


def test_validate_optional_close_job_workflow():
    validated = validate_profile(
        _valid_profile(
            close_job_workflow=[
                {"action": "click", "match_text": "ยืนยัน"},
                {"action": "wait_for", "timeout_ms": 1000},
            ]
        )
    )
    assert len(validated["close_job_workflow"]) == 2


def test_validate_accepts_scroll_and_check_actions():
    validated = validate_profile(
        _valid_profile(
            close_job_workflow=[
                {"action": "scroll_into_view", "scope": "popup", "match_text": "โอน"},
                {"action": "check", "scope": "popup", "match_text": "โอนเงินเรียบร้อยแล้ว"},
            ]
        )
    )
    assert [s["action"] for s in validated["close_job_workflow"]] == [
        "scroll_into_view",
        "check",
    ]


def test_jinbao356_profile_validates():
    path = (
        Path(__file__).resolve().parents[1]
        / "chrome-extension"
        / "profiles"
        / "jinbao356_v1.json"
    )
    profiles = load_profiles(path)
    assert profiles[0]["profile_id"] == "jinbao356_v1"
    actions = {s["action"] for s in profiles[0]["close_job_workflow"]}
    assert "scroll_into_view" in actions
    assert "check" in actions


def test_validate_rejects_unknown_workflow_action():
    with pytest.raises(ValueError, match="action"):
        validate_profile(
            _valid_profile(close_job_workflow=[{"action": "explode"}])
        )
