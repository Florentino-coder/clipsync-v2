"""Slip-to-order matching and auto-confirm gate logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableSet, Optional, Set

AMOUNT_EPSILON = 0.005

# Member/payee bank aliases — order row bank vs slip receiver bank.
_BANK_ALIASES: dict[str, tuple[str, ...]] = {
    "SCB": ("SCB", "ไทยพาณิชย์", "ธนาคารไทยพาณิชย์", "SIAM COMMERCIAL"),
    "KBANK": ("KBANK", "KPLUS", "K+", "กสิกร", "ธนาคารกสิกรไทย", "KASIKORN"),
    "BBL": ("BBL", "กรุงเทพ", "ธนาคารกรุงเทพ", "BANGKOK BANK"),
    "KTB": ("KTB", "กรุงไทย", "ธนาคารกรุงไทย", "KRUNGTHAI"),
    "GSB": ("GSB", "ออมสิน", "ธนาคารออมสิน", "MYMO"),
    "TTB": ("TTB", "ทหารไทย", "ธนชาต", "ธนาคารทหารไทยธนชาต"),
    "BAY": ("BAY", "กรุงศรี", "ธนาคารกรุงศรีอยุธยา"),
}


def _amount_present(value: Any) -> bool:
    """True when amount is a usable number (missing/None rejected; never default to 0)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str) and value.strip():
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


def _amounts_equal(a: Any, b: Any) -> bool:
    if not _amount_present(a) or not _amount_present(b):
        return False
    return abs(float(a) - float(b)) < AMOUNT_EPSILON


def _matching_cfg(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    return cfg.get("matching") or {}


def _auto_confirm_cfg(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    return cfg.get("auto_confirm") or {}


def _normalize_bank_text(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = text.replace(" ", "").replace("-", "")
    return text


def _bank_codes_for(value: Any) -> set[str]:
    """Map a free-form bank label/code to canonical codes (SCB, KBANK, …)."""
    text = _normalize_bank_text(value)
    if not text:
        return set()
    codes: set[str] = set()
    for code, aliases in _BANK_ALIASES.items():
        needles = (_normalize_bank_text(code),) + tuple(
            _normalize_bank_text(a) for a in aliases
        )
        if any(n and n in text for n in needles):
            codes.add(code)
        if text == code:
            codes.add(code)
    return codes


def _banks_match(ocr_bank: Any, order_bank: Any) -> bool:
    left = _bank_codes_for(ocr_bank)
    right = _bank_codes_for(order_bank)
    if not left or not right:
        return False
    return bool(left & right)


def _ocr_receiver_bank(ocr: Mapping[str, Any]) -> str:
    for key in (
        "receiver_bank",
        "receiver_bank_name_th",
        "receiver_bank_name",
        "member_bank",
        "member_bank_name",
    ):
        val = ocr.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _order_bank(order: Mapping[str, Any]) -> str:
    for key in ("bank", "bank_name", "bank_name_th", "member_bank", "member_bank_name"):
        val = order.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _candidate_matches(
    ocr: Mapping[str, Any],
    order: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> bool:
    # Missing/None amounts never match (do not coerce to 0.0).
    if "amount" not in ocr or "amount" not in order:
        return False
    if not _amounts_equal(ocr["amount"], order["amount"]):
        return False
    matching = _matching_cfg(cfg)
    if matching.get("require_account_last4_match", True):
        ocr_last4 = str(ocr.get("receiver_account_last4") or "")
        order_last4 = str(order.get("account_last4") or "")
        if ocr_last4 != order_last4:
            return False
    if matching.get("require_bank_match", True):
        # Payee/member bank only. Skip when OCR has no receiver bank so older
        # APKs without that field still match on amount+last4.
        ocr_bank = _ocr_receiver_bank(ocr)
        if ocr_bank:
            order_bank = _order_bank(order)
            if not order_bank or not _banks_match(ocr_bank, order_bank):
                return False
    return True


def match_order(
    ocr: Mapping[str, Any],
    orders: Iterable[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    used_refs: Optional[MutableSet[str] | Set[str]] = None,
) -> Optional[dict[str, Any]]:
    """Return the single matching order, or None if none / ambiguous / duplicate.

    Ambiguous (>1 matching order) returns None so callers cannot auto-confirm.

    When ``matching.prevent_duplicate_ref_number`` is True, ``used_refs`` must be
    provided (use an empty set if none are known yet). Passing ``used_refs=None``
    raises ``ValueError`` so duplicate checks cannot be silently skipped.
    """
    matching = _matching_cfg(cfg)
    if matching.get("prevent_duplicate_ref_number", True):
        if used_refs is None:
            raise ValueError("used_refs required when prevent_duplicate_ref_number")
        ref = ocr.get("ref_number")
        if ref is not None and str(ref) in used_refs:
            return None

    candidates = [dict(order) for order in orders if _candidate_matches(ocr, order, cfg)]
    if len(candidates) != 1:
        return None
    return candidates[0]


def should_auto_confirm(
    ocr: Mapping[str, Any],
    matched_order: Optional[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> bool:
    """Return True only when it is safe to auto-confirm the matched order."""
    if matched_order is None:
        return False
    if ocr.get("parse_failed"):
        return False

    ac = _auto_confirm_cfg(cfg)
    if not ac.get("enabled", False):
        return False

    confidence = float(ocr.get("ocr_confidence") or 0.0)
    min_conf = float(ac.get("min_ocr_confidence") or 0.0)
    if confidence < min_conf:
        return False

    review = ac.get("require_manual_review") or {}
    if review.get("enabled", False):
        amount = float(ocr.get("amount") or 0.0)
        threshold = float(review.get("amount_threshold") or 0.0)
        if amount >= threshold:
            return False

    return True


def load_used_refs(path: Path | str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {str(x) for x in raw}
    if isinstance(raw, dict) and "refs" in raw:
        return {str(x) for x in raw["refs"]}
    raise ValueError(f"unsupported used_refs format in {p}")


def save_used_refs(refs: Iterable[str], path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = sorted({str(r) for r in refs})
    p.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
