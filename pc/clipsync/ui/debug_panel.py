"""Real-time slip debug panel (tkinter Treeview + queue poll)."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception:  # pragma: no cover
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]

STATUS_TAG_COLORS = {
    "ok": "#d1fadf",
    "warn": "#fef0c7",
    "error": "#fee4e2",
}

_STATUS_TAG_MAP = {
    "auto_confirmed": "ok",
    "auto-confirmed": "ok",
    "admin_manual": "ok",
    "pending_review": "warn",
    "pending review": "warn",
    "กำลังยืนยัน": "warn",
    "confirm_sent": "warn",
    "rejected": "error",
    "confirm_failed": "error",
    "overridden": "error",
}

_STATUS_LABELS = {
    "auto_confirmed": "สำเร็จ",
    "auto-confirmed": "สำเร็จ",
    "admin_manual": "สำเร็จ",
    "pending_review": "รอตรวจ",
    "pending review": "รอตรวจ",
    "กำลังยืนยัน": "กำลังยืนยัน",
    "confirm_sent": "กำลังยืนยัน",
    "rejected": "ปฏิเสธ",
    "confirm_failed": "ล้มเหลว",
    "overridden": "แทนที่",
}

# Main table: time | bank | amount | from last4 | to last4 | status
COLUMNS = ("time", "bank", "amount", "from_acct", "to_acct", "status")


@dataclass(frozen=True)
class SlipRow:
    values: tuple[str, str, str, str, str, str]
    tag: str
    event_id: str
    ref_number: str
    raw: Mapping[str, Any]


def status_tag_for(decision: str | None) -> str:
    if not decision:
        return "warn"
    return _STATUS_TAG_MAP.get(str(decision).strip(), "warn")


def status_display_label(decision: str | None) -> str:
    if not decision:
        return "-"
    key = str(decision).strip()
    return _STATUS_LABELS.get(key, key)


def _format_time(ts: Any) -> str:
    if ts is None or ts == "":
        return datetime.now().strftime("%H:%M:%S")
    text = str(ts)
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%H:%M:%S")
    except ValueError:
        if "T" in text:
            return text.split("T", 1)[1][:8]
        return text[:8]


def _format_amount(amount: Any) -> str:
    if amount is None or amount == "":
        return "-"
    try:
        return f"{float(amount):.2f}"
    except (TypeError, ValueError):
        return str(amount)


def _account_last4(event: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        val = event.get(key)
        if val is None or val == "":
            continue
        digits = "".join(ch for ch in str(val) if ch.isdigit())
        if len(digits) >= 4:
            return f"…{digits[-4:]}"
        text = str(val).strip()
        if text:
            return text[-6:] if len(text) > 6 else text
    return "-"


def format_slip_row(event: Mapping[str, Any]) -> SlipRow:
    decision = event.get("decision") or event.get("status")
    bank = event.get("bank") or event.get("bank_code") or "-"
    ref = event.get("ref_number") or event.get("ref") or ""
    from_acct = _account_last4(
        event,
        "sender_account_last4",
        "senderAccountLast4",
        "sender_account_masked",
        "sender_account",
    )
    to_acct = _account_last4(
        event,
        "receiver_account_last4",
        "receiverAccountLast4",
        "receiver_account_masked",
        "receiver_account",
    )
    return SlipRow(
        values=(
            _format_time(event.get("ts") or event.get("time")),
            str(bank) if bank else "-",
            _format_amount(event.get("amount")),
            from_acct,
            to_acct,
            status_display_label(decision if isinstance(decision, str) else None),
        ),
        tag=status_tag_for(decision if isinstance(decision, str) else None),
        event_id=str(event.get("event_id") or ""),
        ref_number=str(ref) if ref else "",
        raw=dict(event),
    )


def format_slip_details(event: Mapping[str, Any]) -> str:
    """Human-readable detail block for the รายละเอียด popup."""

    def g(*keys: str, default: str = "-") -> str:
        for key in keys:
            val = event.get(key)
            if val is not None and str(val).strip() != "":
                return str(val).strip()
        return default

    lines = [
        f"เวลา: { _format_time(event.get('ts') or event.get('time')) }",
        f"จำนวน: {_format_amount(event.get('amount'))}",
        f"ธนาคารร้าน: {g('bank', 'bank_name_th', 'bank_name')}",
        "",
        "ผู้โอน (จาก / บัญชีร้าน)",
        f"  ชื่อ: {g('sender_name', 'senderName', 'sender')}",
        f"  บัญชี: {g('sender_account_masked', 'sender_account', 'sender_account_last4')}",
        "",
        "ผู้รับ (ไปยัง / สมาชิก)",
        f"  ชื่อ: {g('receiver_name', 'receiverName', 'receiver')}",
        f"  บัญชี: {g('receiver_account_masked', 'receiver_account', 'receiver_account_last4')}",
        f"  ธนาคาร: {g('receiver_bank', 'receiver_bank_name_th', 'receiver_bank_name')}",
        "",
        f"สถานะ: {status_display_label(str(event.get('decision') or event.get('status') or ''))}",
        f"Transport: {g('transport', 'source')}",
        f"Ref: {g('ref_number', 'ref')}",
        f"Order: {g('order_id', 'orderId')}",
        f"Event: {g('event_id')}",
    ]
    return "\n".join(lines)


ManualConfirmFn = Callable[[Mapping[str, Any]], None]
RejectFn = Callable[[Mapping[str, Any]], None]
ViewSlipFn = Callable[[Mapping[str, Any]], None]


class DebugPanel:
    """Treeview of recent slip decisions; poll a queue every 200ms."""

    POLL_MS = 200
    MAX_ROWS = 200

    def __init__(
        self,
        parent: Any,
        event_queue: queue.Queue,
        *,
        on_manual_confirm: Optional[ManualConfirmFn] = None,
        on_reject: Optional[RejectFn] = None,
        on_view_slip: Optional[ViewSlipFn] = None,
    ) -> None:
        if ttk is None or tk is None:
            raise RuntimeError("tkinter is required for DebugPanel")

        self._queue = event_queue
        self._on_manual_confirm = on_manual_confirm
        self._on_reject = on_reject
        self._on_view_slip = on_view_slip
        self._rows: dict[str, SlipRow] = {}

        self.frame = ttk.Frame(parent, padding=8)
        self.frame.pack(fill="both", expand=True)

        toolbar = ttk.Frame(self.frame)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="ดูรูปสลิป", command=self._view_selected).pack(
            side="left"
        )
        ttk.Button(toolbar, text="รายละเอียด", command=self._details_selected).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(toolbar, text="ยืนยันเอง", command=self._confirm_selected).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(toolbar, text="ปฏิเสธ", command=self._reject_selected).pack(
            side="left", padx=(8, 0)
        )

        tree_frame = ttk.Frame(self.frame)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=COLUMNS,
            show="headings",
            selectmode="browse",
            height=14,
        )
        headings = {
            "time": "เวลา",
            "bank": "ธนาคาร",
            "amount": "จำนวน",
            "from_acct": "จาก",
            "to_acct": "ไปยัง",
            "status": "สถานะ",
        }
        widths = {
            "time": 72,
            "bank": 64,
            "amount": 72,
            "from_acct": 72,
            "to_acct": 72,
            "status": 90,
        }
        for col in COLUMNS:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], stretch=True)

        for tag, color in STATUS_TAG_COLORS.items():
            self.tree.tag_configure(tag, background=color)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.frame.after(self.POLL_MS, self._poll)

    def post(self, event: Mapping[str, Any]) -> None:
        """Thread-safe: enqueue a slip UI event for the next poll cycle."""
        self._queue.put(dict(event))

    def _poll(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                self._insert_row(item)
        except queue.Empty:
            pass
        self.frame.after(self.POLL_MS, self._poll)

    def _insert_row(self, event: Mapping[str, Any]) -> None:
        row = format_slip_row(event)
        iid = row.event_id or f"row-{len(self._rows)}"
        self._rows[iid] = row
        if self.tree.exists(iid):
            self.tree.item(iid, values=row.values, tags=(row.tag,))
        else:
            self.tree.insert("", 0, iid=iid, values=row.values, tags=(row.tag,))
        children = self.tree.get_children()
        if len(children) > self.MAX_ROWS:
            for old in children[self.MAX_ROWS :]:
                self.tree.delete(old)
                self._rows.pop(old, None)

    def _selected_row(self) -> Optional[SlipRow]:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._rows.get(sel[0])

    def _view_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("ดูรูปสลิป", "เลือกแถวสลิปก่อน")
            return
        if self._on_view_slip is not None:
            self._on_view_slip(row.raw)
            return
        messagebox.showinfo(
            "ดูรูปสลิป",
            f"ยังไม่มีรูปสำหรับ event {row.event_id or '-'}\n"
            "(ต้องเชื่อม USB + slip_fetcher)",
        )

    def _details_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("รายละเอียด", "เลือกแถวสลิปก่อน")
            return
        messagebox.showinfo("รายละเอียดสลิป", format_slip_details(row.raw))

    def _confirm_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("ยืนยันเอง", "เลือกแถวสลิปก่อน")
            return
        if self._on_manual_confirm is not None:
            self._on_manual_confirm(row.raw)
        else:
            messagebox.showinfo("ยืนยันเอง", "ยังไม่ได้เชื่อม manual confirm handler")

    def _reject_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("ปฏิเสธ", "เลือกแถวสลิปก่อน")
            return
        if self._on_reject is not None:
            self._on_reject(row.raw)
        else:
            messagebox.showinfo("ปฏิเสธ", "ยังไม่ได้เชื่อม reject handler")
