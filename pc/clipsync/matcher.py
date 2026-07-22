"""Slip-to-order matching and auto-confirm gate logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableSet, Optional, Set

AMOUNT_EPSILON = 0.005


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
