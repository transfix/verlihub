"""
Hub status and control API endpoints.
"""
from __future__ import annotations

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
def get_hub_info(ctx=Depends(get_hub_context)) -> HubInfo:
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
        from verlihub.config import get_config_optional
        cfg = get_config_optional()
        config_dir = cfg._config_dir if cfg else "/etc/verlihub"
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
def get_hub_status(ctx=Depends(get_hub_context)) -> HubStatus:
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
        uptime_seconds=ctx.uptime,
    )


@router.get("/config", response_model=HubConfig)
def get_hub_config(ctx=Depends(get_hub_context)) -> HubConfig:
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


class HubConfigUpdate(BaseModel):
    """Request to update hub configuration."""
    # General
    hub_name: Optional[str] = None
    hub_desc: Optional[str] = None
    hub_topic: Optional[str] = None
    hub_host: Optional[str] = None
    hub_owner: Optional[str] = None
    hub_encoding: Optional[str] = None
    hub_category: Optional[str] = None
    # Network — UI sends "port" and "enable_tls"; accept both names
    listen_port: Optional[int] = None
    port: Optional[int] = None          # alias for listen_port (UI data-key)
    listen_ip: Optional[str] = None
    tls_enabled: Optional[bool] = None
    enable_tls: Optional[bool] = None   # alias for tls_enabled (UI data-key)
    use_regserver: Optional[bool] = None
    regserver_host: Optional[str] = None
    hublist_servers: Optional[list[str]] = None
    hublist_server_enabled: Optional[bool] = None
    # Limits
    max_users: Optional[int] = None
    min_share: Optional[int] = None
    min_slots: Optional[int] = None
    max_hubs_user: Optional[int] = None
    max_hubs_op: Optional[int] = None
    max_conn_per_ip: Optional[int] = None
    # Security settings
    allow_unregistered: Optional[bool] = None
    require_password: Optional[bool] = None
    login_timeout: Optional[int] = None
    max_pass_attempts: Optional[int] = None
    flood_protection: Optional[int] = None
    chat_filter: Optional[bool] = None
    anti_clone: Optional[bool] = None
    registration_require_invite: Optional[bool] = None
    # Messages
    hub_motd: Optional[str] = None
    hub_security: Optional[str] = None
    opchat_name: Optional[str] = None


@router.put("/config")
def update_hub_config(
    request: HubConfigUpdate,
    ctx=Depends(get_hub_context),
    _user: TokenData = Depends(require_permission(Permission.ADMIN)),
) -> dict:
    """Update hub configuration. Requires ADMIN (5) permission."""
    updated = {}

    # Resolve UI alias names: "port" → "listen_port", "enable_tls" → "tls_enabled"
    effective_listen_port = request.listen_port if request.listen_port is not None else request.port
    effective_tls = request.tls_enabled if request.tls_enabled is not None else request.enable_tls

    field_map = {
        "hub_name": ("config", "hub_name"),
        "hub_desc": ("config", "hub_desc"),
        "hub_host": ("config", "hub_host"),
        "hub_owner": ("config", "hub_owner"),
        "hub_encoding": ("config", "hub_encoding"),
        "hub_category": ("config", "hub_category"),
        "hub_security": ("config", "hub_security"),
        "opchat_name": ("config", "opchat_name"),
        "listen_ip": ("config", "listen_ip"),
        "regserver_host": ("config", "regserver_host"),
        "max_users": ("config", "max_users"),
        "min_share": ("config", "min_share"),
        "min_slots": ("config", "min_slots"),
        "max_hubs_user": ("config", "max_hubs_user"),
        "max_hubs_op": ("config", "max_hubs_op"),
        "max_conn_per_ip": ("config", "max_conn_per_ip"),
        "login_timeout": ("config", "login_timeout"),
        "max_pass_attempts": ("config", "max_pass_attempts"),
        "flood_protection": ("config", "flood_protection"),
    }

    # Boolean fields that need "0"/"1" conversion
    bool_field_map = {
        "allow_unregistered": ("config", "allow_unregistered"),
        "require_password": ("config", "require_password"),
        "chat_filter": ("config", "chat_filter"),
        "anti_clone": ("config", "anti_clone"),
        "registration_require_invite": ("config", "registration_require_invite"),
        "use_regserver": ("config", "use_regserver"),
    }

    for field, (section, key) in field_map.items():
        value = getattr(request, field, None)
        if value is not None:
            ctx.set_config(section, key, str(value))
            updated[field] = value

    for field, (section, key) in bool_field_map.items():
        value = getattr(request, field, None)
        if value is not None:
            ctx.set_config(section, key, "1" if value else "0")
            updated[field] = value

    # Aliased fields: listen_port / port, tls_enabled / enable_tls
    if effective_listen_port is not None:
        ctx.set_config("config", "listen_port", str(effective_listen_port))
        updated["listen_port"] = effective_listen_port

    if effective_tls is not None:
        ctx.set_config("config", "tls_enabled", "1" if effective_tls else "0")
        updated["tls_enabled"] = effective_tls

    # Hub topic — set directly on the context property
    if request.hub_topic is not None:
        ctx.hub_topic = request.hub_topic
        updated["hub_topic"] = request.hub_topic

    # MOTD — stored as a file, not a C++ config key
    if request.hub_motd is not None:
        try:
            from verlihub.config import get_config_optional
            cfg = get_config_optional()
            config_dir = cfg._config_dir if cfg else "/etc/verlihub"
            motd_file = Path(config_dir) / "motd"
            motd_file.write_text(request.hub_motd, encoding="utf-8")
            updated["hub_motd"] = request.hub_motd
            # Push to live server so new connections see it immediately
            try:
                ctx.set_motd(request.hub_motd)
            except AttributeError:
                pass  # SWIG wrapper may not expose SetMOTD yet
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to write MOTD file: %s", exc)

    # Sync registration_require_invite to the Python config singleton
    if request.registration_require_invite is not None:
        try:
            from verlihub.config import get_config_optional
            cfg = get_config_optional()
            if cfg is not None:
                cfg.api.registration_require_invite = request.registration_require_invite
        except Exception:
            pass

    # Hublist servers (Python config — list of external hublist servers)
    if request.hublist_servers is not None:
        try:
            from verlihub.config import get_config_optional
            cfg = get_config_optional()
            if cfg is not None:
                cfg.hub.hublist_servers = request.hublist_servers
                updated["hublist_servers"] = request.hublist_servers
        except Exception:
            pass

    # Hublist server enabled (Python config — run built-in hublist directory)
    if request.hublist_server_enabled is not None:
        try:
            from verlihub.config import get_config_optional
            cfg = get_config_optional()
            if cfg is not None:
                cfg.hublist.server_enabled = request.hublist_server_enabled
                updated["hublist_server_enabled"] = request.hublist_server_enabled
        except Exception:
            pass

    return {"success": True, "updated": updated}


@router.put("/topic")
def set_hub_topic(
    request: HubTopicUpdate,
    ctx=Depends(get_hub_context),
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
) -> dict:
    """Set the hub topic. Requires OPERATOR (3) permission."""
    ctx.hub_topic = request.topic
    return {"success": True, "topic": request.topic}


class ChatMessageRequest(BaseModel):
    """Request to send a chat message."""
    message: str


@router.post("/chat")
def send_chat_message(
    request: ChatMessageRequest,
    ctx=Depends(get_hub_context),
    user: TokenData = Depends(require_permission(Permission.OPERATOR)),
) -> dict:
    """Send a chat message as the authenticated user. Requires OPERATOR (3)."""
    try:
        success = ctx.send_chat_as(user.nick, request.message)
    except AttributeError:
        # Fallback if send_chat_as not available
        success = ctx.send_to_all(f"<{user.nick}> {request.message}")

    if success:
        # Broadcast via WebSocket so all dashboard users see it.
        # The C++ SendChatToAll only sends to DC clients, it does NOT
        # trigger the OnChatMessage callback, so we must emit manually.
        from verlihub.dashboard.websocket import emit_hub_event
        emit_hub_event("chat", {
            "nick": user.nick,
            "message": request.message,
            "user_class": user.user_class,
        })

        # Dispatch the chat_message event so the bot handler sees it too.
        # Without this, messages sent from the web dashboard never reach
        # the bot's _on_chat callback.
        try:
            ctx.events.OnChatMessage(user.nick, request.message)
        except Exception:
            pass  # best-effort — don't fail the REST response

    return {"success": success}


@router.post("/broadcast")
def broadcast_message(
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


class HubStartRequest(BaseModel):
    """Hub start request."""
    port: int = 0        # 0 = use config / VH_PORT
    listen_ip: str = ""  # empty = use config / VH_LISTEN_IP


@router.post("/start")
def start_hub(
    request: HubStartRequest = HubStartRequest(),
    _user: RequireAdmin = None,
) -> dict:
    """
    Start the hub if it is not already running.
    
    Requires ADMIN (5) permission.
    
    This endpoint is meaningful when VH_AUTO_START is not set and the
    hub was only initialized (not started) during application startup.
    """
    from verlihub.api.deps import get_hub_context
    ctx = get_hub_context()
    if ctx is None:
        raise HTTPException(status_code=503, detail="Hub context not initialized")
    if ctx.is_running:
        return {"success": True, "message": "Hub is already running"}
    
    from verlihub.config import get_config_optional
    cfg = get_config_optional()
    port = request.port or (cfg.hub.port if cfg else 411)
    listen_ip = request.listen_ip or (cfg.hub.listen_host if cfg else "0.0.0.0")
    
    if ctx.start(port, listen_ip):
        return {"success": True, "message": f"Hub started on {listen_ip}:{port}"}
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start hub on port {port} — is another instance already running on that port?",
        )


@router.post("/shutdown")
def shutdown_hub(
    ctx=Depends(get_hub_context),
    _user: RequireMaster = None,
) -> dict:
    """Request hub shutdown. Requires MASTER (10) permission."""
    ctx.request_shutdown(0)
    return {"success": True, "message": "Shutdown requested"}


@router.post("/reload")
def reload_config(
    ctx=Depends(get_hub_context),
    _user: RequireAdmin = None,
) -> dict:
    """Request configuration reload. Requires ADMIN (5) permission."""
    ctx.cpp.RequestReload()
    return {"success": True, "message": "Reload requested"}


# =============================================================================
# Enriched statistics endpoints (geo distribution, share stats)
# =============================================================================


@router.get("/geo-stats")
def get_geo_stats(ctx=Depends(get_hub_context)) -> dict:
    """Get geographic distribution of online users."""
    from verlihub.enrichment import enrich_user_list, compute_geo_distribution

    users = ctx.get_user_list()
    enriched = enrich_user_list(users, fetch_geo=True, fetch_hostnames=False)
    distribution = compute_geo_distribution(enriched)
    return {
        "total_countries": len(distribution),
        "total_users": len(users),
        "distribution": distribution,
    }


@router.get("/share-stats")
def get_share_stats(ctx=Depends(get_hub_context)) -> dict:
    """Get share size statistics for online users."""
    from verlihub.enrichment import compute_share_stats

    users = ctx.get_user_list()
    stats = compute_share_stats(users)
    return {
        "total_bytes": stats.total_bytes,
        "total_formatted": stats.total_formatted,
        "user_count": stats.user_count,
        "average_bytes": stats.average_bytes,
        "average_formatted": stats.average_formatted,
        "median_bytes": stats.median_bytes,
        "median_formatted": stats.median_formatted,
        "max_bytes": stats.max_bytes,
        "max_formatted": stats.max_formatted,
        "max_nick": stats.max_nick,
        "zero_share_count": stats.zero_share_count,
    }
