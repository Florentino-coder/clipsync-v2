"""Tests for slip order matching, auto-confirm thresholds, and audit trail.

API choices:
- ``match_order`` returns the single matching order dict, or ``None`` when there
  is no match, a duplicate ref, or an ambiguous (>1) match. Ambiguous matches
  never auto-confirm because ``should_auto_confirm`` requires a concrete match.
- ``should_auto_confirm`` returns False when match is None / ambiguous, amount
  missing with parse_failed, master switch off, confidence too low *without* a
  usable amount, or amount needs manual review. Soft parse_failed (null ref)
  and low-but-nonzero OCR confidence with a parsed amount still auto-confirm.
"""

from __future__ import annotations

import json
import sys
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


def test_low_confidence_soft_when_amount_present():
    """Low ML Kit averages no longer hard-block when amount already parsed."""
    ocr = {**OCR, "ocr_confidence": 0.5}
    matched = match_order(ocr, ORDERS, CFG, used_refs=set())
    assert matched is not None
    assert should_auto_confirm(ocr, matched, CFG) is True


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


def test_soft_parse_failed_with_amount_still_auto_confirms():
    """Live bug: mobile sets parse_failed when ref OCR misses (ref_number null).

    Audit for 1006/1669 had parse-able amount + accounts but every slip stayed
    pending_review while manual ยืนยันเอง worked — parse_failed was a hard gate.
    """
    ocr = {**OCR, "parse_failed": True, "ref_number": None}
    matched = match_order(ocr, ORDERS, CFG, used_refs=set())
    assert matched is not None
    assert should_auto_confirm(ocr, matched, CFG) is True


def test_parse_failed_without_amount_still_blocks():
    ocr = {**OCR, "parse_failed": True, "amount": None}
    matched = match_order(ocr, ORDERS, CFG, used_refs=set())
    assert matched is None
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
    monkeypatch.setattr(sys, "platform", "win32")
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


def test_same_amount_disambiguates_by_member_bank():
    """Two withdrawals at 100.00 — pick the one whose bank matches the slip payee."""
    cfg = {
        **CFG,
        "matching": {
            "require_account_last4_match": True,
            "require_bank_match": True,
            "prevent_duplicate_ref_number": True,
        },
    }
    ocr = {
        "amount": 100.0,
        "receiver_account_last4": "0860",
        "receiver_bank": "KTB",
        "ref_number": "REF-BANK-1",
        "ocr_confidence": 0.97,
    }
    orders = [
        {
            "order_id": "a",
            "amount": 100.0,
            "account_last4": "0860",
            "bank": "SCB",
        },
        {
            "order_id": "b",
            "amount": 100.0,
            "account_last4": "0860",
            "bank": "ธนาคารกรุงไทย",
        },
    ]
    matched = match_order(ocr, orders, cfg, used_refs=set())
    assert matched is not None
    assert matched["order_id"] == "b"


def test_same_amount_rejects_wrong_member_bank():
    cfg = {
        **CFG,
        "matching": {
            "require_account_last4_match": True,
            "require_bank_match": True,
            "prevent_duplicate_ref_number": True,
        },
    }
    ocr = {
        "amount": 100.0,
        "receiver_account_last4": "0860",
        "receiver_bank": "KTB",
        "ref_number": "REF-BANK-2",
        "ocr_confidence": 0.97,
    }
    orders = [
        {"order_id": "a", "amount": 100.0, "account_last4": "0860", "bank": "SCB"},
    ]
    assert match_order(ocr, orders, cfg, used_refs=set()) is None


def test_bank_match_skipped_when_ocr_has_no_receiver_bank():
    """Without a payee bank on the slip, don't fail closed on bank — last4 still applies."""
    cfg = {
        **CFG,
        "matching": {
            "require_account_last4_match": True,
            "require_bank_match": True,
            "prevent_duplicate_ref_number": True,
        },
    }
    ocr = {
        "amount": 100.0,
        "receiver_account_last4": "0860",
        "ref_number": "REF-BANK-3",
        "ocr_confidence": 0.97,
    }
    orders = [
        {"order_id": "a", "amount": 100.0, "account_last4": "0860", "bank": "SCB"},
    ]
    matched = match_order(ocr, orders, cfg, used_refs=set())
    assert matched is not None
    assert matched["order_id"] == "a"


def test_baac_and_kkp_bank_aliases_match():
    cfg = {
        **CFG,
        "matching": {
            "require_account_last4_match": True,
            "require_bank_match": True,
            "prevent_duplicate_ref_number": True,
        },
    }
    ocr_baac = {
        "amount": 100.0,
        "receiver_account_last4": "4567",
        "receiver_bank": "BAAC",
        "ref_number": "REF-BAAC",
        "ocr_confidence": 0.97,
    }
    orders_baac = [
        {
            "order_id": "baac1",
            "amount": 100.0,
            "account_last4": "4567",
            "bank": "ธกส",
        },
    ]
    matched = match_order(ocr_baac, orders_baac, cfg, used_refs=set())
    assert matched is not None
    assert matched["order_id"] == "baac1"

    ocr_kkp = {
        "amount": 200.0,
        "receiver_account_last4": "8899",
        "receiver_bank": "เกียรตินาคิน",
        "ref_number": "REF-KKP",
        "ocr_confidence": 0.97,
    }
    orders_kkp = [
        {
            "order_id": "kkp1",
            "amount": 200.0,
            "account_last4": "8899",
            "bank": "KKP",
        },
    ]
    matched_kkp = match_order(ocr_kkp, orders_kkp, cfg, used_refs=set())
    assert matched_kkp is not None
    assert matched_kkp["order_id"] == "kkp1"


def test_unknown_raw_bank_labels_still_match():
    """When neither side maps to a known code, equal Thai labels can still match."""
    cfg = {
        **CFG,
        "matching": {
            "require_account_last4_match": True,
            "require_bank_match": True,
            "prevent_duplicate_ref_number": True,
        },
    }
    ocr = {
        "amount": 50.0,
        "receiver_account_last4": "1122",
        "receiver_bank": "ธนาคารทดสอบไม่รู้จัก",
        "ref_number": "REF-RAW",
        "ocr_confidence": 0.97,
    }
    orders = [
        {
            "order_id": "raw1",
            "amount": 50.0,
            "account_last4": "1122",
            "bank": "ธนาคารทดสอบไม่รู้จัก",
        },
    ]
    matched = match_order(ocr, orders, cfg, used_refs=set())
    assert matched is not None
    assert matched["order_id"] == "raw1"


def test_dom_scrape_amount_with_commas_matches():
    """Jinbao scrape yields '1,097.00' — must still match float OCR amounts."""
    orders = [{"order_id": "row-1", "amount": "1,097.00", "account_last4": ""}]
    ocr = {
        "amount": 1097.0,
        "receiver_account_last4": "8474",
        "ocr_confidence": 0.95,
        "ref_number": "R1",
    }
    matched = match_order(ocr, orders, CFG, used_refs=set())
    assert matched is not None
    assert matched["order_id"] == "row-1"


def test_empty_order_last4_does_not_block_unique_amount():
    """DOM scrape without account digits → amount-only match when unique."""
    orders = [{"order_id": "0971572720", "amount": "1097.00"}]
    ocr = {
        "amount": 1097.0,
        "receiver_account_last4": "8474",
        "ocr_confidence": 0.95,
    }
    matched = match_order(ocr, orders, CFG, used_refs=set())
    assert matched is not None
    assert matched["order_id"] == "0971572720"


def test_missing_ocr_confidence_still_auto_confirms_when_enabled():
    matched = match_order(OCR, ORDERS, CFG, used_refs=set())
    ocr = {k: v for k, v in OCR.items() if k != "ocr_confidence"}
    assert should_auto_confirm(ocr, matched, CFG) is True


def test_zero_ocr_confidence_treated_as_unknown_still_auto_confirms():
    """ML Kit often reports no element.confidence → mobile sends 0.0 (unknown).

    Treating 0.0 as a real score below min_ocr_confidence (0.90) blocked every
    auto-confirm while manual ยืนยันเอง still worked (no confidence gate).
    """
    matched = match_order(OCR, ORDERS, CFG, used_refs=set())
    ocr = {**OCR, "ocr_confidence": 0.0}
    assert should_auto_confirm(ocr, matched, CFG) is True


def test_low_but_nonzero_ocr_confidence_soft_when_amount_parsed():
    """Live 14:18:42 slip 1900 → รอตรวจ (low_ocr_confidence) despite From…7476.

    Audit.jsonl only stores reason (not the float), so any value in (0, min)
    reproduces the gate. ML Kit Latin on Thai SCB slips often averages ~0.3–0.8
    even when amount (+ payer last4) parsed correctly; manual ยืนยันเอง worked.
    """
    from clipsync.matcher import auto_confirm_block_reason

    matched = match_order(OCR, ORDERS, CFG, used_refs=set())
    ocr = {
        **OCR,
        "sender_account_last4": "7476",
        "ocr_confidence": 0.5,
        "parse_failed": True,
        "ref_number": None,
    }
    assert should_auto_confirm(ocr, matched, CFG) is True
    assert auto_confirm_block_reason(ocr, matched, CFG) is None


def test_live_1900_payload_shape_must_auto_confirm():
    """Exact live shape from activity log 14:18:42 / audit event f9d049bc…"""
    from clipsync.matcher import auto_confirm_block_reason, resolve_auto_match

    live_1900 = {
        "event_id": "f9d049bc-e1ab-46e3-b3d2-97ec9a27b06b",
        "amount": 1900.0,
        "ref_number": None,
        "sender_account_last4": "7476",
        "receiver_account_last4": "1588",
        # audit omits the float; reason=low_ocr_confidence ⇒ 0 < conf < 0.9
        "ocr_confidence": 0.42,
        "parse_failed": True,
        "bank": "SCB",
    }
    matched = resolve_auto_match(live_1900, [], CFG, used_refs=set())
    assert matched is not None
    assert matched.get("match_mode") == "amount_only"
    assert should_auto_confirm(live_1900, matched, CFG) is True
    assert auto_confirm_block_reason(live_1900, matched, CFG) is None


def test_live_1006_matcher_allows_amount_so_orchestrator_can_require_last4():
    """Live 14:17:58 slip 1006 From '-' — matcher must not eat the last4 gate.

    If confidence hard-blocks first, audit shows low_ocr_confidence forever and
    never missing_sender_last4. Soft-skip confidence when amount is present.
    """
    from clipsync.matcher import auto_confirm_block_reason, resolve_auto_match

    live_1006 = {
        "event_id": "5e29a94f-7f74-486b-91df-ccae602fc1f9",
        "amount": 1006.0,
        "ref_number": None,
        "receiver_account_last4": "1588",
        "ocr_confidence": 0.42,
        "parse_failed": True,
        "bank": "SCB",
    }
    matched = resolve_auto_match(live_1006, [], CFG, used_refs=set())
    assert matched is not None
    assert should_auto_confirm(live_1006, matched, CFG) is True
    assert auto_confirm_block_reason(live_1006, matched, CFG) is None


def test_low_ocr_confidence_still_blocks_without_amount():
    from clipsync.matcher import auto_confirm_block_reason

    matched = {"order_id": "", "amount": None, "match_mode": "amount_only"}
    ocr = {
        "amount": None,
        "ocr_confidence": 0.5,
        "sender_account_last4": "7476",
        "parse_failed": False,
    }
    assert should_auto_confirm(ocr, matched, CFG) is False
    assert auto_confirm_block_reason(ocr, matched, CFG) == "low_ocr_confidence"


def test_auto_confirm_block_reason_disabled():
    from clipsync.matcher import auto_confirm_block_reason

    matched = match_order(OCR, ORDERS, CFG, used_refs=set())
    cfg = {**CFG, "auto_confirm": {**CFG["auto_confirm"], "enabled": False}}
    assert auto_confirm_block_reason(OCR, matched, cfg) == "auto_confirm_disabled"


def test_resolve_sender_account_last4_from_mask():
    from clipsync.slip_ocr import resolve_sender_account_last4

    assert (
        resolve_sender_account_last4({"sender_account_masked": "xxxxxx7476"}) == "7476"
    )
    assert resolve_sender_account_last4({"sender_account_last4": "1234"}) == "1234"
    assert resolve_sender_account_last4({}) is None


def test_resolve_auto_match_amount_only_when_scrape_empty():
    from clipsync.matcher import resolve_auto_match

    matched = resolve_auto_match(OCR, [], CFG, used_refs=set())
    assert matched is not None
    assert matched.get("match_mode") == "amount_only"
    assert matched["amount"] == OCR["amount"]
    assert should_auto_confirm(OCR, matched, CFG) is True


def test_resolve_auto_match_does_not_override_last4_conflict():
    from clipsync.matcher import resolve_auto_match

    orders = [{"order_id": "x", "amount": 350.0, "account_last4": "0000"}]
    assert resolve_auto_match(OCR, orders, CFG, used_refs=set()) is None


def test_resolve_auto_match_ambiguous_same_amount_stays_none():
    from clipsync.matcher import resolve_auto_match

    orders = [
        {"order_id": "a", "amount": 350.0},
        {"order_id": "b", "amount": 350.0},
    ]
    assert resolve_auto_match(OCR, orders, CFG, used_refs=set()) is None


def test_is_reliable_order_id_rejects_page_index():
    from clipsync.matcher import is_reliable_order_id

    assert is_reliable_order_id("1") is False
    assert is_reliable_order_id("12") is False
    assert is_reliable_order_id("099") is False
    assert is_reliable_order_id("0971572720") is True
    assert is_reliable_order_id("acct:0722488474") is True
    assert is_reliable_order_id("") is False


def test_rejects_ref_fragment_ocr_amount():
    """7268 from ref 202607268… must not match even when a same-amount order exists."""
    ref = "202607268XRZLCrFvm0JLRag6"
    ocr_bad = {
        "amount": 7268,
        "ref_number": ref,
        "receiver_account_last4": "1756",
        "ocr_confidence": 0.95,
    }
    trap_orders = [{"order_id": "trap", "amount": 7268.0, "account_last4": "1756"}]
    assert match_order(ocr_bad, trap_orders, CFG, used_refs=set()) is None

    real_orders = [{"order_id": "w-3727", "amount": 3727.00, "account_last4": "1756"}]
    assert match_order(ocr_bad, real_orders, CFG, used_refs=set()) is None


def test_correct_amount_still_matches_with_alphanumeric_ref():
    ref = "202607268XRZLCrFvm0JLRag6"
    ocr_good = {
        "amount": 3727.00,
        "ref_number": ref,
        "receiver_account_last4": "1756",
        "ocr_confidence": 0.95,
    }
    orders = [{"order_id": "w-3727", "amount": 3727.00, "account_last4": "1756"}]
    result = match_order(ocr_good, orders, CFG, used_refs=set())
    assert result is not None
    assert result["order_id"] == "w-3727"


def test_float_amount_cents_not_treated_as_ref_fragment():
    """str(100.0) digits must not become '1000' and false-reject vs ref."""
    ref = "202607261000XRZLCrFvm0JLRag6"
    ocr = {
        "amount": 100.0,
        "ref_number": ref,
        "receiver_account_last4": "1756",
        "ocr_confidence": 0.95,
    }
    orders = [{"order_id": "w-100", "amount": 100.0, "account_last4": "1756"}]
    result = match_order(ocr, orders, CFG, used_refs=set())
    assert result is not None
    assert result["order_id"] == "w-100"
