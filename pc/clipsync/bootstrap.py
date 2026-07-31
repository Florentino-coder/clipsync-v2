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
from clipsync.withdraw_notify import (
    build_slip_status_payload,
    resolve_order_id_for_slip_status,
    should_emit_slip_status,
)

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
        self._last_slip_status_key: str | None = None
        self._last_slip_status_at: float = 0.0

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
            send_withdraw_notify=self._emit_withdraw_notify,
            send_slip_status=self._emit_slip_status,
            activity_log=self._app_log,
        )

        self._manager = TransportManager(
            self._shared_secret,
            mode=(cfg.get("transport") or {}).get("preferred_mode", "relay"),
            on_transport_changed=self._on_transport_changed,
        )

        set_relay_failure = getattr(self._client, "set_relay_failure_handler", None)
        if callable(set_relay_failure):
            set_relay_failure(self._notify_relay_failure)
        set_image_request = getattr(self._client, "set_image_request_handler", None)
        if callable(set_image_request):
            set_image_request(self._send_image_request)

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
            if payload.get("type") == "image_response":
                event_id = str(payload.get("event_id") or "")
                image_b64 = payload.get("image_jpeg_b64")
                if event_id and isinstance(image_b64, str) and image_b64:
                    self._push_ui_image(event_id, image_b64)
                return
            if self._orchestrator is None:
                return
            source = "usb"
            if self._manager and self._manager.transport_name == "relay":
                source = "relay"
            thumb = payload.get("thumbnail_jpeg_b64")
            thumb_s = thumb if isinstance(thumb, str) else None
            result = await self._orchestrator.handle_slip_event(
                payload,
                source=source,
                thumbnail_jpeg_b64=thumb_s,
            )
            self._push_ui_event(payload, result, thumbnail_jpeg_b64=thumb_s)

        await self._bridge.start()
        await self._manager.start(on_slip_event)

        relay_handler = self._make_relay_handler()
        set_handler = getattr(self._client, "set_slip_message_handler", None)
        if callable(set_handler):
            set_handler(relay_handler)

        self._app.after(0, lambda: self._app.set_slip_override_bridge(self._bridge))
        self._app.after(0, lambda: self._app.set_slip_orchestrator(self._orchestrator))
        port = self._bridge.port if self._bridge else 8765
        ac_on = bool((cfg.get("auto_confirm") or {}).get("enabled", False))
        ac_label = "ON" if ac_on else "OFF"
        self._app_log(
            f"Slip stack started — Chrome bridge listening on ws://127.0.0.1:{port} "
            f"(Auto-confirm: {ac_label})"
        )

        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

        await self._async_stop()

    async def _async_stop(self) -> None:
        clear_handler = getattr(self._client, "set_slip_message_handler", None)
        if callable(clear_handler):
            clear_handler(None)
        clear_image_request = getattr(self._client, "set_image_request_handler", None)
        if callable(clear_image_request):
            clear_image_request(None)
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
            if msg.get("type") == "image_response":
                event_id = str(msg.get("event_id") or "")
                image_b64 = msg.get("image_jpeg_b64")
                if event_id and isinstance(image_b64, str) and image_b64:
                    self._push_ui_image(event_id, image_b64)
                return
            payload = msg.get("payload")
            if not isinstance(payload, dict):
                return
            sig = str(msg.get("sig") or "")
            thumb = msg.get("thumbnail_jpeg_b64")
            thumb_s = thumb if isinstance(thumb, str) else None
            result = await self._orchestrator.handle_slip_event(
                payload,
                source="relay",
                sig=sig,
                thumbnail_jpeg_b64=thumb_s,
            )
            self._push_ui_event(payload, result, thumbnail_jpeg_b64=thumb_s)

        return _handle

    def _notify_relay_failure(self) -> None:
        manager = self._manager
        loop = self._loop
        if manager is None or loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(manager.notify_relay_failure(), loop)

    async def _send_image_request(self, event_id: str) -> None:
        manager = self._manager
        transport = manager.transport if manager else None
        if isinstance(transport, UsbTransport) and self._loop is not None:
            future = asyncio.run_coroutine_threadsafe(
                transport.send_image_request(event_id), self._loop
            )
            await asyncio.wrap_future(future)
            return
        send = getattr(self._client, "send_image_request", None)
        if send is not None:
            result = send(event_id)
            if asyncio.iscoroutine(result):
                await result

    def _push_ui_image(self, event_id: str, image_b64: str) -> None:
        def _update() -> None:
            updater = getattr(self._app, "update_slip_image", None)
            if callable(updater):
                updater(event_id, image_b64)

        self._app.after(0, _update)

    def _on_pending_orders(self, data: dict[str, Any]) -> None:
        if self._orchestrator is not None:
            self._orchestrator.on_pending_orders(data)

    def _emit_withdraw_notify(self, payload: dict[str, Any]) -> None:
        """Schedule withdraw_notify on the ClipSyncClient WS loop (bridge thread-safe)."""
        order_id = str((payload or {}).get("order_id") or "").strip() or "-"
        amount = str((payload or {}).get("amount") or "").strip() or "-"
        schedule = getattr(self._client, "schedule_withdraw_notify", None)
        if callable(schedule):
            ws = getattr(self._client, "ws", None)
            loop = getattr(self._client, "loop", None)
            if not ws or not loop:
                self._app_log(f"WDRAW skip {order_id}: no relay WS")
                return
            schedule(payload)
            self._app_log(f"WDRAW emit {order_id} amount={amount}")
            return
        send = getattr(self._client, "send_withdraw_notify", None)
        if send is None:
            self._app_log(f"WDRAW skip {order_id}: send not available")
            return
        loop = getattr(self._client, "loop", None)
        if loop is not None and loop.is_running():
            result = send(payload)
            if asyncio.iscoroutine(result):
                asyncio.run_coroutine_threadsafe(result, loop)
            self._app_log(f"WDRAW emit {order_id} amount={amount}")
        else:
            self._app_log(f"WDRAW skip {order_id}: client loop not running")

    def _emit_slip_status(self, payload: dict[str, Any]) -> None:
        """Schedule slip_status on the ClipSyncClient WS loop (deduped ~2s)."""
        import time

        job_id = str((payload or {}).get("job_id") or "").strip()
        order_id = str((payload or {}).get("order_id") or "").strip()
        stage = str((payload or {}).get("stage") or "").strip()
        now = time.monotonic()
        key = f"{job_id}|{order_id}|{stage}"
        if not should_emit_slip_status(
            self._last_slip_status_key,
            job_id=job_id,
            order_id=order_id,
            stage=stage,
            now=now,
            last_at=self._last_slip_status_at,
        ):
            return
        schedule = getattr(self._client, "schedule_slip_status", None)
        if callable(schedule):
            ws = getattr(self._client, "ws", None)
            loop = getattr(self._client, "loop", None)
            if not ws or not loop:
                self._app_log(f"SSTAT skip {order_id or job_id}: no relay WS")
                return
            schedule(payload)
            self._last_slip_status_key = key
            self._last_slip_status_at = now
            self._app_log(f"SSTAT emit {order_id or job_id} stage={stage}")
            return
        send = getattr(self._client, "send_slip_status", None)
        if send is None:
            self._app_log(f"SSTAT skip {order_id or job_id}: send not available")
            return
        loop = getattr(self._client, "loop", None)
        if loop is not None and loop.is_running():
            result = send(payload)
            if asyncio.iscoroutine(result):
                asyncio.run_coroutine_threadsafe(result, loop)
            self._last_slip_status_key = key
            self._last_slip_status_at = now
            self._app_log(f"SSTAT emit {order_id or job_id} stage={stage}")
        else:
            self._app_log(f"SSTAT skip {order_id or job_id}: client loop not running")

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
            pending = self._orchestrator._pending_orders if self._orchestrator else []
            order_id = resolve_order_id_for_slip_status(data, pending=pending)
            job_id = event_id or order_id
            if order_id or job_id:
                self._emit_slip_status(
                    build_slip_status_payload(
                        job_id=job_id,
                        order_id=order_id,
                        amount=amount
                        if amount not in (None, "", "-")
                        else data.get("matchKey"),
                        stage="done",
                    )
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
        decision = result.get("decision")
        reason = result.get("reason")
        if decision == "pending_review" and reason:
            amount = payload.get("amount")
            self._app_log(f"Slip รอตรวจ ({amount}): {reason}")

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
