"""
Hub status and control API endpoints.
"""
from __future__ import annotations

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


# =============================================================================
# Endpoints
# =============================================================================


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
