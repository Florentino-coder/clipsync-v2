"""Read-only audit.jsonl viewer with day / confirmed_by filters."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]

from clipsync.audit import default_audit_path

COLUMNS = ("ts", "event_id", "amount", "order_id", "decision", "confirmed_by")


def load_audit_records(path: Path | str) -> list[dict[str, Any]]:
    audit_path = Path(path)
    if not audit_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _record_day(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    text = str(ts).strip()
    if not text:
        return None
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10]


def filter_audit_records(
    records: Sequence[Mapping[str, Any]],
    *,
    day: Optional[str] = None,
    confirmed_by: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Filter by calendar day (YYYY-MM-DD) and/or confirmed_by type."""
    out: list[dict[str, Any]] = []
    for rec in records:
        if day:
            if _record_day(rec.get("ts")) != day:
                continue
        if confirmed_by is not None and confirmed_by != "":
            if str(rec.get("confirmed_by") or "") != confirmed_by:
                continue
        out.append(dict(rec))
    return out


def format_audit_row(rec: Mapping[str, Any]) -> tuple[str, ...]:
    amount = rec.get("amount")
    if amount is None or amount == "":
        amount_s = "-"
    else:
        try:
            amount_s = f"{float(amount):.2f}"
        except (TypeError, ValueError):
            amount_s = str(amount)
    return (
        str(rec.get("ts") or "-"),
        str(rec.get("event_id") or "-"),
        amount_s,
        str(rec.get("order_id") or "-"),
        str(rec.get("decision") or "-"),
        str(rec.get("confirmed_by") or "-"),
    )


class AuditViewer:
    """Notebook tab: ประวัติ — read-only audit trail."""

    def __init__(
        self,
        parent: Any,
        *,
        audit_path: Optional[Path | str] = None,
    ) -> None:
        if ttk is None or tk is None:
            raise RuntimeError("tkinter is required for AuditViewer")

        self._audit_path = Path(audit_path) if audit_path else default_audit_path()
        self._all_records: list[dict[str, Any]] = []

        self.frame = ttk.Frame(parent, padding=12)
        self.frame.pack(fill="both", expand=True)

        filters = ttk.Frame(self.frame)
        filters.pack(fill="x", pady=(0, 8))

        ttk.Label(filters, text="วัน (YYYY-MM-DD)").pack(side="left")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._day_var = tk.StringVar(value="")
        ttk.Entry(filters, textvariable=self._day_var, width=14).pack(
            side="left", padx=(6, 12)
        )
        ttk.Label(filters, text="ประเภท").pack(side="left")
        self._type_var = tk.StringVar(value="all")
        ttk.Combobox(
            filters,
            textvariable=self._type_var,
            values=("all", "system", "admin_manual"),
            state="readonly",
            width=14,
        ).pack(side="left", padx=(6, 12))
        ttk.Button(filters, text="Apply", command=self.apply_filters).pack(side="left")
        ttk.Button(filters, text="Refresh", command=self.reload).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            filters, text="Today", command=lambda: self._set_day(today)
        ).pack(side="left", padx=(8, 0))

        tree_frame = ttk.Frame(self.frame)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=COLUMNS,
            show="headings",
            selectmode="browse",
            height=16,
        )
        headings = {
            "ts": "Timestamp",
            "event_id": "Event",
            "amount": "Amount",
            "order_id": "Order",
            "decision": "Decision",
            "confirmed_by": "By",
        }
        widths = {
            "ts": 170,
            "event_id": 120,
            "amount": 70,
            "order_id": 90,
            "decision": 110,
            "confirmed_by": 100,
        }
        for col in COLUMNS:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], stretch=True)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._status_var = tk.StringVar(value="")
        ttk.Label(self.frame, textvariable=self._status_var).pack(anchor="w", pady=(8, 0))

        self.reload()

    def _set_day(self, day: str) -> None:
        self._day_var.set(day)
        self.apply_filters()

    def reload(self) -> None:
        self._all_records = load_audit_records(self._audit_path)
        self.apply_filters()
        self._status_var.set(
            f"{self._audit_path} — {len(self._all_records)} records loaded"
        )

    def apply_filters(self) -> None:
        day = self._day_var.get().strip() or None
        type_sel = self._type_var.get().strip()
        confirmed_by = None if type_sel in ("", "all") else type_sel
        filtered = filter_audit_records(
            self._all_records, day=day, confirmed_by=confirmed_by
        )
        self.tree.delete(*self.tree.get_children())
        # newest first
        for rec in reversed(filtered):
            self.tree.insert("", "end", values=format_audit_row(rec))
        self._status_var.set(
            f"Showing {len(filtered)} / {len(self._all_records)} "
            f"(day={day or 'any'}, type={type_sel})"
        )
