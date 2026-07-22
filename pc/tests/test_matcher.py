"""Tests for slip order matching, auto-confirm thresholds, and audit trail.

API choices:
- ``match_order`` returns the single matching order dict, or ``None`` when there
  is no match, a duplicate ref, or an ambiguous (>1) match. Ambiguous matches
  never auto-confirm because ``should_auto_confirm`` requires a concrete match.
- ``should_auto_confirm`` returns False when match is None / ambiguous, parse
  failed, master switch off, confidence too low, or amount needs manual review.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipsync.audit import append_audit, default_audit_path
from clipsync.matcher import load_used_refs, match_order, save_used_refs, should_auto_confirm

CFG = {
    "auto_confirm": {
        "enabled": True,
        "min_ocr_confidence": 0.90,
        "require_manual_review": {"enabled": True, "amount_threshold": 5000.0},
    },
    "matching": {
        "require_account_last4_match": True,
        "prevent_duplicate_ref_number": True,
    },
}
OCR = {
    "amount": 350.0,
    "receiver_account_last4": "6789",
    "ref_number": "202607221432001",
    "ocr_confidence": 0.97,
}
ORDERS = [{"order_id": "1234", "amount": 350.0, "account_last4": "6789"}]


def test_match_exact():
    result = match_order(OCR, ORDERS, CFG, used_refs=set())
    assert result is not None
    assert result["order_id"] == "1234"


def test_no_match_wrong_amount():
    orders = [{"order_id": "1234", "amount": 351.0, "account_last4": "6789"}]
    assert match_order(OCR, orders, CFG, used_refs=set()) is None


def test_no_match_wrong_last4():
    orders = [{"order_id": "1234", "amount": 350.0, "account_last4": "0000"}]
    assert match_order(OCR, orders, CFG, used_refs=set()) is None


def test_last4_check_disabled():
    cfg = {
        **CFG,
        "matching": {
            "require_account_last4_match": False,
            "prevent_duplicate_ref_number": True,
        },
    }
    orders = [{"order_id": "99", "amount": 350.0, "account_last4": "0000"}]
    result = match_order(OCR, orders, cfg, used_refs=set())
    assert result is not None
    assert result["order_id"] == "99"


def test_duplicate_ref_rejected():
    used = {"202607221432001"}
    assert match_order(OCR, ORDERS, CFG, used_refs=used) is None


def test_auto_confirm_normal():
    matched = match_order(OCR, ORDERS, CFG, used_refs=set())
    assert should_auto_confirm(OCR, matched, CFG) is True


def test_over_threshold_needs_review():
    ocr = {**OCR, "amount": 6000.0}
    orders = [{"order_id": "1", "amount": 6000.0, "account_last4": "6789"}]
    matched = match_order(ocr, orders, CFG, used_refs=set())
    assert matched is not None
    assert should_auto_confirm(ocr, matched, CFG) is False


def test_threshold_disabled_confirms_high_amount():
    cfg = {
        "auto_confirm": {
            "enabled": True,
            "min_ocr_confidence": 0.90,
            "require_manual_review": {"enabled": False, "amount_threshold": 5000.0},
        },
        "matching": CFG["matching"],
    }
    ocr = {**OCR, "amount": 6000.0}
    orders = [{"order_id": "1", "amount": 6000.0, "account_last4": "6789"}]
    matched = match_order(ocr, orders, cfg, used_refs=set())
    assert should_auto_confirm(ocr, matched, cfg) is True


def test_low_confidence_blocked():
    ocr = {**OCR, "ocr_confidence": 0.5}
    matched = match_order(ocr, ORDERS, CFG, used_refs=set())
    assert matched is not None
    assert should_auto_confirm(ocr, matched, CFG) is False


def test_master_switch_off():
    cfg = {
        "auto_confirm": {
            "enabled": False,
            "min_ocr_confidence": 0.90,
            "require_manual_review": {"enabled": True, "amount_threshold": 5000.0},
        },
        "matching": CFG["matching"],
    }
    matched = match_order(OCR, ORDERS, cfg, used_refs=set())
    assert matched is not None
    assert should_auto_confirm(OCR, matched, cfg) is False


def test_parse_failed_never_confirms():
    ocr = {**OCR, "parse_failed": True}
    matched = match_order(ocr, ORDERS, CFG, used_refs=set())
    assert should_auto_confirm(ocr, matched, CFG) is False


def test_ambiguous_match_goes_to_review():
    """Two orders with same amount+last4 → match_order returns None; no auto-confirm."""
    orders = [
        {"order_id": "a", "amount": 350.0, "account_last4": "6789"},
        {"order_id": "b", "amount": 350.0, "account_last4": "6789"},
    ]
    matched = match_order(OCR, orders, CFG, used_refs=set())
    assert matched is None
    assert should_auto_confirm(OCR, matched, CFG) is False


def test_audit_append_jsonl(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    append_audit({"event": "match", "order_id": "1234"}, path=path)
    append_audit({"event": "confirm", "order_id": "1234"}, path=path)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "match"
    assert json.loads(lines[1])["order_id"] == "1234"


def test_default_audit_path_under_clipsync(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert default_audit_path() == tmp_path / "ClipSync" / "audit.jsonl"


def test_used_refs_persist_roundtrip(tmp_path: Path):
    path = tmp_path / "used_refs.json"
    refs = {"ref-a", "ref-b"}
    save_used_refs(refs, path)
    assert load_used_refs(path) == refs


def test_amount_epsilon_boundary_abs_diff_lt_0_005_matches():
    """abs(349.996 - 350) = 0.004 < 0.005 → match; abs(349.994 - 350) = 0.006 ≥ 0.005 → no match."""
    orders = [{"order_id": "1234", "amount": 350.0, "account_last4": "6789"}]
    ocr_inside = {**OCR, "amount": 349.996}
    ocr_outside = {**OCR, "amount": 349.994}

    assert match_order(ocr_inside, orders, CFG, used_refs=set()) is not None
    assert match_order(ocr_outside, orders, CFG, used_refs=set()) is None


def test_exact_amount_threshold_5000_needs_review_because_gte():
    """Manual review uses amount >= threshold: exactly 5000.0 with threshold 5000.0 needs review."""
    ocr = {**OCR, "amount": 5000.0}
    orders = [{"order_id": "1", "amount": 5000.0, "account_last4": "6789"}]
    matched = match_order(ocr, orders, CFG, used_refs=set())
    assert matched is not None
    assert should_auto_confirm(ocr, matched, CFG) is False


def test_missing_ocr_amount_no_match():
    ocr = {k: v for k, v in OCR.items() if k != "amount"}
    assert match_order(ocr, ORDERS, CFG, used_refs=set()) is None


def test_none_ocr_amount_no_match():
    ocr = {**OCR, "amount": None}
    assert match_order(ocr, ORDERS, CFG, used_refs=set()) is None


def test_missing_order_amount_no_match():
    orders = [{"order_id": "1234", "account_last4": "6789"}]
    assert match_order(OCR, orders, CFG, used_refs=set()) is None


def test_none_order_amount_no_match():
    orders = [{"order_id": "1234", "amount": None, "account_last4": "6789"}]
    assert match_order(OCR, orders, CFG, used_refs=set()) is None


def test_used_refs_none_raises_when_prevent_duplicate_enabled():
    with pytest.raises(ValueError, match="used_refs required when prevent_duplicate_ref_number"):
        match_order(OCR, ORDERS, CFG, used_refs=None)


def test_used_refs_none_ok_when_prevent_duplicate_disabled():
    cfg = {
        **CFG,
        "matching": {
            "require_account_last4_match": True,
            "prevent_duplicate_ref_number": False,
        },
    }
    result = match_order(OCR, ORDERS, cfg, used_refs=None)
    assert result is not None
    assert result["order_id"] == "1234"
