"""Tkinter UI panels for slip auto-confirm (debug / settings / audit)."""

from __future__ import annotations

from clipsync.ui.audit_viewer import AuditViewer, filter_audit_records, load_audit_records
from clipsync.ui.debug_panel import DebugPanel, format_slip_row, status_tag_for
from clipsync.ui.settings_panel import SettingsPanel, transport_indicator

__all__ = [
    "AuditViewer",
    "DebugPanel",
    "SettingsPanel",
    "filter_audit_records",
    "format_slip_row",
    "load_audit_records",
    "status_tag_for",
    "transport_indicator",
]
