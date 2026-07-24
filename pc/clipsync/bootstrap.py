"""Bootstrap slip auto-confirm stack (chrome bridge, transport, orchestrator)."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from clipsync.chrome_bridge import ChromeBridge
from clipsync.config import load_config
from clipsync.license import verify_token
from clipsync.orchestrator import SlipOrchestrator
from clipsync.transport.manager import TransportManager
from clipsync.transport.usb import UsbTransport

logger = logging.getLogger(__name__)

SlipRelayHandler = Callable[[dict[str, Any]], Awaitable[None]]


def _license_warning(cfg: dict[str, Any]) -> Optional[str]:
    """Non-blocking license check for startup logging."""
    token_path = Path(str((cfg.get("license") or {}).get("token_path", "")))
    if not token_path.is_file():
        return "No license token found (slip features still available for setup)"
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return f"Could not read license token: {exc}"
    result = verify_token(token)
    if not result.valid:
        reason = result.reason or "invalid"
        return f"License not valid: {reason}"
    if result.warning:
        return result.warning
    return None


class SlipBootstrap:
    """Runs async slip services on a background thread; does not block clipboard sync."""

    def __init__(self, app: Any, client: Any, *, shared_secret: str) -> None:
        self._app = app
        self._client = client
        self._shared_secret = shared_secret
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = threading.Event()
        self._orchestrator: Optional[SlipOrchestrator] = None
        self._bridge: Optional[ChromeBridge] = None
        self._manager: Optional[TransportManager] = None
        self._cfg: dict[str, Any] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        if not self._shared_secret:
            return
        try:
            self._cfg = load_config()
        except Exception as exc:
            self._app_log(f"Slip config load failed: {exc}")
            return

        warning = _license_warning(self._cfg)
        if warning:
            self._app_log(f"License: {warning}")

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="slip-bootstrap",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_stop(), loop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception:
            logger.exception("slip bootstrap thread failed")
            self._app_log("Slip stack crashed — see logs")

    async def _async_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        cfg = self._cfg

        self._bridge = ChromeBridge(
            cfg,
            on_pending_orders=self._on_pending_orders,
            on_confirm_result=self._on_confirm_result,
        )
        self._orchestrator = SlipOrchestrator(
            cfg,
            chrome_bridge=self._bridge,
            shared_secret=self._shared_secret,
        )

        self._manager = TransportManager(
            self._shared_secret,
            mode=(cfg.get("transport") or {}).get("preferred_mode", "auto"),
            on_transport_changed=self._on_transport_changed,
        )

        async def send_ack(event_id: str) -> None:
            transport = self._manager.transport if self._manager else None
            if isinstance(transport, UsbTransport):
                await transport.send_ack(event_id)
                return
            send = getattr(self._client, "send_slip_ack", None)
            if send is not None:
                result = send(event_id)
                if asyncio.iscoroutine(result):
                    await result

        self._orchestrator.set_send_ack(send_ack)

        async def on_slip_event(payload: dict[str, Any]) -> None:
            if self._orchestrator is None:
                return
            source = "usb"
            if self._manager and self._manager.transport_name == "relay":
                source = "relay"
            result = await self._orchestrator.handle_slip_event(payload, source=source)
            self._push_ui_event(payload, result)

        await self._bridge.start()
        await self._manager.start(on_slip_event)

        relay_handler = self._make_relay_handler()
        set_handler = getattr(self._client, "set_slip_message_handler", None)
        if callable(set_handler):
            set_handler(relay_handler)

        self._app.after(0, lambda: self._app.set_slip_override_bridge(self._bridge))
        self._app.after(0, lambda: self._app.set_slip_orchestrator(self._orchestrator))
        port = self._bridge.port if self._bridge else 8765
        self._app_log(
            f"Slip stack started — Chrome bridge listening on ws://127.0.0.1:{port} "
            "(auto_confirm off unless enabled in Settings)"
        )

        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

        await self._async_stop()

    async def _async_stop(self) -> None:
        clear_handler = getattr(self._client, "set_slip_message_handler", None)
        if callable(clear_handler):
            clear_handler(None)
        if self._manager is not None:
            await self._manager.stop()
            self._manager = None
        if self._bridge is not None:
            await self._bridge.stop()
            self._bridge = None
        self._orchestrator = None

    def _make_relay_handler(self) -> SlipRelayHandler:
        async def _handle(msg: dict[str, Any]) -> None:
            if self._orchestrator is None:
                return
            payload = msg.get("payload")
            if not isinstance(payload, dict):
                return
            sig = str(msg.get("sig") or "")
            result = await self._orchestrator.handle_slip_event(
                payload,
                source="relay",
                sig=sig,
            )
            thumb = msg.get("thumbnail_jpeg_b64")
            thumb_s = thumb if isinstance(thumb, str) else None
            self._push_ui_event(payload, result, thumbnail_jpeg_b64=thumb_s)

        return _handle

    def _on_pending_orders(self, data: dict[str, Any]) -> None:
        if self._orchestrator is not None:
            self._orchestrator.on_pending_orders(data)

    def _on_confirm_result(self, data: dict[str, Any]) -> None:
        reason = data.get("reason")
        ok = data.get("ok")
        match_key = data.get("matchKey") or data.get("tried") or "-"
        verified = data.get("verified")
        event_id = str(data.get("event_id") or "")
        amount = data.get("amount")
        if ok and (verified is True or reason in (None, "", "ok") or data.get("dismissed")):
            msg = f"ยืนยันสำเร็จ ({match_key})"
            self._app_log(f"Extension: {msg}")
            self._app_status(msg, "#19a94b")
            self._update_slip_row_status(
                event_id=event_id,
                amount=amount or match_key,
                decision="admin_manual",
            )
        elif reason == "dry_run":
            msg = f"dry-run พร้อมกดจริง ({match_key}) — ดูกรอบแดงบนหน้าเว็บ"
            self._app_log(f"Extension: {msg}")
            self._app_status(msg, "#e09c18")
        else:
            msg = f"ยืนยันล้มเหลว: {reason or 'unknown'} ({match_key})"
            self._app_log(f"Extension: {msg}")
            self._app_status(msg, "#d92d20")
            self._update_slip_row_status(
                event_id=event_id,
                amount=amount or match_key,
                decision="confirm_failed",
                extra_reason=str(reason or ""),
            )
        if self._orchestrator is not None:
            self._orchestrator.on_confirm_result(data)

    def _update_slip_row_status(
        self,
        *,
        event_id: str,
        amount: Any,
        decision: str,
        extra_reason: str = "",
    ) -> None:
        """Refresh Slip tab row after extension finishes (success or fail)."""
        app = self._app
        pending = getattr(app, "_pending_manual_confirms", None)
        base: dict[str, Any] = {}
        if isinstance(pending, dict):
            if event_id and event_id in pending:
                base = dict(pending.pop(event_id) or {})
            else:
                amt_key = f"amount:{amount}" if amount not in (None, "", "-") else ""
                if amt_key and amt_key in pending:
                    base = dict(pending.pop(amt_key) or {})
        if not base:
            base = {
                "event_id": event_id or f"confirm-{amount}",
                "amount": amount,
            }
        ui_event = {
            **base,
            "decision": decision,
            "confirmed_by": "admin_manual",
        }
        if extra_reason:
            ui_event["fail_reason"] = extra_reason

        def _enqueue() -> None:
            app.push_slip_ui_event(ui_event)

        app.after(0, _enqueue)

    def _on_transport_changed(self, old: Optional[str], new: str) -> None:
        self._app.after(0, lambda: self._app.on_transport_changed(old, new))

    def _push_ui_event(
        self,
        payload: dict[str, Any],
        result: dict[str, Any],
        *,
        thumbnail_jpeg_b64: Optional[str] = None,
    ) -> None:
        from clipsync.slip_image import ui_event_with_thumbnail

        ui_event = ui_event_with_thumbnail(
            payload,
            result,
            thumbnail_jpeg_b64=thumbnail_jpeg_b64,
            transport=self._manager.transport_name if self._manager else None,
        )

        def _enqueue() -> None:
            self._app.push_slip_ui_event(ui_event)

        self._app.after(0, _enqueue)

    def _app_log(self, message: str) -> None:
        append = getattr(self._app, "_append_log", None)
        if callable(append):
            self._app.after(0, lambda: append(message))

    def _app_status(self, message: str, color: str) -> None:
        setter = getattr(self._app, "_set_status", None)
        if callable(setter):
            self._app.after(0, lambda: setter(message, color))


def start_slip_bootstrap(app: Any, client: Any, shared_secret: str) -> SlipBootstrap:
    """Start slip services when config + shared secret are available."""
    bootstrap = SlipBootstrap(app, client, shared_secret=shared_secret)
    bootstrap.start()
    return bootstrap
