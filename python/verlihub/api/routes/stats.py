"""
Statistics and monitoring API endpoints.

Provides endpoints for hub statistics, geographic distribution,
share statistics, operators, bots, and health checks.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class HubStatistics(BaseModel):
    """Hub statistics response."""
    timestamp: str
    users_online: int
    max_users: int
    operators_online: int
    bots_online: int
    total_share: int
    total_share_formatted: str
    average_share: int
    average_share_formatted: str
    hub_name: str
    uptime_seconds: int
    uptime_formatted: str


class GeoDistribution(BaseModel):
    """Geographic distribution entry."""
    country_code: str
    country_name: str
    users: int
    share: int
    share_formatted: str


class GeoStats(BaseModel):
    """Geographic statistics response."""
    total_countries: int
    distribution: list[GeoDistribution]


class ShareStats(BaseModel):
    """Share statistics response."""
    total: int
    total_formatted: str
    average: int
    average_formatted: str
    median: int
    median_formatted: str
    max: int
    max_formatted: str
    min: int
    min_formatted: str


class OnlineUser(BaseModel):
    """Online user with full details."""
    nick: str
    user_class: int
    class_name: str
    ip: str
    host: str
    country_code: str
    country: str
    city: str
    region: str
    asn: str
    description: str
    tag: str
    email: str
    share: int
    share_formatted: str
    speed: str = ""
    client: str = ""
    client_version: str = ""
    mode: str = ""
    slots: int = 0
    hubs_normal: int = 0
    hubs_registered: int = 0
    hubs_operator: int = 0
    status_flag: int = 0
    supports: str = ""
    login_time: Optional[str] = None
    # Clone detection
    is_clone: bool = False
    clone_group: list[str] = []
    same_ip_users: list[str] = []


class OperatorInfo(BaseModel):
    """Operator information."""
    nick: str
    user_class: int
    class_name: str
    ip: str
    share: int
    share_formatted: str


class BotInfo(BaseModel):
    """Bot information."""
    nick: str
    description: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    timestamp: str
    hub_running: bool
    database_connected: bool
    uptime_seconds: int


# =============================================================================
# Utility Functions
# =============================================================================


def format_bytes(size: int) -> str:
    """Format bytes into human-readable string."""
    size_f = float(size)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if size_f < 1024.0:
            return f"{size_f:.2f} {unit}"
        size_f /= 1024.0
    return f"{size_f:.2f} EB"


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


def get_class_name(user_class: int) -> str:
    """Convert user class number to name."""
    classes = {
        -1: "Disconnected",
        0: "Guest",
        1: "Regular",
        2: "VIP",
        3: "Operator",
        4: "Cheef",
        5: "Admin",
        10: "Master"
    }
    return classes.get(user_class, f"Class{user_class}")


# Country code to name mapping (common codes)
COUNTRY_NAMES = {
    "US": "United States",
    "GB": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "BE": "Belgium",
    "AT": "Austria",
    "CH": "Switzerland",
    "PL": "Poland",
    "CZ": "Czech Republic",
    "SK": "Slovakia",
    "HU": "Hungary",
    "RO": "Romania",
    "BG": "Bulgaria",
    "UA": "Ukraine",
    "RU": "Russia",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "PT": "Portugal",
    "GR": "Greece",
    "TR": "Turkey",
    "IL": "Israel",
    "BR": "Brazil",
    "AR": "Argentina",
    "MX": "Mexico",
    "CA": "Canada",
    "AU": "Australia",
    "NZ": "New Zealand",
    "JP": "Japan",
    "KR": "South Korea",
    "CN": "China",
    "IN": "India",
    "SG": "Singapore",
    "MY": "Malaysia",
    "ID": "Indonesia",
    "TH": "Thailand",
    "VN": "Vietnam",
    "PH": "Philippines",
    "ZA": "South Africa",
    "EG": "Egypt",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
}


def get_country_name(code: str) -> str:
    """Get country name from code."""
    return COUNTRY_NAMES.get(code.upper(), code)


# =============================================================================
# Dependencies
# =============================================================================


def get_hub_context():
    """Get the HubContext."""
    from verlihub.api.deps import get_hub_context as _get_ctx
    ctx = _get_ctx()
    if ctx is None:
        raise HTTPException(status_code=503, detail="Hub not initialized")
    return ctx


# Track hub start time (set when server starts)
_hub_start_time: Optional[float] = None


def get_hub_start_time() -> float:
    """Get hub start time."""
    global _hub_start_time
    if _hub_start_time is None:
        _hub_start_time = time.time()
    return _hub_start_time


def set_hub_start_time(ts: float):
    """Set hub start time."""
    global _hub_start_time
    _hub_start_time = ts


def _get_all_users(ctx) -> list[dict]:
    """Get all online users as list of dicts from the hub context."""
    if hasattr(ctx, 'get_user_list'):
        return ctx.get_user_list()
    # Fallback: nick-only
    nicks = ctx.get_user_nicks() if hasattr(ctx, 'get_user_nicks') else []
    return [{"nick": n, "user_class": 0, "share": 0, "ip": "", "country": ""} for n in nicks]


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/stats", response_model=HubStatistics)
def get_statistics(ctx=Depends(get_hub_context)) -> HubStatistics:
    """Get comprehensive hub statistics."""
    try:
        all_users = _get_all_users(ctx)
        total_share = ctx.total_share if hasattr(ctx, 'total_share') else 0
        max_users = int(ctx.get_config("config", "max_users", "1000"))
        hub_name = ctx.hub_name if hasattr(ctx, 'hub_name') else "Verlihub"
        
        # Count operators (class >= 3)
        op_count = sum(1 for u in all_users if u.get("user_class", 0) >= 3)
        bot_nicks = ctx.get_bot_nicks() if hasattr(ctx, 'get_bot_nicks') else []
        
        user_count = len(all_users)
        avg_share = total_share // user_count if user_count > 0 else 0
        
        uptime = int(time.time() - get_hub_start_time())
        
        return HubStatistics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            users_online=user_count,
            max_users=max_users,
            operators_online=op_count,
            bots_online=len(bot_nicks),
            total_share=total_share,
            total_share_formatted=format_bytes(total_share),
            average_share=avg_share,
            average_share_formatted=format_bytes(avg_share),
            hub_name=hub_name,
            uptime_seconds=uptime,
            uptime_formatted=format_uptime(uptime),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/geo", response_model=GeoStats)
def get_geographic_stats(ctx=Depends(get_hub_context)) -> GeoStats:
    """Get geographic distribution of users."""
    try:
        all_users = _get_all_users(ctx)
        
        # Aggregate by country
        country_data: dict[str, dict[str, Any]] = {}
        
        for u in all_users:
            cc = u.get("country", "")
            share = u.get("share", 0)
            
            if cc and cc != "--":
                if cc not in country_data:
                    country_data[cc] = {"users": 0, "share": 0}
                country_data[cc]["users"] += 1
                country_data[cc]["share"] += share
        
        # Sort by user count descending
        distribution = sorted(
            [
                GeoDistribution(
                    country_code=cc,
                    country_name=get_country_name(cc),
                    users=data["users"],
                    share=data["share"],
                    share_formatted=format_bytes(data["share"]),
                )
                for cc, data in country_data.items()
            ],
            key=lambda x: x.users,
            reverse=True,
        )
        
        return GeoStats(
            total_countries=len(distribution),
            distribution=distribution,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/share", response_model=ShareStats)
def get_share_stats(ctx=Depends(get_hub_context)) -> ShareStats:
    """Get share size statistics."""
    try:
        all_users = _get_all_users(ctx)
        
        shares = [u.get("share", 0) for u in all_users]
        
        if not shares:
            return ShareStats(
                total=0,
                total_formatted="0 B",
                average=0,
                average_formatted="0 B",
                median=0,
                median_formatted="0 B",
                max=0,
                max_formatted="0 B",
                min=0,
                min_formatted="0 B",
            )
        
        total = sum(shares)
        avg = total // len(shares)
        sorted_shares = sorted(shares)
        median = sorted_shares[len(sorted_shares) // 2]
        max_share = max(shares)
        min_share = min(shares)
        
        return ShareStats(
            total=total,
            total_formatted=format_bytes(total),
            average=avg,
            average_formatted=format_bytes(avg),
            median=median,
            median_formatted=format_bytes(median),
            max=max_share,
            max_formatted=format_bytes(max_share),
            min=min_share,
            min_formatted=format_bytes(min_share),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ops", response_model=list[OperatorInfo])
def get_operators(ctx=Depends(get_hub_context)) -> list[OperatorInfo]:
    """Get list of online operators (class >= 3)."""
    try:
        all_users = _get_all_users(ctx)
        
        operators = []
        for u in all_users:
            user_class = u.get("user_class", 0)
            if user_class >= 3:
                operators.append(OperatorInfo(
                    nick=u.get("nick", ""),
                    user_class=user_class,
                    class_name=get_class_name(user_class),
                    ip=u.get("ip", ""),
                    share=u.get("share", 0),
                    share_formatted=format_bytes(u.get("share", 0)),
                ))
        
        return operators
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bots", response_model=list[BotInfo])
def get_bots(ctx=Depends(get_hub_context)) -> list[BotInfo]:
    """Get list of hub bots."""
    try:
        bot_nicks = ctx.get_bot_nicks() if hasattr(ctx, 'get_bot_nicks') else []
        
        bots = []
        for nick in bot_nicks:
            try:
                desc = ctx.get_bot_description(nick) if hasattr(ctx, 'get_bot_description') else ""
                bots.append(BotInfo(nick=nick, description=desc))
            except Exception:
                bots.append(BotInfo(nick=nick, description=""))
        
        return bots
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=HealthResponse)
def health_check(ctx=Depends(get_hub_context)) -> HealthResponse:
    """Health check endpoint for monitoring."""
    try:
        hub_running = ctx.is_running if hasattr(ctx, 'is_running') else False
    except Exception:
        hub_running = False
    
    # Check database
    db_connected = False
    try:
        from verlihub.models.database import get_database
        db = get_database()
        db_connected = db is not None
    except Exception:
        pass
    
    uptime = int(time.time() - get_hub_start_time())
    
    return HealthResponse(
        status="healthy" if hub_running else "degraded",
        timestamp=datetime.now(timezone.utc).isoformat(),
        hub_running=hub_running,
        database_connected=db_connected,
        uptime_seconds=uptime,
    )


@router.get("/users/detailed", response_model=list[OnlineUser])
def get_detailed_users(
    ctx=Depends(get_hub_context),
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[OnlineUser]:
    """Get detailed list of online users with geo info and clone detection."""
    try:
        # Use get_user_list() which returns full user info dicts
        raw_users = ctx.get_user_list() if hasattr(ctx, 'get_user_list') else []
        
        users = []
        ip_share_groups: dict[str, list[str]] = {}  # "ip:share" -> [nicks]
        ip_groups: dict[str, list[str]] = {}  # ip -> [nicks]
        
        # First pass: gather all users
        user_data = []
        for u in raw_users:
            try:
                nick = u.get("nick", "")
                user_class = u.get("user_class", 0)
                ip = u.get("ip", "")
                share = u.get("share", 0)
                host = u.get("host", "")
                cc = u.get("country", "")
                
                # Geo info from C++ core GeoIP
                country_name = u.get("country_name", "")
                city = u.get("city", "")
                region = u.get("region", "")
                asn = u.get("asn", "")
                
                # User description/tag/speed/email from C++ core
                desc = u.get("description", "")
                tag = u.get("tag", "")
                email = u.get("email", "")
                speed = u.get("speed", "")
                client = u.get("client", "")
                client_version = u.get("client_version", "")
                mode = u.get("mode", "")
                slots = u.get("slots", 0)
                hubs_normal = u.get("hubs_normal", 0)
                hubs_registered = u.get("hubs_registered", 0)
                hubs_operator = u.get("hubs_operator", 0)
                status_flag = u.get("status_flag", 0)
                supports = u.get("supports", "")
                
                # Track for clone detection
                clone_key = f"{ip}:{share}"
                if clone_key not in ip_share_groups:
                    ip_share_groups[clone_key] = []
                ip_share_groups[clone_key].append(nick)
                
                if ip not in ip_groups:
                    ip_groups[ip] = []
                ip_groups[ip].append(nick)
                
                user_data.append({
                    "nick": nick,
                    "user_class": user_class,
                    "class_name": get_class_name(user_class),
                    "ip": ip,
                    "host": host,
                    "country_code": cc,
                    "country": country_name or get_country_name(cc) if cc else "",
                    "city": city,
                    "region": region,
                    "asn": asn,
                    "description": desc,
                    "tag": tag,
                    "email": email,
                    "speed": speed,
                    "client": client,
                    "client_version": client_version,
                    "mode": mode,
                    "slots": slots,
                    "hubs_normal": hubs_normal,
                    "hubs_registered": hubs_registered,
                    "hubs_operator": hubs_operator,
                    "status_flag": status_flag,
                    "supports": supports,
                    "share": share,
                    "share_formatted": format_bytes(share),
                    "clone_key": clone_key,
                })
            except Exception:
                pass
        
        # Second pass: add clone detection
        for data in user_data:
            clone_key = data["clone_key"]
            ip = data["ip"]
            nick = data["nick"]
            
            clone_group = [n for n in ip_share_groups.get(clone_key, []) if n != nick]
            same_ip = [n for n in ip_groups.get(ip, []) if n != nick]
            
            users.append(OnlineUser(
                nick=data["nick"],
                user_class=data["user_class"],
                class_name=data["class_name"],
                ip=data["ip"],
                host=data["host"],
                country_code=data["country_code"],
                country=data["country"],
                city=data["city"],
                region=data["region"],
                asn=data["asn"],
                description=data["description"],
                tag=data["tag"],
                email=data["email"],
                speed=data.get("speed", ""),
                client=data.get("client", ""),
                client_version=data.get("client_version", ""),
                mode=data.get("mode", ""),
                slots=data.get("slots", 0),
                hubs_normal=data.get("hubs_normal", 0),
                hubs_registered=data.get("hubs_registered", 0),
                hubs_operator=data.get("hubs_operator", 0),
                status_flag=data.get("status_flag", 0),
                supports=data.get("supports", ""),
                share=data["share"],
                share_formatted=data["share_formatted"],
                is_clone=len(clone_group) > 0,
                clone_group=clone_group,
                same_ip_users=same_ip,
            ))
        
        # Apply pagination
        if limit:
            users = users[offset:offset + limit]
        elif offset:
            users = users[offset:]
        
        return users
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
