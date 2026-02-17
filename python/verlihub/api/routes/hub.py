"""
Hub status and control API endpoints.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from verlihub.api.auth import (
    Permission,
    RequireAdmin,
    RequireMaster,
    TokenData,
    get_current_user_optional,
    require_permission,
)

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class HubStatus(BaseModel):
    """Hub status response."""
    is_running: bool
    user_count: int
    operator_count: int
    total_share: int  # bytes
    total_share_gb: float
    hub_name: str
    hub_topic: str
    uptime_seconds: int


class HubInfo(BaseModel):
    """Full hub information."""
    name: str
    description: str
    host: str
    topic: str
    motd: str
    max_users: int
    version: str
    icon_url: str
    logo_url: str
    uptime_seconds: int
    uptime_formatted: str
    hub_encoding: str
    hub_owner: str
    listen_port: int
    tls_enabled: bool


class HubConfig(BaseModel):
    """Hub configuration response."""
    hub_name: str
    hub_desc: str
    hub_topic: str
    hub_host: str
    hub_owner: str
    hub_encoding: str
    listen_port: int
    max_users: int
    min_share: int  # bytes
    tls_enabled: bool


class HubTopicUpdate(BaseModel):
    """Request to update hub topic."""
    topic: str


class BroadcastRequest(BaseModel):
    """Request to broadcast a message."""
    message: str
    min_class: Optional[int] = None
    max_class: Optional[int] = None


# =============================================================================
# Utility Functions
# =============================================================================


def format_uptime(seconds: int) -> str:
    """Format uptime in human-readable format."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    
    return " ".join(parts)


# =============================================================================
# Dependency to get HubContext
# =============================================================================


def get_hub_context():
    """
    FastAPI dependency to get the HubContext.
    
    This should be set up during application startup.
    """
    from verlihub.api.deps import get_hub_context as _get_ctx
    ctx = _get_ctx()
    if ctx is None:
        raise HTTPException(status_code=503, detail="Hub not initialized")
    return ctx


# Track hub start time
_hub_start_time: Optional[float] = None


def get_hub_start_time() -> float:
    """Get hub start time."""
    global _hub_start_time
    if _hub_start_time is None:
        _hub_start_time = time.time()
    return _hub_start_time


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/info", response_model=HubInfo)
async def get_hub_info(ctx=Depends(get_hub_context)) -> HubInfo:
    """Get full hub information including uptime and MOTD."""
    try:
        hub_name = ctx.get_config("config", "hub_name", "Verlihub")
        hub_desc = ctx.get_config("config", "hub_desc", "DC++ Hub")
        hub_host = ctx.get_config("config", "hub_host", "")
        hub_owner = ctx.get_config("config", "hub_owner", "")
        hub_encoding = ctx.get_config("config", "hub_encoding", "CP1252")
        max_users = int(ctx.get_config("config", "max_users", "1000"))
        listen_port = int(ctx.get_config("config", "listen_port", "411"))
        tls_enabled = ctx.get_config("config", "tls_enabled", "0") == "1"
        icon_url = ctx.get_config("config", "hub_icon_url", "")
        logo_url = ctx.get_config("config", "hub_logo_url", "")
        version = ctx.get_config("config", "hub_version", "Verlihub")
        
        topic = ctx.hub_topic if hasattr(ctx, 'hub_topic') else ""
        
        # Read MOTD file
        motd = ""
        config_dir = os.getenv("VH_CONFIG_DIR", "/etc/verlihub")
        motd_file = Path(config_dir) / "motd"
        if motd_file.exists():
            try:
                motd = motd_file.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                pass
        
        uptime = int(time.time() - get_hub_start_time())
        
        return HubInfo(
            name=hub_name,
            description=hub_desc,
            host=hub_host,
            topic=topic,
            motd=motd,
            max_users=max_users,
            version=version,
            icon_url=icon_url,
            logo_url=logo_url,
            uptime_seconds=uptime,
            uptime_formatted=format_uptime(uptime),
            hub_encoding=hub_encoding,
            hub_owner=hub_owner,
            listen_port=listen_port,
            tls_enabled=tls_enabled,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=HubStatus)
async def get_hub_status(ctx=Depends(get_hub_context)) -> HubStatus:
    """Get current hub status."""
    total_share = ctx.total_share
    return HubStatus(
        is_running=ctx.is_running,
        user_count=ctx.user_count,
        operator_count=0,  # TODO: implement
        total_share=total_share,
        total_share_gb=total_share / (1024 ** 3),
        hub_name=ctx.hub_name,
        hub_topic=ctx.hub_topic,
        uptime_seconds=0,  # TODO: implement
    )


@router.get("/config", response_model=HubConfig)
async def get_hub_config(ctx=Depends(get_hub_context)) -> HubConfig:
    """Get hub configuration."""
    return HubConfig(
        hub_name=ctx.get_config("config", "hub_name"),
        hub_desc=ctx.get_config("config", "hub_desc"),
        hub_topic=ctx.hub_topic,
        hub_host=ctx.get_config("config", "hub_host"),
        hub_owner=ctx.get_config("config", "hub_owner"),
        hub_encoding=ctx.get_config("config", "hub_encoding", "CP1252"),
        listen_port=int(ctx.get_config("config", "listen_port", "411")),
        max_users=int(ctx.get_config("config", "max_users", "1000")),
        min_share=int(ctx.get_config("config", "min_share", "0")),
        tls_enabled=ctx.get_config("config", "tls_enabled", "0") == "1",
    )


@router.put("/topic")
async def set_hub_topic(
    request: HubTopicUpdate,
    ctx=Depends(get_hub_context),
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
) -> dict:
    """Set the hub topic. Requires OPERATOR (3) permission."""
    ctx.hub_topic = request.topic
    return {"success": True, "topic": request.topic}


@router.post("/broadcast")
async def broadcast_message(
    request: BroadcastRequest,
    ctx=Depends(get_hub_context),
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
) -> dict:
    """Broadcast a message to users. Requires OPERATOR (3) permission."""
    if request.min_class is not None and request.max_class is not None:
        success = ctx.send_to_class(
            request.message, 
            request.min_class, 
            request.max_class
        )
    else:
        success = ctx.send_to_all(request.message)
    
    return {"success": success}


@router.post("/shutdown")
async def shutdown_hub(
    ctx=Depends(get_hub_context),
    _user: RequireMaster = None,
) -> dict:
    """Request hub shutdown. Requires MASTER (10) permission."""
    ctx.request_shutdown(0)
    return {"success": True, "message": "Shutdown requested"}


@router.post("/reload")
async def reload_config(
    ctx=Depends(get_hub_context),
    _user: RequireAdmin = None,
) -> dict:
    """Request configuration reload. Requires ADMIN (5) permission."""
    ctx.cpp.RequestReload()
    return {"success": True, "message": "Reload requested"}
