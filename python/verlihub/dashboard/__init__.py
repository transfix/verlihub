"""
Web Dashboard for Verlihub.

This module provides a web-based admin dashboard using:
- FastAPI for routing
- Jinja2 for templating
- Bulma CSS for styling
- WebSocket for real-time updates
"""
from __future__ import annotations

from verlihub.dashboard.routes import dashboard_router
from verlihub.dashboard.websocket import (
    ws_router,
    emit_hub_event,
    emit_log,
    broadcast_hub_event,
    broadcast_log,
)

__all__ = [
    "dashboard_router",
    "ws_router",
    "emit_hub_event",
    "emit_log",
    "broadcast_hub_event",
    "broadcast_log",
]
