"""
System log API endpoints.

Provides REST endpoints to read and clear the in-memory log buffer,
complementing the real-time WebSocket stream at ``/ws/logs``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from verlihub.api.auth import Permission, require_permission
from verlihub.log_buffer import get_log_buffer

router = APIRouter()


# =========================================================================
# Response models
# =========================================================================


class LogEntryOut(BaseModel):
    type: str = "log"
    level: str
    message: str
    log_type: str
    time: str


class LogsResponse(BaseModel):
    """Paginated log response."""
    entries: list[LogEntryOut]
    total: int
    returned: int


class InjectEntry(BaseModel):
    """A single log entry to inject into the buffer."""
    level: str = "info"
    message: str
    log_type: str = "system"


class InjectRequest(BaseModel):
    """Request body for injecting log entries (testing/diagnostics)."""
    entries: list[InjectEntry]


class InjectResponse(BaseModel):
    added: int
    total: int


class ClearResponse(BaseModel):
    cleared: int
    message: str


# =========================================================================
# Endpoints
# =========================================================================


@router.get("", response_model=LogsResponse)
async def get_logs(
    limit: int = Query(default=500, ge=1, le=5000, description="Max entries to return"),
    _user=Depends(require_permission(Permission.ADMIN)),
):
    """
    Get recent log entries from the in-memory ring buffer.

    Returns the most recent *limit* entries (oldest first).
    """
    buf = get_log_buffer()
    entries = buf.get_recent(limit)
    return LogsResponse(
        entries=entries,
        total=len(buf),
        returned=len(entries),
    )


@router.delete("", response_model=ClearResponse)
async def clear_logs(
    _user=Depends(require_permission(Permission.ADMIN)),
):
    """Clear all log entries from the in-memory buffer."""
    buf = get_log_buffer()
    cleared = buf.clear()
    return ClearResponse(cleared=cleared, message=f"Cleared {cleared} log entries")


@router.post("", response_model=InjectResponse)
async def inject_logs(
    body: InjectRequest,
    _user=Depends(require_permission(Permission.ADMIN)),
):
    """Inject log entries into the in-memory ring buffer.

    Useful for diagnostics and testing.  Each entry is stored in the buffer
    and broadcast to connected WebSocket clients.
    """
    buf = get_log_buffer()
    for entry in body.entries:
        buf.add(level=entry.level, message=entry.message, log_type=entry.log_type)
    return InjectResponse(added=len(body.entries), total=len(buf))
