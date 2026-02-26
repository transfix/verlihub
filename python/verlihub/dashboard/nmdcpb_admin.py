"""
NMDCpb Relay Admin API
======================

REST endpoints and WebSocket channel for monitoring and managing
NMDCpb relay sessions, E2EPM statistics, and hub plugin state.

Mounted at ``/dashboard/nmdcpb/`` via the dashboard router.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from verlihub.dashboard.routes import get_user_from_cookie, get_base_context, templates
from verlihub.api.auth import TokenData

# Import hub plugin state (module-level globals)
from verlihub.client.nmdcpb import hub_plugin

nmdcpb_router = APIRouter(prefix="/nmdcpb", tags=["nmdcpb-admin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_admin(user: Optional[TokenData]) -> bool:
    """Check user has admin access (class >= 5)."""
    return user is not None and user.user_class >= 5


def _format_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _relay_session_dict(rid: int, sess: hub_plugin._RelaySession) -> dict:
    """Convert a _RelaySession to a JSON-friendly dict."""
    now = time.time()
    return {
        "relay_id": rid,
        "user_a": sess.user_a,
        "user_b": sess.user_b,
        "token": sess.token,
        "bytes_forwarded": sess.bytes_forwarded,
        "bytes_forwarded_human": _format_bytes(sess.bytes_forwarded),
        "created_at": sess.created_at,
        "age_seconds": int(now - sess.created_at),
        "last_activity": sess.last_activity,
        "idle_seconds": int(now - sess.last_activity),
        "is_idle": sess.is_idle(now),
    }


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@nmdcpb_router.get("/api/stats")
async def get_nmdcpb_stats(
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Get NMDCpb plugin statistics."""
    if not _require_admin(user):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    stats = dict(hub_plugin._stats)
    stats["pb_users_count"] = len(hub_plugin._pb_users)
    stats["active_relay_sessions"] = len(hub_plugin._relay_sessions)
    stats["pending_relay_requests"] = len(hub_plugin._pending_relay)
    stats["version"] = hub_plugin.VERSION
    stats["config"] = {
        "enable_legacy_translation": hub_plugin.ENABLE_LEGACY_TRANSLATION,
        "enable_hubrelay": hub_plugin.ENABLE_HUBRELAY,
        "enable_e2epm_forward": hub_plugin.ENABLE_E2EPM_FORWARD,
        "enable_stealth_search": hub_plugin.ENABLE_STEALTH_SEARCH,
        "max_pb_size": hub_plugin.MAX_PB_SIZE,
        "rate_max_messages": hub_plugin.RATE_MAX_MESSAGES,
        "rate_max_e2epm": hub_plugin.RATE_MAX_E2EPM,
        "relay_max_sessions_per_user": hub_plugin.RELAY_MAX_SESSIONS_PER_USER,
        "relay_max_sessions_total": hub_plugin.RELAY_MAX_SESSIONS_TOTAL,
        "relay_idle_timeout_sec": hub_plugin.RELAY_IDLE_TIMEOUT_SEC,
        "relay_max_payload": hub_plugin.RELAY_MAX_PAYLOAD,
    }
    return JSONResponse(stats)


@nmdcpb_router.get("/api/users")
async def get_nmdcpb_users(
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Get list of NMDCpb-capable users currently online."""
    if not _require_admin(user):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    users = []
    for nick, features in hub_plugin._pb_users.items():
        relay_count = hub_plugin._user_relay_count(nick)
        users.append({
            "nick": nick,
            "features": sorted(features),
            "active_relays": relay_count,
        })
    return JSONResponse({"users": users, "total": len(users)})


@nmdcpb_router.get("/api/relays")
async def get_relay_sessions(
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Get all active relay sessions."""
    if not _require_admin(user):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    sessions = []
    for rid, sess in hub_plugin._relay_sessions.items():
        sessions.append(_relay_session_dict(rid, sess))

    pending = []
    for token, pend in hub_plugin._pending_relay.items():
        pending.append({
            "token": token,
            "from_nick": pend.get("from_nick", ""),
            "to_nick": pend.get("to_nick", ""),
            "purpose": pend.get("purpose", ""),
            "created_at": pend.get("created_at", 0),
        })

    return JSONResponse({
        "sessions": sessions,
        "pending": pending,
        "total_active": len(sessions),
        "total_pending": len(pending),
    })


@nmdcpb_router.get("/api/relay/{relay_id}")
async def get_relay_session(
    relay_id: int,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Get details of a specific relay session."""
    if not _require_admin(user):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    sess = hub_plugin._relay_sessions.get(relay_id)
    if not sess:
        return JSONResponse({"error": f"Relay session {relay_id} not found"}, status_code=404)

    return JSONResponse(_relay_session_dict(relay_id, sess))


@nmdcpb_router.post("/api/relay/{relay_id}/close")
async def close_relay_session(
    relay_id: int,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Force-close a relay session (admin action)."""
    if not _require_admin(user):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    sess = hub_plugin._relay_sessions.get(relay_id)
    if not sess:
        return JSONResponse({"error": f"Relay session {relay_id} not found"}, status_code=404)

    hub_plugin._close_relay_session(relay_id, reason=3, notify=True)  # HUB_LIMIT
    return JSONResponse({
        "status": "closed",
        "relay_id": relay_id,
        "user_a": sess.user_a,
        "user_b": sess.user_b,
    })


@nmdcpb_router.post("/api/relay/close-all")
async def close_all_relay_sessions(
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Force-close all active relay sessions."""
    if not _require_admin(user):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    relay_ids = list(hub_plugin._relay_sessions.keys())
    for rid in relay_ids:
        hub_plugin._close_relay_session(rid, reason=3, notify=True)

    return JSONResponse({"status": "all_closed", "count": len(relay_ids)})


@nmdcpb_router.post("/api/relay/close-user/{nick}")
async def close_user_relay_sessions(
    nick: str,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Force-close all relay sessions for a specific user."""
    if not _require_admin(user):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    relay_ids = [
        rid for rid, sess in hub_plugin._relay_sessions.items()
        if sess.touches(nick)
    ]
    for rid in relay_ids:
        hub_plugin._close_relay_session(rid, reason=3, notify=True)

    return JSONResponse({
        "status": "user_sessions_closed",
        "nick": nick,
        "count": len(relay_ids),
    })


# ---------------------------------------------------------------------------
# Dashboard HTML page
# ---------------------------------------------------------------------------

@nmdcpb_router.get("/", response_class=HTMLResponse)
async def nmdcpb_dashboard(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """NMDCpb relay admin dashboard page."""
    if user is None:
        from fastapi import status as http_status
        from fastapi.responses import RedirectResponse
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/nmdcpb/",
            status_code=http_status.HTTP_303_SEE_OTHER,
        )
    if not _require_admin(user):
        return HTMLResponse("<h1>403 — Admin access required</h1>", status_code=403)

    context = get_base_context(request, user)
    context["page_title"] = "NMDCpb Relay Admin"
    return templates.TemplateResponse(request, "nmdcpb_relay.html", context)
