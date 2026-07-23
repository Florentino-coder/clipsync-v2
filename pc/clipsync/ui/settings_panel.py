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


def pairing_token_from_config(cfg: Mapping[str, Any]) -> str:
    """Chrome bridge pairing token from config (empty string if missing)."""
    bridge = cfg.get("chrome_bridge") or {}
    return str(bridge.get("pairing_token") or "")


def copy_text_to_clipboard(text: str, *, root: Any = None) -> None:
    """Copy text to clipboard via pyperclip, with Tk fallback."""
    try:
        import pyperclip

        pyperclip.copy(text)
        return
    except Exception:
        pass
    if root is not None and tk is not None:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        return
    raise RuntimeError("clipboard copy unavailable")


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

        row += 1
        ttk.Separator(form, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=(12, 8)
        )
        row += 1
        ttk.Label(form, text="Chrome extension pairing token", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w"
        )
        row += 1
        self._pairing_token = tk.StringVar(value="")
        token_entry = ttk.Entry(
            form, textvariable=self._pairing_token, width=40, state="readonly"
        )
        token_entry.grid(row=row, column=0, columnspan=2, sticky="we", pady=4)
        ttk.Button(form, text="Copy", command=self.copy_pairing_token).grid(
            row=row, column=2, sticky="w", padx=(8, 0)
        )
        row += 1
        ttk.Label(
            form,
            text="วาง token นี้ใน popup ของ ClipSync Slip Bridge → Save",
            foreground="#667085",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 4))
        row += 1
        self._ws_port_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=self._ws_port_var, foreground="#667085").grid(
            row=row, column=0, columnspan=3, sticky="w"
        )

        row += 1
        ttk.Separator(form, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=(12, 8)
        )
        row += 1
        ttk.Label(
            form,
            text="ติดตั้ง APK มือถือ (PC Hotspot + QR / ไม่ต้อง ADB)",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        self._apk_status = tk.StringVar(value="")
        ttk.Label(form, textvariable=self._apk_status, foreground="#667085", wraplength=520).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        row += 1
        apk_btns = ttk.Frame(form)
        apk_btns.grid(row=row, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Button(apk_btns, text="ดาวน์โหลด APK จาก GitHub", command=self.download_apk).pack(
            side="left"
        )
        ttk.Button(apk_btns, text="เปิดหน้า Hotspot", command=self.open_hotspot_settings).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(apk_btns, text="แชร์ APK + QR", command=self.share_apk_over_hotspot).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(apk_btns, text="Copy URL", command=self.copy_apk_url).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(apk_btns, text="หยุดแชร์", command=self.stop_apk_share).pack(
            side="left", padx=(8, 0)
        )
        row += 1
        self._apk_qr_label = ttk.Label(form)
        self._apk_qr_label.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._apk_url: str = ""
        self._apk_qr_photo = None  # keep PhotoImage ref alive

        actions = ttk.Frame(self.frame)
        actions.pack(fill="x", pady=(16, 0))
        ttk.Button(actions, text="Save", command=self.save).pack(side="left")
        ttk.Button(actions, text="Reload from disk", command=self.load_into_form).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="Copy pairing token", command=self.copy_pairing_token).pack(
            side="left", padx=(8, 0)
        )

        self._status_var = tk.StringVar(value="")
        ttk.Label(self.frame, textvariable=self._status_var).pack(anchor="w", pady=(10, 0))

        self.load_into_form()
        self.refresh_apk_status()

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
        token = pairing_token_from_config(cfg)
        self._pairing_token.set(token)
        port = (cfg.get("chrome_bridge") or {}).get("ws_port", 8765)
        self._ws_port_var.set(
            f"Chrome bridge: ws://127.0.0.1:{port}  (ต้องเปิด ClipSync PC ค้างไว้ก่อนกด Save & connect)"
        )
        self._status_var.set(f"Loaded {self._config_path}")

    def copy_pairing_token(self) -> None:
        token = (self._pairing_token.get() or "").strip()
        if not token:
            self._status_var.set("No pairing token — reload config")
            if messagebox is not None:
                messagebox.showwarning("Settings", "ยังไม่มี pairing token ใน config")
            return
        try:
            copy_text_to_clipboard(token, root=self.frame.winfo_toplevel())
        except Exception as exc:
            self._status_var.set(f"Copy failed: {exc}")
            if messagebox is not None:
                messagebox.showerror("Settings", str(exc))
            return
        self._status_var.set("Copied pairing token — paste into extension popup")

    def refresh_apk_status(self) -> None:
        from clipsync.apk_installer import find_bundled_apk, find_hotspot_pc_ip

        apk = find_bundled_apk()
        hotspot = find_hotspot_pc_ip()
        parts = []
        if apk is not None:
            parts.append(f"APK: {apk.name} ({apk.parent})")
        else:
            parts.append("APK: ยังไม่พบ — กดดาวน์โหลดจาก GitHub หรือวางใน Downloads")
        if hotspot:
            parts.append(f"Hotspot PC IP: {hotspot}")
        else:
            parts.append("Hotspot: ยังไม่เจอ — กด「เปิดหน้า Hotspot」แล้วเปิดสวิตช์")
        if self._apk_url:
            parts.append(f"กำลังแชร์: {self._apk_url}")
        self._apk_status.set(" | ".join(parts))

    def _show_apk_qr(self, qr_path: str) -> None:
        try:
            from PIL import Image, ImageTk
        except Exception:
            self._apk_qr_label.configure(text=f"QR: {qr_path}")
            return
        try:
            img = Image.open(qr_path).resize((180, 180))
            photo = ImageTk.PhotoImage(img)
            self._apk_qr_photo = photo
            self._apk_qr_label.configure(image=photo, text="")
        except Exception as exc:
            self._apk_qr_label.configure(image="", text=f"QR error: {exc}")

    def open_hotspot_settings(self) -> None:
        from clipsync.apk_installer import open_mobile_hotspot_settings

        try:
            open_mobile_hotspot_settings()
        except Exception as exc:
            if messagebox is not None:
                messagebox.showerror("Hotspot", str(exc))
            return
        self._status_var.set("เปิดหน้า Mobile hotspot แล้ว — เปิดสวิตช์ แล้วให้มือถือต่อ Wi‑Fi ของ PC")
        if messagebox is not None:
            messagebox.showinfo(
                "ขั้นตอนเปิด Hotspot",
                "1) ในหน้าต่าง Settings ที่เด้งขึ้น → เปิดสวิตช์ Mobile hotspot\n"
                "2) จดชื่อ Wi‑Fi + รหัส (หรือสแกน QR ของ Windows)\n"
                "3) บนมือถือ: ต่อ Wi‑Fi นั้น (ปิดเน็ตมือถือได้)\n"
                "4) กลับมาที่ ClipSync → กด「แชร์ APK + QR」\n"
                "5) สแกน QR ใน Settings หรือเปิด URL ที่คัดลอกไว้\n\n"
                "หมายเหตุ: อย่าเปิด IP ของมือถือ — ต้องเป็นของ PC (มักเป็น 192.168.137.1)",
            )
        self.refresh_apk_status()

    def download_apk(self) -> None:
        from clipsync.apk_installer import download_apk_from_url
        from clipsync.config import load_config

        try:
            cfg = load_config(path=self._config_path)
            url = str((cfg.get("apk") or {}).get("download_url") or "")
            self._status_var.set("กำลังดาวน์โหลด APK จาก GitHub…")
            self.frame.update_idletasks()
            path = download_apk_from_url(url)
        except Exception as exc:
            self._status_var.set(f"ดาวน์โหลดไม่สำเร็จ: {exc}")
            if messagebox is not None:
                messagebox.showerror(
                    "ดาวน์โหลด APK",
                    f"{exc}\n\nตรวจว่า clipsync-v2 มี release tag slip-test-latest "
                    "และ Actions build ผ่านแล้ว",
                )
            self.refresh_apk_status()
            return
        self.refresh_apk_status()
        self._status_var.set(f"ดาวน์โหลดแล้ว: {path}")
        if messagebox is not None:
            messagebox.showinfo(
                "ดาวน์โหลด APK",
                f"บันทึกแล้ว:\n{path}\n\nขั้นถัดไป: กด「เปิดหน้า Hotspot」→ เปิดสวิตช์ → "
                "มือถือต่อ Wi‑Fi ของ PC → กด「แชร์ APK + QR」",
            )

    def share_apk_over_hotspot(self) -> None:
        from clipsync.apk_installer import copy_apk_to_appdata, start_apk_share
        from clipsync.config import load_config

        try:
            cfg = load_config(path=self._config_path)
            port = int((cfg.get("apk") or {}).get("share_port") or 8788)
            local = copy_apk_to_appdata()
            info = start_apk_share(local, port=port)
        except FileNotFoundError as exc:
            self._status_var.set(str(exc))
            if messagebox is not None:
                messagebox.showwarning(
                    "ติดตั้ง APK",
                    f"{exc}\n\nกด「ดาวน์โหลด APK จาก GitHub」ก่อน หรือรอ Actions build เสร็จ",
                )
            self.refresh_apk_status()
            return
        except Exception as exc:
            self._status_var.set(f"แชร์ APK ไม่ได้: {exc}")
            if messagebox is not None:
                messagebox.showerror(
                    "ติดตั้ง APK",
                    f"{exc}\n\nถ้ายังไม่เปิด Hotspot: กด「เปิดหน้า Hotspot」ก่อน",
                )
            self.refresh_apk_status()
            return

        self._apk_url = info["url"]
        self._show_apk_qr(info["qr_path"])
        try:
            copy_text_to_clipboard(self._apk_url, root=self.frame.winfo_toplevel())
        except Exception:
            pass
        self.refresh_apk_status()
        self._status_var.set(f"แชร์ APK แล้ว — สแกน QR หรือเปิด: {self._apk_url}")
        if messagebox is not None:
            messagebox.showinfo(
                "ติดตั้ง APK ผ่าน Hotspot",
                "มือถือต้องต่อ Wi‑Fi Hotspot ของ PC อยู่แล้ว\n\n"
                "1) สแกน QR ในหน้า Settings (หรือเปิด URL ที่คัดลอกไว้)\n"
                f"2) URL ที่ถูก:\n{self._apk_url}\n"
                "3) ดาวน์โหลด → ติดตั้ง (อนุญาตติดตั้งจากไฟล์)\n\n"
                "อย่าใช้ IP ของมือถือ (.252 ฯลฯ) — ต้องเป็นของ PC (.1)",
            )

    def copy_apk_url(self) -> None:
        if not self._apk_url:
            self.refresh_apk_status()
            if messagebox is not None:
                messagebox.showwarning("ติดตั้ง APK", "ยังไม่ได้แชร์ — กด「แชร์ APK + QR」ก่อน")
            return
        try:
            copy_text_to_clipboard(self._apk_url, root=self.frame.winfo_toplevel())
            self._status_var.set("Copied APK URL")
        except Exception as exc:
            self._status_var.set(f"Copy failed: {exc}")

    def stop_apk_share(self) -> None:
        from clipsync.apk_installer import stop_apk_share

        stop_apk_share()
        self._apk_url = ""
        self._apk_qr_photo = None
        self._apk_qr_label.configure(image="", text="")
        self.refresh_apk_status()
        self._status_var.set("หยุดแชร์ APK แล้ว")

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
