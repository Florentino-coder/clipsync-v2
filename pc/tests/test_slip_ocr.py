"""Payer ("จาก") account last-4 parsing from slip OCR text."""

from __future__ import annotations

from clipsync.slip_ocr import extract_sender_account_last4, parse_sender_last4_from_text


def test_scb_slip_from_masked_account():
    text = "\n".join(
        [
            "โอนเงินสำเร็จ",
            "24 ก.ค. 2569 - 11:41",
            "รหัสอ้างอิง: 202607249a7RUxGBGQWI6jtqt",
            "จาก นางสาว อังคณี ฉ.",
            "xxx-xxx747-6",
            "ไปยัง นางสาว ศศิภา เสาว์ศรี",
            "x-4106",
            "จำนวนเงิน 1,067.00",
        ]
    )
    assert parse_sender_last4_from_text(text) == "7476"


def test_from_and_account_on_same_line():
    text = "จาก บริษัท ทดสอบ xxx-xxx5042827476\nไปยัง สมชาย x-8309\nจำนวนเงิน 600.00"
    assert parse_sender_last4_from_text(text) == "7476"


def test_ignores_receiver_and_amount():
    # No payer block present → must not grab the receiver or amount digits.
    text = "ไปยัง นางสาว ก x-8309\nจำนวนเงิน 1,732.00"
    assert parse_sender_last4_from_text(text) is None


def test_does_not_pick_amount_as_account():
    text = "จาก ร้านค้า\nจำนวนเงิน 1,067.00\nไปยัง x-4106"
    # The only thing after 'จาก' before a stop marker is the shop name (no digits).
    assert parse_sender_last4_from_text(text) is None


def test_kbank_style_plain_digits():
    text = "From SHOP CO\n1234567476\nTo MEMBER\n9990008309\nAmount 500.00"
    assert parse_sender_last4_from_text(text) == "7476"


def test_empty_and_none():
    assert parse_sender_last4_from_text("") is None
    assert parse_sender_last4_from_text(None) is None


def test_extract_returns_none_without_ocr_backend():
    # No/blank image bytes must never raise and must return None (fail-safe).
    assert extract_sender_account_last4(b"") is None
