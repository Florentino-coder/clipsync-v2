"""Slip-to-order matching and auto-confirm gate logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableSet, Optional, Set

AMOUNT_EPSILON = 0.005


def is_reliable_order_id(value: Any) -> bool:
    """True when a scraped/matched id is safe to use as the Jinbao row match key.

    DOM scrapes often grab page indices / tab numbers like ``\"1\"`` which then
    become ``Admin confirm sent (1)`` and break account select. Prefer amount.
    """
    text = str(value or "").strip()
    if not text:
        return False
    if len(text) < 4:
        return False
    # Pure short integers (1, 12, 99) are almost never real order ids.
    if text.isdigit() and len(text) <= 3:
        return False
    return True


# Member/payee bank aliases — order row bank vs slip receiver bank.
_BANK_ALIASES: dict[str, tuple[str, ...]] = {
    "SCB": ("SCB", "ไทยพาณิชย์", "ธนาคารไทยพาณิชย์", "SIAM COMMERCIAL"),
    "KBANK": ("KBANK", "KPLUS", "K+", "กสิกร", "ธนาคารกสิกรไทย", "KASIKORN"),
    "BBL": ("BBL", "กรุงเทพ", "ธนาคารกรุงเทพ", "BANGKOK BANK"),
    "KTB": ("KTB", "กรุงไทย", "ธนาคารกรุงไทย", "KRUNGTHAI"),
    "GSB": ("GSB", "ออมสิน", "ธนาคารออมสิน", "MYMO"),
    "TTB": ("TTB", "ทหารไทย", "ธนชาต", "ธนาคารทหารไทยธนชาต"),
    "BAY": ("BAY", "กรุงศรี", "ธนาคารกรุงศรีอยุธยา"),
    "BAAC": (
        "BAAC",
        "ธกส",
        "ธ.ก.ส.",
        "ธ.ก.ส",
        "เพื่อการเกษตร",
        "ธนาคารเพื่อการเกษตรและสหกรณ์การเกษตร",
    ),
    "KKP": (
        "KKP",
        "เกียรตินาคิน",
        "เกียรตินาคินภัทร",
        "KIATNAKIN",
        "ธนาคารเกียรตินาคินภัทร",
    ),
}


def _parse_amount(value: Any) -> Optional[float]:
    """Parse amount from int/float/str. Accepts thousands commas (\"1,097.00\")."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        cleaned = value.strip().replace(",", "").replace(" ", "")
        # Strip currency suffixes like THB / บาท
        for suffix in ("THB", "บาท", "฿"):
            if cleaned.upper().endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
        cleaned = cleaned.strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _amount_present(value: Any) -> bool:
    """True when amount is a usable number (missing/None rejected; never default to 0)."""
    return _parse_amount(value) is not None


def _amounts_equal(a: Any, b: Any) -> bool:
    left = _parse_amount(a)
    right = _parse_amount(b)
    if left is None or right is None:
        return False
    return abs(left - right) < AMOUNT_EPSILON


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
    if left and right:
        return bool(left & right)
    # Unknown bank labels (not in whitelist): still match on raw normalized text.
    if left or right:
        return False
    a = _normalize_bank_text(ocr_bank)
    b = _normalize_bank_text(order_bank)
    if not a or not b:
        return False
    return a == b or a in b or b in a


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


def _ocr_amount_looks_like_ref_fragment(ocr: Mapping[str, Any]) -> bool:
    """True when OCR amount digits likely came from รหัสอ้างอิง, not the amount label."""
    ref = ocr.get("ref_number")
    if ref is None or not str(ref).strip():
        return False
    ref_text = str(ref).strip()
    amount = ocr.get("amount")
    if amount is None:
        return False
    # Baht integer digits only — avoid str(100.0) → "1000" false positives.
    raw = str(amount).strip().replace(",", "")
    int_part = raw.split(".", 1)[0]
    digits_only = "".join(ch for ch in int_part if ch.isdigit())
    return len(digits_only) >= 4 and digits_only in ref_text


def _candidate_matches(
    ocr: Mapping[str, Any],
    order: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> bool:
    if _ocr_amount_looks_like_ref_fragment(ocr):
        return False
    # Missing/None amounts never match (do not coerce to 0.0).
    if "amount" not in ocr or "amount" not in order:
        return False
    if not _amounts_equal(ocr["amount"], order["amount"]):
        return False
    matching = _matching_cfg(cfg)
    if matching.get("require_account_last4_match", True):
        ocr_last4 = str(ocr.get("receiver_account_last4") or "").replace(" ", "")[-4:]
        order_last4 = str(order.get("account_last4") or "").replace(" ", "")[-4:]
        # DOM scrapes often omit last4; only enforce when the order carries one.
        if order_last4 and ocr_last4 != order_last4:
            return False
        if order_last4 and not ocr_last4:
            return False
    if matching.get("require_bank_match", True):
        # Payee/member bank only. Skip when OCR has no receiver bank so older
        # APKs without that field still match on amount+last4. Also skip when
        # the scraped order has no bank (amount-only scrape).
        ocr_bank = _ocr_receiver_bank(ocr)
        order_bank = _order_bank(order)
        if ocr_bank and order_bank and not _banks_match(ocr_bank, order_bank):
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


def resolve_auto_match(
    ocr: Mapping[str, Any],
    orders: Iterable[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    used_refs: Optional[MutableSet[str] | Set[str]] = None,
) -> Optional[dict[str, Any]]:
    """Match a pending order, or fall back to amount-only when scrape is empty/misses.

    Manual confirm already pushes by amount+slip without a scraped order_id. Auto
    must do the same when the extension has not published pending_orders yet (or
    Jinbao DOM scrape returned nothing) — otherwise every slip stays pending_review
    forever even with Auto-confirm ticked.
    """
    matched = match_order(ocr, orders, cfg, used_refs=used_refs)
    if matched is not None:
        return matched

    order_list = [dict(o) for o in orders]
    amount_hits = [
        o for o in order_list if _amounts_equal(ocr.get("amount"), o.get("amount"))
    ]
    # Ambiguous same-amount rows on the scrape list → leave for manual review.
    if len(amount_hits) > 1:
        return None
    # Scrape listed this amount but last4/bank blocked the match → do not override.
    if len(amount_hits) == 1:
        return None
    # No scrape list, or scrape missed this amount → amount-only (extension finds row).
    if _parse_amount(ocr.get("amount")) is None:
        return None
    return {
        "order_id": "",
        "amount": ocr.get("amount"),
        "account_last4": "",
        "bank": "",
        "match_mode": "amount_only",
    }


def auto_confirm_block_reason(
    ocr: Mapping[str, Any],
    matched_order: Optional[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> Optional[str]:
    """Return why auto-confirm must not run, or None when it is safe."""
    if matched_order is None:
        return "no_match"
    # Soft parse_failed (e.g. mobile ref_invalid when OCR misses the long ref)
    # must NOT block auto-confirm when amount matched — live audits showed
    # ref_number=null + parse_failed on every slip while manual confirm worked.
    if ocr.get("parse_failed") and not _amount_present(ocr.get("amount")):
        return "parse_failed"

    ac = _auto_confirm_cfg(cfg)
    if not ac.get("enabled", False):
        return "auto_confirm_disabled"

    confidence_raw = ocr.get("ocr_confidence")
    if confidence_raw is not None and str(confidence_raw).strip() != "":
        confidence = float(confidence_raw)
        # 0.0 means "unknown" from mobile (ML Kit often omits element.confidence).
        # Do not treat it as a real score below min_ocr_confidence — that blocked
        # every auto-confirm while manual ยืนยันเอง still worked.
        if confidence > 0.0:
            min_conf = float(ac.get("min_ocr_confidence") or 0.0)
            if confidence < min_conf:
                # Soft when amount already parsed. Live SCB slips (1006/1900 on
                # 2026-07-25) hit low_ocr_confidence with ML Kit Latin averages
                # in (0, 0.9) even though amount + From last4 were correct and
                # manual confirm succeeded. Hard gates remain: last4, match,
                # amount threshold.
                if not _amount_present(ocr.get("amount")):
                    return "low_ocr_confidence"
    # Missing / unknown (0.0) / soft-low-with-amount: allow when master switch on.


    review = ac.get("require_manual_review") or {}
    if review.get("enabled", False):
        amount = float(ocr.get("amount") or 0.0)
        threshold = float(review.get("amount_threshold") or 0.0)
        if amount >= threshold:
            return "over_amount_threshold"

    return None


def should_auto_confirm(
    ocr: Mapping[str, Any],
    matched_order: Optional[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> bool:
    """Return True only when it is safe to auto-confirm the matched order."""
    return auto_confirm_block_reason(ocr, matched_order, cfg) is None


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
