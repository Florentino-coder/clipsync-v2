"""Settings panel for slip auto-confirm config + transport status."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception:  # pragma: no cover
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]

from clipsync.config import _PREFERRED_MODES, default_config_path, load_config, save_config

ReloadCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class SettingsFormValues:
    auto_confirm_enabled: bool
    threshold_enabled: bool
    amount_threshold: float
    min_ocr_confidence: float
    preferred_mode: str


def form_values_from_config(cfg: Mapping[str, Any]) -> SettingsFormValues:
    ac = cfg.get("auto_confirm") or {}
    review = ac.get("require_manual_review") or {}
    transport = cfg.get("transport") or {}
    return SettingsFormValues(
        auto_confirm_enabled=bool(ac.get("enabled", False)),
        threshold_enabled=bool(review.get("enabled", True)),
        amount_threshold=float(review.get("amount_threshold", 5000.0)),
        min_ocr_confidence=float(ac.get("min_ocr_confidence", 0.90)),
        preferred_mode=str(transport.get("preferred_mode") or "auto"),
    )


def apply_form_values(cfg: Mapping[str, Any], values: SettingsFormValues) -> dict[str, Any]:
    if values.preferred_mode not in _PREFERRED_MODES:
        raise ValueError(
            f"transport.preferred_mode must be one of {sorted(_PREFERRED_MODES)}, "
            f"got {values.preferred_mode!r}"
        )
    updated = deepcopy(dict(cfg))
    updated.setdefault("auto_confirm", {})
    updated["auto_confirm"]["enabled"] = bool(values.auto_confirm_enabled)
    updated["auto_confirm"]["min_ocr_confidence"] = float(values.min_ocr_confidence)
    updated["auto_confirm"].setdefault("require_manual_review", {})
    updated["auto_confirm"]["require_manual_review"]["enabled"] = bool(
        values.threshold_enabled
    )
    updated["auto_confirm"]["require_manual_review"]["amount_threshold"] = float(
        values.amount_threshold
    )
    updated.setdefault("transport", {})
    updated["transport"]["preferred_mode"] = values.preferred_mode
    return updated


def transport_indicator(name: Optional[str]) -> tuple[str, str]:
    """Return (label, color) for the live transport status strip."""
    if name == "usb":
        return ("● USB (Local)", "#19a94b")
    if name == "relay":
        return ("● Cloud Relay (สำรอง — ไม่พบสาย USB)", "#e09c18")
    return ("● ไม่เชื่อมต่อ", "#667085")


class SettingsPanel:
    """Form to edit slip config with save + hot-reload callback."""

    def __init__(
        self,
        parent: Any,
        *,
        config_path: Optional[Path | str] = None,
        on_reload: Optional[ReloadCallback] = None,
        initial_transport: Optional[str] = None,
    ) -> None:
        if ttk is None or tk is None:
            raise RuntimeError("tkinter is required for SettingsPanel")

        self._config_path = Path(config_path) if config_path else default_config_path()
        self._on_reload = on_reload
        self._transport_name = initial_transport

        self.frame = ttk.Frame(parent, padding=12)
        self.frame.pack(fill="both", expand=True)

        self._transport_var = tk.StringVar(value="")
        self._transport_label = ttk.Label(
            self.frame,
            textvariable=self._transport_var,
            font=("Segoe UI", 11, "bold"),
        )
        self._transport_label.pack(anchor="w", pady=(0, 12))
        self.set_transport(initial_transport)

        form = ttk.Frame(self.frame)
        form.pack(fill="x")

        self._auto_confirm = tk.BooleanVar(value=False)
        self._threshold_enabled = tk.BooleanVar(value=True)
        self._amount_threshold = tk.StringVar(value="5000")
        self._min_confidence = tk.StringVar(value="0.90")
        self._preferred_mode = tk.StringVar(value="auto")

        row = 0
        ttk.Checkbutton(
            form, text="Auto-confirm", variable=self._auto_confirm
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        row += 1
        ttk.Checkbutton(
            form,
            text="Require manual review above threshold",
            variable=self._threshold_enabled,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        row += 1
        ttk.Label(form, text="Amount threshold").grid(row=row, column=0, sticky="w")
        ttk.Entry(form, textvariable=self._amount_threshold, width=16).grid(
            row=row, column=1, sticky="w", padx=(8, 0), pady=4
        )
        row += 1
        ttk.Label(form, text="Min OCR confidence").grid(row=row, column=0, sticky="w")
        ttk.Entry(form, textvariable=self._min_confidence, width=16).grid(
            row=row, column=1, sticky="w", padx=(8, 0), pady=4
        )
        row += 1
        ttk.Label(form, text="Preferred transport").grid(row=row, column=0, sticky="w")
        mode_box = ttk.Combobox(
            form,
            textvariable=self._preferred_mode,
            values=("auto", "usb", "relay"),
            state="readonly",
            width=14,
        )
        mode_box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=4)

        actions = ttk.Frame(self.frame)
        actions.pack(fill="x", pady=(16, 0))
        ttk.Button(actions, text="Save", command=self.save).pack(side="left")
        ttk.Button(actions, text="Reload from disk", command=self.load_into_form).pack(
            side="left", padx=(8, 0)
        )

        self._status_var = tk.StringVar(value="")
        ttk.Label(self.frame, textvariable=self._status_var).pack(anchor="w", pady=(10, 0))

        self.load_into_form()

    def set_transport(self, name: Optional[str]) -> None:
        self._transport_name = name
        label, color = transport_indicator(name)
        self._transport_var.set(label)
        try:
            self._transport_label.configure(foreground=color)
        except Exception:
            pass

    def on_transport_changed(self, old: Optional[str], new: str) -> None:
        del old
        self.set_transport(new)

    def _read_form(self) -> SettingsFormValues:
        try:
            amount = float(self._amount_threshold.get().strip())
            conf = float(self._min_confidence.get().strip())
        except ValueError as exc:
            raise ValueError("threshold and confidence must be numbers") from exc
        return SettingsFormValues(
            auto_confirm_enabled=bool(self._auto_confirm.get()),
            threshold_enabled=bool(self._threshold_enabled.get()),
            amount_threshold=amount,
            min_ocr_confidence=conf,
            preferred_mode=str(self._preferred_mode.get() or "auto"),
        )

    def load_into_form(self) -> None:
        cfg = load_config(path=self._config_path)
        values = form_values_from_config(cfg)
        self._auto_confirm.set(values.auto_confirm_enabled)
        self._threshold_enabled.set(values.threshold_enabled)
        self._amount_threshold.set(str(values.amount_threshold))
        self._min_confidence.set(str(values.min_ocr_confidence))
        self._preferred_mode.set(values.preferred_mode)
        self._status_var.set(f"Loaded {self._config_path}")

    def save(self) -> None:
        try:
            values = self._read_form()
            cfg = load_config(path=self._config_path)
            updated = apply_form_values(cfg, values)
            save_config(updated, path=self._config_path)
        except Exception as exc:
            if messagebox is not None:
                messagebox.showerror("Settings", str(exc))
            self._status_var.set(f"Save failed: {exc}")
            return

        self._status_var.set("Saved — hot-reloaded")
        if self._on_reload is not None:
            self._on_reload(updated)
