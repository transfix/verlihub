"""
Dashboard routes for the Verlihub web interface.

Provides HTML pages for:
- Login/authentication
- Main dashboard with hub status
- User management
- Ban management
- Configuration
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response, HTTPException, status, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from verlihub.api.auth import decode_token, TokenData
from verlihub.api.deps import get_hub_context
from verlihub.config import get_config_optional
from verlihub.models import RegUser

# Default Verlihub logo (GitHub avatar)
VERLIHUB_DEFAULT_LOGO = "https://avatars1.githubusercontent.com/u/1856420?v=3&s=300"

# Setup templates directory
DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Create router
dashboard_router = APIRouter()


async def get_user_from_cookie(
    access_token: Optional[str] = Cookie(default=None),
) -> Optional[TokenData]:
    """Get user from access_token cookie."""
    if access_token is None:
        return None
    
    # Cookie format is "Bearer <token>"
    if access_token.startswith("Bearer "):
        token = access_token[7:]
    else:
        token = access_token
    
    return decode_token(token)


def get_base_context(request: Request, user: Optional[TokenData] = None) -> dict:
    """Get base template context with common variables."""
    ctx = get_hub_context()

    # Hub info: prefer live C++ context, fall back to config singleton
    cfg = get_config_optional()
    hub_name = ctx.hub_name if ctx else (cfg.hub.name if cfg else "Verlihub")
    hub_description = cfg.hub.description if cfg else ""
    hub_topic = ctx.hub_topic if ctx else (cfg.hub.topic if cfg else "")
    hub_logo = cfg.hub.logo if cfg else ""

    return {
        "request": request,
        "user": user,
        "hub_running": ctx.is_running if ctx else False,
        "hub_name": hub_name,
        "hub_description": hub_description,
        "hub_topic": hub_topic,
        "hub_logo": hub_logo or VERLIHUB_DEFAULT_LOGO,
        "current_year": datetime.now(timezone.utc).year,
        "version": "1.7.0.0",
    }


@dashboard_router.get("/", response_class=HTMLResponse)
async def dashboard_home(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Main dashboard page - redirects to login if not authenticated."""
    if user is None:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    
    ctx = get_hub_context()
    context = get_base_context(request, user)
    
    # Get hub stats if available
    motd = ""
    try:
        cfg = get_config_optional()
        _config_dir = cfg._config_dir if cfg else "/etc/verlihub"
        _motd_file = Path(_config_dir) / "motd"
        if _motd_file.exists():
            motd = _motd_file.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        pass

    context.update({
        "user_count": ctx.user_count if ctx else 0,
        "share_size": _format_bytes(ctx.total_share if ctx else 0),
        "uptime": _format_uptime(ctx.uptime if ctx else 0),
        "hub_port": ctx.port if ctx else 411,
        "hub_motd": motd,
    })
    
    return templates.TemplateResponse(request, "dashboard.html", context)


@dashboard_router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: Optional[str] = None,
    next_url: Optional[str] = None,
):
    """Login page."""
    context = get_base_context(request)
    context["error"] = error
    context["next_url"] = next_url or "/dashboard/"
    return templates.TemplateResponse(request, "login.html", context)


@dashboard_router.post("/login")
async def login_submit(request: Request):
    """Handle login form submission."""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    next_url = form.get("next_url", "/dashboard/")
    
    if not username or not password:
        return RedirectResponse(
            url=f"/dashboard/login?error=Username+and+password+required&next_url={next_url}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Authenticate user
    from verlihub.api.auth import authenticate_user, create_access_token
    from verlihub.models.database import get_async_session
    
    try:
        async with get_async_session() as session:
            user = await authenticate_user(username, password, session)
            if user is None:
                return RedirectResponse(
                    url=f"/dashboard/login?error=Invalid+username+or+password&next_url={next_url}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).exception("Login failed for user %r: %s", username, exc)
        return RedirectResponse(
            url=f"/dashboard/login?error=Authentication+failed&next_url={next_url}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Create JWT token
    token = create_access_token(user.nick, user.user_class)
    
    # Create response with redirect
    response = RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)
    
    # Set cookie with token (httponly for security)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token.access_token}",
        httponly=True,
        max_age=86400,  # 24 hours
        samesite="lax",
    )
    
    return response


@dashboard_router.get("/logout")
async def logout(request: Request):
    """Logout and clear session."""
    response = RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="access_token")
    return response


# =============================================================================
# Invite Permalink (QR-code friendly)
# =============================================================================


@dashboard_router.get("/invite/{code}", response_class=HTMLResponse)
async def invite_permalink(request: Request, code: str):
    """Clean permalink for invite codes — ideal for QR codes and sharing.
    
    Redirects to the registration page with the invite code pre-filled.
    Example: /dashboard/invite/abc123def456
    """
    from starlette.responses import RedirectResponse
    return RedirectResponse(
        url=f"/dashboard/register?invite={code}",
        status_code=303,
    )


# =============================================================================
# Registration Page
# =============================================================================


@dashboard_router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    invite: Optional[str] = None,
):
    """Public registration page."""
    cfg = get_config_optional()
    registration_enabled = cfg.api.registration_enabled if cfg else True
    require_invite = cfg.api.registration_require_invite if cfg else False
    
    context = get_base_context(request)
    context["error"] = error
    context["success"] = success
    context["invite_code"] = invite or ""
    context["registration_enabled"] = registration_enabled
    context["require_invite"] = require_invite
    return templates.TemplateResponse(request, "register.html", context)


@dashboard_router.post("/register")
async def register_submit(request: Request):
    """Handle registration form submission."""
    import re
    cfg = get_config_optional()
    registration_enabled = cfg.api.registration_enabled if cfg else True
    require_invite = cfg.api.registration_require_invite if cfg else False
    
    if not registration_enabled:
        return RedirectResponse(
            url="/dashboard/register?error=Registration+is+disabled",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    form = await request.form()
    nick = (form.get("nick", "") or "").strip()
    password = form.get("password", "") or ""
    confirm_password = form.get("confirm_password", "") or ""
    invite_code = (form.get("invite_code", "") or "").strip()
    
    # Validate
    if not nick or len(nick) < 2:
        return RedirectResponse(
            url=f"/dashboard/register?error=Nick+must+be+at+least+2+characters&invite={invite_code}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not re.match(r'^[a-zA-Z0-9_\-\[\]{}|`^]+$', nick):
        return RedirectResponse(
            url=f"/dashboard/register?error=Nick+contains+invalid+characters&invite={invite_code}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not password or len(password) < 4:
        return RedirectResponse(
            url=f"/dashboard/register?error=Password+must+be+at+least+4+characters&invite={invite_code}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if password != confirm_password:
        return RedirectResponse(
            url=f"/dashboard/register?error=Passwords+do+not+match&invite={invite_code}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if require_invite and not invite_code:
        return RedirectResponse(
            url="/dashboard/register?error=An+invite+code+is+required",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Call the API registration endpoint
    from verlihub.api.auth import create_access_token, hash_password
    from verlihub.models import InviteCode as InviteCodeModel
    from verlihub.models import utc_now
    from verlihub.models.database import get_async_session
    from sqlmodel import select
    
    try:
        async with get_async_session() as session:
            # Check if nick exists
            query = select(RegUser).where(RegUser.nick == nick)
            result = await session.execute(query)
            if result.scalar_one_or_none() is not None:
                return RedirectResponse(
                    url=f"/dashboard/register?error=Nick+already+registered&invite={invite_code}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            
            # Handle invite code
            from verlihub.models import UserClass
            default_class = cfg.api.registration_default_class if cfg else UserClass.REGISTERED
            user_class = default_class
            invite = None
            
            if invite_code:
                query = select(InviteCodeModel).where(InviteCodeModel.code == invite_code)
                result = await session.execute(query)
                invite = result.scalar_one_or_none()
                
                if invite is None:
                    return RedirectResponse(
                        url="/dashboard/register?error=Invalid+invite+code",
                        status_code=status.HTTP_303_SEE_OTHER,
                    )
                if invite.used:
                    return RedirectResponse(
                        url="/dashboard/register?error=Invite+code+already+used",
                        status_code=status.HTTP_303_SEE_OTHER,
                    )
                if invite.expires_at and invite.expires_at < utc_now():
                    return RedirectResponse(
                        url="/dashboard/register?error=Invite+code+has+expired",
                        status_code=status.HTTP_303_SEE_OTHER,
                    )
                user_class = invite.max_class
            
            # Create user
            new_user = RegUser(
                nick=nick,
                login_pwd=hash_password(password),
                user_class=user_class,
                authorised=True,
                reg_op="self-registration",
            )
            session.add(new_user)
            
            if invite:
                invite.used = True
                invite.used_by = nick
                invite.used_at = utc_now()
            
            await session.commit()
    except Exception:
        return RedirectResponse(
            url=f"/dashboard/register?error=Registration+failed&invite={invite_code}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Create token and log the user in
    token = create_access_token(nick, user_class)
    response = RedirectResponse(url="/dashboard/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token.access_token}",
        httponly=True,
        max_age=86400,
        samesite="lax",
    )
    return response


# =============================================================================
# Invite Code Management Page
# =============================================================================


@dashboard_router.get("/invites", response_class=HTMLResponse)
async def invites_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
    access_token: Optional[str] = Cookie(default=None),
):
    """Invite code management page."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/invites",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    ctx = get_hub_context()
    context = get_base_context(request, user)
    
    # Pass the token for API calls
    token = access_token[7:] if access_token and access_token.startswith("Bearer ") else access_token
    context["auth_token"] = token or ""
    context["is_admin"] = user.user_class >= 5
    
    return templates.TemplateResponse(request, "invites.html", context)


@dashboard_router.get("/hublist", response_class=HTMLResponse)
async def hublist_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Hub list management page (master-only)."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/hublist",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if user.user_class < 10:  # Master required
        return RedirectResponse(
            url="/dashboard/",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    context = get_base_context(request, user)

    hubs: list = []
    blocks: list = []
    try:
        from verlihub.models.database import get_async_session
        from verlihub.models import HubListEntry, HubListBlock
        async with get_async_session() as session:
            result = await session.execute(select(HubListEntry))
            hubs = result.scalars().all()
            result2 = await session.execute(select(HubListBlock))
            blocks = result2.scalars().all()
    except Exception:
        pass

    context.update({
        "hubs": hubs,
        "blocks": blocks,
    })

    return templates.TemplateResponse(request, "hublist.html", context)


@dashboard_router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
    page: int = 1,
    search: Optional[str] = None,
):
    """User management page."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/users",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    ctx = get_hub_context()
    context = get_base_context(request, user)
    
    # Get online users from hub
    online_users = []
    if ctx and ctx.is_running:
        online_users = ctx.get_user_list()
    
    # Get registered users from database (with pagination)
    from verlihub.models.database import get_async_session
    from sqlmodel import select
    
    per_page = 50
    offset = (page - 1) * per_page
    
    try:
        async with get_async_session() as session:
            query = select(RegUser)
            if search:
                query = query.where(RegUser.nick.contains(search))
            query = query.offset(offset).limit(per_page)
            result = await session.execute(query)
            registered_users = result.scalars().all()
    except Exception:
        registered_users = []
    
    context.update({
        "online_users": online_users,
        "registered_users": registered_users,
        "page": page,
        "search": search,
        "per_page": per_page,
        "can_edit": user.user_class >= 5,  # Admin+ can edit
    })
    
    return templates.TemplateResponse(request, "users.html", context)


@dashboard_router.get("/bans", response_class=HTMLResponse)
async def bans_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
    page: int = 1,
    search: Optional[str] = None,
):
    """Ban management page."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/bans",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    context = get_base_context(request, user)
    
    # Get bans from database
    from verlihub.models.database import get_async_session
    from verlihub.models import Ban
    from sqlmodel import select
    
    per_page = 50
    offset = (page - 1) * per_page
    
    try:
        async with get_async_session() as session:
            query = select(Ban)
            if search:
                query = query.where(
                    Ban.nick.contains(search) | Ban.ip.contains(search)
                )
            query = query.offset(offset).limit(per_page)
            result = await session.execute(query)
            bans = result.scalars().all()
    except Exception:
        bans = []
    
    context.update({
        "bans": bans,
        "page": page,
        "search": search,
        "per_page": per_page,
        "can_edit": user.user_class >= 3,  # Operator+ can edit
    })
    
    return templates.TemplateResponse(request, "bans.html", context)


@dashboard_router.get("/config", response_class=HTMLResponse)
async def config_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """Hub configuration page."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/config",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    ctx = get_hub_context()
    context = get_base_context(request, user)
    
    # Get current configuration
    config = {}
    if ctx:
        try:
            _g = lambda key, default="": ctx.get_config("config", key, default)
            _gi = lambda key, default=0: int(_g(key, str(default)) or default)
            config = {
                "hub_name": _g("hub_name"),
                "hub_desc": _g("hub_desc"),
                "hub_topic": _g("hub_topic"),
                "hub_owner": _g("hub_owner"),
                "hub_category": _g("hub_category"),
                "hub_encoding": _g("hub_encoding", "UTF-8"),
                "port": _gi("listen_port", 411),
                "listen_ip": _g("listen_ip", "0.0.0.0"),
                "hub_host": _g("hub_host"),
                "use_regserver": _g("use_regserver") == "1",
                "regserver_host": _g("regserver_host"),
                "enable_tls": _g("tls_enabled") == "1",
                "allow_unregistered": _g("allow_unregistered") == "1",
                "require_password": _g("require_password") == "1",
                "login_timeout": _gi("login_timeout", 60),
                "max_pass_attempts": _gi("max_pass_attempts", 3),
                "flood_protection": _gi("flood_protection", 2),
                "chat_filter": _g("chat_filter") == "1",
                "anti_clone": _g("anti_clone") == "1",
                "registration_require_invite": _g("registration_require_invite") == "1",
                "max_users": _gi("max_users", 1000),
                "min_share": _gi("min_share", 0),
                "min_slots": _gi("min_slots", 0),
                "max_hubs_user": _gi("max_hubs_user", 0),
                "max_hubs_op": _gi("max_hubs_op", 0),
                "max_conn_per_ip": _gi("max_conn_per_ip", 5),
                "hub_motd": "",  # loaded from file below
                "hub_security": _g("hub_security", "Hub-Security"),
                "opchat_name": _g("opchat_name", "OpChat"),
            }
            # MOTD is stored in a file, not a C++ config key
            try:
                from verlihub.config import get_config_optional as _gcc
                _cfg = _gcc()
                _config_dir = _cfg._config_dir if _cfg else "/etc/verlihub"
                _motd_file = Path(_config_dir) / "motd"
                if _motd_file.exists():
                    config["hub_motd"] = _motd_file.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                # Hublist servers from YAML config (multi-server list)
                if _cfg:
                    config["hublist_servers"] = "\n".join(_cfg.hub.hublist_servers or [])
                    config["hublist_server_enabled"] = _cfg.hublist.server_enabled
            except Exception:
                pass
        except Exception:
            pass
    
    context["config"] = config
    context["can_edit"] = user.user_class >= 10  # Master can edit
    
    return templates.TemplateResponse(request, "config.html", context)


@dashboard_router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
):
    """System logs page."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/logs",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    context = get_base_context(request, user)
    context["logs"] = []  # TODO: Implement log retrieval
    context["can_edit"] = user.user_class >= 5  # Admin+ can manage
    
    return templates.TemplateResponse(request, "logs.html", context)


@dashboard_router.get("/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
    access_token: Optional[str] = Cookie(default=None),
):
    """Hub chat page — real-time chat with hub command support."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/chat",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    ctx = get_hub_context()
    context = get_base_context(request, user)
    
    # Pass the token for API calls
    token = access_token[7:] if access_token and access_token.startswith("Bearer ") else access_token
    context["auth_token"] = token or ""
    context["user_count"] = ctx.user_count if ctx else 0
    
    return templates.TemplateResponse(request, "chat.html", context)


@dashboard_router.get("/plugins", response_class=HTMLResponse)
async def plugins_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
    access_token: Optional[str] = Cookie(default=None),
):
    """Plugin management page."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/plugins",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    ctx = get_hub_context()
    context = get_base_context(request, user)
    
    # Pass the token for API calls
    token = access_token[7:] if access_token and access_token.startswith("Bearer ") else access_token
    context["auth_token"] = token or ""
    
    # Get plugin list
    plugins = []
    scripts = []
    
    if ctx:
        try:
            plugins = ctx.get_plugins() or []
        except AttributeError:
            # Mock some plugins for demo
            plugins = [
                {"nick": "plugman", "desc": "Plugin Manager", "loaded": True, "autoload": True, "version": "1.0"},
                {"nick": "python", "desc": "Python Scripting", "loaded": True, "autoload": True, "version": "2.0"},
            ]
        
        try:
            scripts = ctx.get_python_scripts() or []
        except AttributeError:
            scripts = []
    
    # Get Lua scripts
    lua_scripts = []
    if ctx:
        try:
            lua_scripts = ctx.get_lua_scripts() or []
        except AttributeError:
            # Fall back to checking scripts directory
            import os
            scripts_dir = os.environ.get("VH_SCRIPTS_DIR", "/usr/local/share/verlihub/scripts")
            if os.path.isdir(scripts_dir):
                for f in os.listdir(scripts_dir):
                    if f.endswith(".lua"):
                        lua_scripts.append({"name": f, "loaded": False})
    
    context["plugins"] = plugins
    context["scripts"] = scripts
    context["lua_scripts"] = lua_scripts
    context["can_edit"] = user.user_class >= 5  # Admin+ can manage plugins
    
    return templates.TemplateResponse(request, "plugins.html", context)


# =============================================================================
# SPA Dashboard (Single Page Application)
# =============================================================================


# SPA Dashboard HTML - Full-featured single-page dashboard matching verlihub_client.html
SPA_DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <title id="page-title">Verlihub Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #f0f2f5; color: #333; }
        
        .page-wrapper { max-width: 1200px; margin: 0 auto; padding: 20px; }
        
        /* Header */
        .page-header { text-align: center; margin-bottom: 20px; }
        .hub-name { font-size: 2em; margin: 0; color: #1a237e; }
        .hub-desc { font-size: 1.1em; color: #555; margin: 10px 0 0 0; }
        
        /* Navigation tabs */
        #tabs { margin-bottom: 20px; position: relative; z-index: 100; display: flex; justify-content: center; flex-wrap: wrap; }
        .tab { cursor: pointer; display: inline-block; padding: 12px 18px; background: #eee; border: 1px solid #ccc; margin-right: 5px; margin-bottom: 5px; border-radius: 5px 5px 0 0; position: relative; z-index: 101; transition: all 0.2s; }
        .tab:hover { background: #e0e0e0; }
        .tab.active { background: #fff; border-bottom: none; font-weight: bold; color: #1a237e; }
        
        /* Content area */
        #content { padding: 20px; border: 1px solid #ccc; border-radius: 0 5px 5px 5px; background: #fff; min-height: 300px; position: relative; z-index: 1; }
        #content::before { content: "Loading..."; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 1.2em; color: #666; display: none; z-index: 10; }
        #content.loading::before { display: block; }
        #content.loading > * { opacity: 0.3; pointer-events: none; }
        
        /* Tables */
        table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        th, td { border: 1px solid #ddd; padding: 10px; text-align: left; }
        th { background: #f5f5f5; font-size: 1em; cursor: pointer; user-select: none; }
        th:hover { background: #e9e9e9; }
        tr:nth-child(even) { background: #fafafa; }
        tr.update-highlight { background: #ffffd0 !important; animation: highlightFade 4s ease-out forwards; }
        @keyframes highlightFade { 0% { background: #ffffd0 !important; } 50% { background: #ffffd0 !important; } 100% { background: transparent; } }
        
        /* Links */
        a { color: #0066cc; text-decoration: none; }
        a:hover { text-decoration: underline; }
        
        /* User detail modal */
        #user-detail { position: fixed; top: 10%; left: 10%; width: 80%; max-width: 800px; max-height: 80%; overflow-y: auto; background: #fff; border: 1px solid #aaa; padding: 20px; box-shadow: 0 0 20px rgba(0,0,0,0.3); display: none; z-index: 1000; border-radius: 8px; }
        #user-detail button.close-btn { float: right; padding: 8px 12px; cursor: pointer; background: #1a237e; color: white; border: none; border-radius: 4px; }
        #user-detail h3 { color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 8px; margin-top: 25px; margin-bottom: 15px; }
        #user-detail h3:first-of-type { margin-top: 10px; }
        
        /* Info list */
        .info-list { list-style: none; padding: 0; margin: 15px 0; }
        .info-list li { margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #eee; }
        
        /* Flag and counts */
        .flag { font-size: 2em; text-align: center; }
        .count { text-align: center; font-weight: bold; font-size: 1.2em; }
        .total-countries, .total-users { font-size: 1.2em; font-weight: bold; margin-bottom: 15px; }
        
        /* Hub info layout */
        .hub-topic { font-size: 1.4em; font-weight: bold; margin: 0 0 30px 0; text-align: center; }
        .hub-upper { display: flex; flex-wrap: wrap; gap: 30px; align-items: flex-start; margin-bottom: 30px; justify-content: center; }
        .hub-logo-wrapper { flex: 0 0 auto; text-align: center; width: 256px; height: 256px; display: flex; align-items: center; justify-content: center; }
        .hub-logo img { max-width: 256px; max-height: 256px; width: auto; height: auto; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); display: block; }
        .hub-info { flex: 1 1 300px; }
        .motd-wrapper { text-align: center; margin-top: 20px; }
        .motd { background: #f0f0f0; padding: 15px; border-radius: 8px; display: block; text-align: left; width: 100%; box-sizing: border-box; }
        .motd pre { margin: 0; white-space: pre-wrap; font-family: monospace; font-size: 0.95em; }
        
        /* Sort arrows */
        .sort-arrow { margin-left: 6px; opacity: 0.5; }
        .sort-arrow.active { opacity: 1; font-weight: bold; }
        
        /* Cards */
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background: #f8f9fa; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); text-align: center; }
        .card h3 { color: #1a237e; margin-bottom: 10px; font-size: 0.95em; }
        .card-value { font-size: 1.8em; font-weight: 700; color: #1a237e; }
        .card-label { font-size: 0.85em; color: #666; margin-top: 5px; }
        
        /* Badges */
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75em; font-weight: 600; margin-left: 5px; }
        .badge-clone { background: #ffebee; color: #c62828; }
        .badge-op { background: #e3f2fd; color: #1565c0; }
        .badge-bot { background: #f3e5f5; color: #7b1fa2; }
        
        /* Clone filter */
        .filter-controls { margin-bottom: 15px; padding: 10px; background: #f5f5f5; border-radius: 5px; }
        .filter-controls label { cursor: pointer; user-select: none; }
        
        /* Health status */
        .status-healthy { color: #2e7d32; }
        .status-degraded { color: #ed6c02; }
        .status-unknown { color: #9e9e9e; }
        
        /* Responsive */
        @media (max-width: 768px) {
            .page-wrapper { width: 100%; padding: 10px; }
            .tab { padding: 10px 12px; font-size: 0.9em; }
            th, td { padding: 8px 6px; font-size: 0.85em; }
            #user-detail { left: 5%; width: 90%; }
        }
    </style>
</head>
<body>
    <div class="page-wrapper">
        <div class="page-header">
            <img src="https://avatars1.githubusercontent.com/u/1856420?v=3&s=300"
                 alt="Verlihub" id="spa-logo"
                 style="width: 72px; height: 72px; border-radius: 50%; box-shadow: 0 4px 16px rgba(0,0,0,0.15); border: 3px solid #1a237e; margin-bottom: 12px;">
            <h1 id="hub-name-header" class="hub-name">Verlihub Dashboard</h1>
            <p id="hub-desc-header" class="hub-desc"></p>
        </div>

        <div id="tabs">
            <span class="tab active" data-tab="hub">Hub</span>
            <span class="tab" data-tab="users">Online Users</span>
            <span class="tab" data-tab="geo">Countries</span>
            <span class="tab" data-tab="cities">Cities</span>
            <span class="tab" data-tab="asns">ASNs</span>
            <span class="tab" data-tab="ips">IPs</span>
        </div>
        <div id="content">Loading...</div>
    </div>

    <div id="user-detail">
        <button class="close-btn" onclick="closeUserDetail()">Close &times;</button>
        <h2 id="user-detail-title">Details</h2>
        <div id="user-content"></div>
    </div>

    <script>
        const API_BASE = '/api/v1';
        let currentTab = 'hub';
        let currentUsers = [];
        let currentGeo = [];
        let currentCities = [];
        let currentASNs = [];
        let currentIPs = [];
        let isFirstLoad = { users: true, geo: true, cities: true, asns: true, ips: true };
        let hideClones = false;
        let opsList = [];
        let botsList = [];
        let hubStartTime = null;
        let uptimeUpdateInterval = null;
        let pollInterval = null;

        const sortState = {
            users: { key: 'share', asc: false },
            geo: { key: 'users', asc: false },
            cities: { key: 'users', asc: false },
            asns: { key: 'users', asc: false },
            ips: { key: 'users', asc: false }
        };

        function fetchData(endpoint) {
            return fetch(API_BASE + endpoint)
                .then(res => {
                    if (!res.ok) throw new Error(`HTTP ${res.status} - ${res.statusText}`);
                    return res.json();
                });
        }

        function formatBytes(bytes) {
            if (bytes === undefined || bytes === null) return 'N/A';
            const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
            let i = 0;
            while (bytes >= 1024 && i < units.length - 1) {
                bytes /= 1024;
                i++;
            }
            return `${bytes.toFixed(2)} ${units[i]}`;
        }

        function getFlagEmoji(cc) {
            if (!cc || cc.length !== 2) return '&#x1F310;';
            return String.fromCodePoint(...[...cc.toUpperCase()].map(c => 0x1F1E6 + c.charCodeAt(0) - 65));
        }

        function formatUptime(seconds) {
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const secs = Math.floor(seconds % 60);
            let parts = [];
            if (days > 0) parts.push(`${days}d`);
            if (hours > 0 || days > 0) parts.push(`${hours}h`);
            if (minutes > 0 || hours > 0 || days > 0) parts.push(`${minutes}m`);
            parts.push(`${secs}s`);
            return parts.join(' ');
        }

        function escapeHtml(str) {
            if (!str) return '';
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        function updateUptimeDisplay() {
            if (!hubStartTime) return;
            const now = Date.now();
            const uptimeSeconds = (now - hubStartTime) / 1000;
            const uptimeElement = document.getElementById('hub-uptime');
            if (uptimeElement) {
                uptimeElement.textContent = formatUptime(uptimeSeconds);
            }
        }

        function setSort(tab, key) {
            if (sortState[tab].key === key) {
                sortState[tab].asc = !sortState[tab].asc;
            } else {
                sortState[tab].key = key;
                sortState[tab].asc = (tab === 'geo' && key === 'users') || (tab === 'users' && key === 'share') ? false : true;
            }
            loadTab(currentTab);
        }

        async function fetchOpsAndBots() {
            try {
                const [opsData, botsData] = await Promise.all([
                    fetchData('/stats/ops'),
                    fetchData('/stats/bots')
                ]);
                opsList = opsData || [];
                botsList = botsData || [];
            } catch (err) {
                console.error('Failed to fetch ops/bots:', err);
                opsList = [];
                botsList = [];
            }
        }

        async function loadTab(tab) {
            const isTabSwitch = (currentTab !== tab);
            currentTab = tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            const activeTab = document.querySelector(`.tab[data-tab="${tab}"]`);
            if (activeTab) activeTab.classList.add('active');

            const contentEl = document.getElementById('content');
            contentEl.classList.add('loading');

            try {
                let html = '';
                switch (tab) {
                    case 'hub': html = await renderHub(); break;
                    case 'users': html = await renderUsers(); break;
                    case 'geo': html = await renderGeo(); break;
                    case 'cities': html = await renderCities(); break;
                    case 'asns': html = await renderASNs(); break;
                    case 'ips': html = await renderIPs(); break;
                }
                contentEl.innerHTML = html;
                contentEl.classList.remove('loading');
            } catch (err) {
                contentEl.innerHTML = `<p style="color:red;">Error: ${escapeHtml(err.message)}</p>`;
                contentEl.classList.remove('loading');
                console.error('Tab load error:', err);
            }
        }

        async function renderHub() {
            const [info, stats, health] = await Promise.all([
                fetchData('/hub/info'),
                fetchData('/stats/stats'),
                fetchData('/stats/health').catch(() => ({ status: 'unknown', hub_running: false }))
            ]);

            document.getElementById('hub-name-header').textContent = info.name || 'Verlihub';
            document.getElementById('hub-desc-header').textContent = info.description || '';
            document.title = (info.name || 'Verlihub') + ' - Dashboard';

            // Store start time for live uptime updates
            if (info.uptime_seconds) {
                hubStartTime = Date.now() - (info.uptime_seconds * 1000);
                if (!uptimeUpdateInterval) {
                    uptimeUpdateInterval = setInterval(updateUptimeDisplay, 1000);
                }
            }

            let html = '';
            
            // Topic
            if (info.topic) {
                html += `<div class="hub-topic">${escapeHtml(info.topic)}</div>`;
            }

            // Upper section with logo and info
            html += '<div class="hub-upper">';
            if (info.logo_url) {
                html += `<div class="hub-logo-wrapper"><div class="hub-logo"><img src="${escapeHtml(info.logo_url)}" alt="Hub Logo"></div></div>`;
            } else if (info.icon_url) {
                html += `<div class="hub-logo-wrapper"><div class="hub-logo"><img src="${escapeHtml(info.icon_url)}" alt="Hub Icon"></div></div>`;
            }
            
            html += '<div class="hub-info"><ul class="info-list">';
            html += `<li><strong>Host:</strong> ${escapeHtml(info.host) || 'Not configured'}</li>`;
            html += `<li><strong>Port:</strong> ${info.listen_port || 411}</li>`;
            html += `<li><strong>TLS:</strong> ${info.tls_enabled ? 'Enabled' : 'Disabled'}</li>`;
            html += `<li><strong>Version:</strong> ${escapeHtml(info.version) || 'Unknown'}</li>`;
            html += `<li><strong>Encoding:</strong> ${escapeHtml(info.hub_encoding) || 'UTF-8'}</li>`;
            if (info.hub_owner) html += `<li><strong>Owner:</strong> ${escapeHtml(info.hub_owner)}</li>`;
            html += `<li><strong>Uptime:</strong> <span id="hub-uptime">${info.uptime_formatted || formatUptime(info.uptime_seconds || 0)}</span></li>`;
            html += '</ul></div></div>';

            // Stats cards
            html += '<div class="cards">';
            html += `<div class="card"><h3>Users Online</h3><div class="card-value">${stats.users_online}</div><div class="card-label">of ${stats.max_users} max</div></div>`;
            html += `<div class="card"><h3>Total Share</h3><div class="card-value">${stats.total_share_formatted}</div></div>`;
            html += `<div class="card"><h3>Operators</h3><div class="card-value">${stats.operators_online}</div><div class="card-label">online</div></div>`;
            html += `<div class="card"><h3>Bots</h3><div class="card-value">${stats.bots_online}</div><div class="card-label">active</div></div>`;
            html += `<div class="card"><h3>Status</h3><div class="card-value status-${health.status}">${health.status}</div></div>`;
            html += '</div>';

            // MOTD
            if (info.motd) {
                html += '<div class="motd-wrapper"><h3 style="margin-bottom: 10px;">Message of the Day</h3>';
                html += `<div class="motd"><pre>${escapeHtml(info.motd)}</pre></div></div>`;
            }

            return html;
        }

        async function renderUsers() {
            const response = await fetchData('/stats/users/detailed?limit=500');
            let users = Array.isArray(response) ? response : (response.users || []);

            // Clone detection
            const cloneGroups = new Map();
            const nickToCloneKey = new Map();
            let cloneCount = 0;

            for (const user of users) {
                const nick = user.nick || '';
                const isBot = botsList.some(bot => bot.nick === nick);
                if (isBot) continue;
                
                const ip = user.ip || '';
                const share = user.share || 0;
                const cloneKey = `${ip}:${share}`;
                
                if (!cloneGroups.has(cloneKey)) cloneGroups.set(cloneKey, []);
                cloneGroups.get(cloneKey).push(user);
                nickToCloneKey.set(nick, cloneKey);
            }

            const uniqueUsers = cloneGroups.size;
            for (const [key, group] of cloneGroups) {
                if (group.length > 1) cloneCount += group.length - 1;
            }

            // Filter if hiding clones
            let displayUsers = users;
            if (hideClones) {
                const seenKeys = new Set();
                displayUsers = users.filter(u => {
                    const cloneKey = nickToCloneKey.get(u.nick);
                    if (!cloneKey || seenKeys.has(cloneKey)) return !cloneKey;
                    seenKeys.add(cloneKey);
                    return true;
                });
            }

            // Sort
            const sorted = [...displayUsers].sort((a, b) => {
                const key = sortState.users.key;
                const asc = sortState.users.asc;
                if (key === 'nick') return asc ? a.nick.localeCompare(b.nick) : b.nick.localeCompare(a.nick);
                if (key === 'class') return asc ? (a.class_name || '').localeCompare(b.class_name || '') : (b.class_name || '').localeCompare(a.class_name || '');
                if (key === 'country') return asc ? (a.country_code || '').localeCompare(b.country_code || '') : (b.country_code || '').localeCompare(a.country_code || '');
                if (key === 'share') return asc ? (a.share || 0) - (b.share || 0) : (b.share || 0) - (a.share || 0);
                return 0;
            });

            const clonePercent = users.length > 0 ? ((cloneCount / users.length) * 100).toFixed(1) : 0;

            let html = `
                <div class="filter-controls">
                    <label><input type="checkbox" id="hide-clones" ${hideClones ? 'checked' : ''} onchange="toggleHideClones(this.checked)"> Hide clones (show only unique)</label>
                </div>
                <div class="total-users">
                    Online users: ${users.length} | True users: ${uniqueUsers} | Clones: ${cloneCount} (${clonePercent}%)
                </div>`;

            html += '<table id="users-table"><thead><tr>';
            const headers = [
                { text: 'Nick', key: 'nick' },
                { text: 'Class', key: 'class' },
                { text: 'Country', key: 'country' },
                { text: 'Share', key: 'share' }
            ];
            for (const h of headers) {
                const arrow = sortState.users.key === h.key ? (sortState.users.asc ? '&uarr;' : '&darr;') : '&varr;';
                const activeClass = sortState.users.key === h.key ? 'active' : '';
                html += `<th onclick="setSort('users', '${h.key}')">${h.text} <span class="sort-arrow ${activeClass}">${arrow}</span></th>`;
            }
            html += '</tr></thead><tbody>';

            for (const user of sorted) {
                const nick = user.nick || 'Unknown';
                const prevUser = currentUsers.find(u => u.nick === nick);
                const isNew = !prevUser && !isFirstLoad.users;
                const shareChanged = prevUser && (user.share || 0) !== (prevUser.share || 0) && !isFirstLoad.users;
                const highlightClass = (isNew || shareChanged) ? ' update-highlight' : '';

                const cloneKey = nickToCloneKey.get(nick);
                const cloneGroup = cloneGroups.get(cloneKey) || [];
                const hasClones = cloneGroup.length > 1;
                const isOp = opsList.some(op => op.nick === nick);
                const isBot = botsList.some(bot => bot.nick === nick);

                const badges = [];
                if (isBot) badges.push('<span class="badge badge-bot">BOT</span>');
                if (isOp && !isBot) badges.push('<span class="badge badge-op">OP</span>');
                if (hasClones && !isBot) badges.push(`<span class="badge badge-clone">Clone (${cloneGroup.length})</span>`);

                html += `<tr class="${highlightClass}">`;
                html += `<td><a href="#" onclick="showUser('${encodeURIComponent(nick)}'); return false;">${escapeHtml(nick)}</a>${badges.join('')}</td>`;
                html += `<td>${escapeHtml(user.class_name || 'Unknown')}</td>`;
                html += `<td><span class="flag">${getFlagEmoji(user.country_code)}</span></td>`;
                html += `<td>${user.share_formatted || formatBytes(user.share || 0)}</td>`;
                html += '</tr>';
            }
            html += '</tbody></table>';

            currentUsers = [...users];
            isFirstLoad.users = false;
            return html;
        }

        async function renderGeo() {
            const data = await fetchData('/stats/geo');
            const distribution = data.distribution || [];

            const sorted = [...distribution].sort((a, b) => {
                const key = sortState.geo.key;
                const asc = sortState.geo.asc;
                if (key === 'country') return asc ? a.country_code.localeCompare(b.country_code) : b.country_code.localeCompare(a.country_code);
                return asc ? a.users - b.users : b.users - a.users;
            });

            let html = `<div class="total-countries">Total countries represented: ${data.total_countries || distribution.length}</div>`;
            html += '<table id="geo-table"><thead><tr>';
            
            const headers = [{ text: 'Flag', key: 'country' }, { text: 'Country', key: 'country' }, { text: 'Users', key: 'users' }, { text: 'Share', key: 'share' }];
            for (const h of headers) {
                const arrow = sortState.geo.key === h.key ? (sortState.geo.asc ? '&uarr;' : '&darr;') : '&varr;';
                const activeClass = sortState.geo.key === h.key ? 'active' : '';
                html += `<th onclick="setSort('geo', '${h.key}')">${h.text} <span class="sort-arrow ${activeClass}">${arrow}</span></th>`;
            }
            html += '</tr></thead><tbody>';

            for (const item of sorted) {
                const prev = currentGeo.find(g => g.country_code === item.country_code);
                const changed = prev && prev.users !== item.users && !isFirstLoad.geo;
                const isNew = !prev && !isFirstLoad.geo;
                const highlightClass = (isNew || changed) ? ' update-highlight' : '';
                
                html += `<tr class="${highlightClass}" style="cursor: pointer;" onclick="showCountryUsers('${item.country_code}')">`;
                html += `<td class="flag">${getFlagEmoji(item.country_code)}</td>`;
                html += `<td>${escapeHtml(item.country_name || item.country_code)}</td>`;
                html += `<td class="count">${item.users}</td>`;
                html += `<td>${item.share_formatted || formatBytes(item.share || 0)}</td>`;
                html += '</tr>';
            }
            html += '</tbody></table>';

            currentGeo = [...distribution];
            isFirstLoad.geo = false;
            return html;
        }

        async function renderCities() {
            const response = await fetchData('/stats/users/detailed?limit=500');
            const users = Array.isArray(response) ? response : (response.users || []);

            const cityStats = new Map();
            for (const user of users) {
                const city = user.city || '';
                const cc = (user.country_code || '').toUpperCase();
                if (!city || city === 'N/A' || !cc) continue;
                const key = `${city}|||${cc}`;
                if (!cityStats.has(key)) cityStats.set(key, { city, country_code: cc, users: 0 });
                cityStats.get(key).users++;
            }

            const cityArray = Array.from(cityStats.values());
            const sorted = [...cityArray].sort((a, b) => {
                const key = sortState.cities.key;
                const asc = sortState.cities.asc;
                if (key === 'city') return asc ? a.city.localeCompare(b.city) : b.city.localeCompare(a.city);
                return asc ? a.users - b.users : b.users - a.users;
            });

            let html = `<div class="total-countries">Total cities represented: ${cityArray.length}</div>`;
            html += '<table id="cities-table"><thead><tr>';
            
            const headers = [{ text: 'City', key: 'city' }, { text: 'Flag', key: 'country' }, { text: 'Users', key: 'users' }];
            for (const h of headers) {
                const arrow = sortState.cities.key === h.key ? (sortState.cities.asc ? '&uarr;' : '&darr;') : '&varr;';
                const activeClass = sortState.cities.key === h.key ? 'active' : '';
                html += `<th onclick="setSort('cities', '${h.key}')">${h.text} <span class="sort-arrow ${activeClass}">${arrow}</span></th>`;
            }
            html += '</tr></thead><tbody>';

            for (const item of sorted) {
                const prev = currentCities.find(c => c.city === item.city && c.country_code === item.country_code);
                const changed = prev && prev.users !== item.users && !isFirstLoad.cities;
                const isNew = !prev && !isFirstLoad.cities;
                const highlightClass = (isNew || changed) ? ' update-highlight' : '';
                const cityKey = `${item.city}|||${item.country_code}`;
                
                html += `<tr class="${highlightClass}" style="cursor: pointer;" onclick="showCityUsers('${encodeURIComponent(cityKey)}')">`;
                html += `<td>${escapeHtml(item.city)}</td>`;
                html += `<td class="flag">${getFlagEmoji(item.country_code)}</td>`;
                html += `<td class="count">${item.users}</td>`;
                html += '</tr>';
            }
            html += '</tbody></table>';

            currentCities = [...cityArray];
            isFirstLoad.cities = false;
            return html;
        }

        async function renderASNs() {
            const response = await fetchData('/stats/users/detailed?limit=500');
            const users = Array.isArray(response) ? response : (response.users || []);

            const asnStats = new Map();
            for (const user of users) {
                const asn = user.asn || '';
                if (!asn || asn === 'N/A') continue;
                if (!asnStats.has(asn)) asnStats.set(asn, { asn, users: 0 });
                asnStats.get(asn).users++;
            }

            const asnArray = Array.from(asnStats.values());
            const sorted = [...asnArray].sort((a, b) => {
                const key = sortState.asns.key;
                const asc = sortState.asns.asc;
                if (key === 'asn') return asc ? a.asn.localeCompare(b.asn) : b.asn.localeCompare(a.asn);
                return asc ? a.users - b.users : b.users - a.users;
            });

            let html = `<div class="total-countries">Total ASNs represented: ${asnArray.length}</div>`;
            html += '<table id="asns-table"><thead><tr>';
            
            const headers = [{ text: 'ASN', key: 'asn' }, { text: 'Users', key: 'users' }];
            for (const h of headers) {
                const arrow = sortState.asns.key === h.key ? (sortState.asns.asc ? '&uarr;' : '&darr;') : '&varr;';
                const activeClass = sortState.asns.key === h.key ? 'active' : '';
                html += `<th onclick="setSort('asns', '${h.key}')">${h.text} <span class="sort-arrow ${activeClass}">${arrow}</span></th>`;
            }
            html += '</tr></thead><tbody>';

            for (const item of sorted) {
                const prev = currentASNs.find(a => a.asn === item.asn);
                const changed = prev && prev.users !== item.users && !isFirstLoad.asns;
                const isNew = !prev && !isFirstLoad.asns;
                const highlightClass = (isNew || changed) ? ' update-highlight' : '';
                
                html += `<tr class="${highlightClass}" style="cursor: pointer;" onclick="showASNUsers('${encodeURIComponent(item.asn)}')">`;
                html += `<td>${escapeHtml(item.asn)}</td>`;
                html += `<td class="count">${item.users}</td>`;
                html += '</tr>';
            }
            html += '</tbody></table>';

            currentASNs = [...asnArray];
            isFirstLoad.asns = false;
            return html;
        }

        async function renderIPs() {
            const response = await fetchData('/stats/users/detailed?limit=500');
            const users = Array.isArray(response) ? response : (response.users || []);

            const ipStats = new Map();
            for (const user of users) {
                const ip = user.ip || '';
                if (!ip || ip === 'N/A') continue;
                if (!ipStats.has(ip)) ipStats.set(ip, { ip, hostname: user.host || '', asn: user.asn || '', users: 0 });
                ipStats.get(ip).users++;
            }

            const ipArray = Array.from(ipStats.values());
            const sorted = [...ipArray].sort((a, b) => {
                const key = sortState.ips.key;
                const asc = sortState.ips.asc;
                if (key === 'ip') return asc ? a.ip.localeCompare(b.ip) : b.ip.localeCompare(a.ip);
                if (key === 'hostname') return asc ? a.hostname.localeCompare(b.hostname) : b.hostname.localeCompare(a.hostname);
                if (key === 'asn') return asc ? a.asn.localeCompare(b.asn) : b.asn.localeCompare(a.asn);
                return asc ? a.users - b.users : b.users - a.users;
            });

            let html = `<div class="total-countries">Total IP addresses: ${ipArray.length}</div>`;
            html += '<table id="ips-table"><thead><tr>';
            
            const headers = [{ text: 'IP Address', key: 'ip' }, { text: 'Hostname', key: 'hostname' }, { text: 'ASN', key: 'asn' }, { text: 'Users', key: 'users' }];
            for (const h of headers) {
                const arrow = sortState.ips.key === h.key ? (sortState.ips.asc ? '&uarr;' : '&darr;') : '&varr;';
                const activeClass = sortState.ips.key === h.key ? 'active' : '';
                html += `<th onclick="setSort('ips', '${h.key}')">${h.text} <span class="sort-arrow ${activeClass}">${arrow}</span></th>`;
            }
            html += '</tr></thead><tbody>';

            for (const item of sorted) {
                const prev = currentIPs.find(i => i.ip === item.ip);
                const changed = prev && prev.users !== item.users && !isFirstLoad.ips;
                const isNew = !prev && !isFirstLoad.ips;
                const highlightClass = (isNew || changed) ? ' update-highlight' : '';
                
                html += `<tr class="${highlightClass}" style="cursor: pointer;" onclick="showIPUsers('${encodeURIComponent(item.ip)}')">`;
                html += `<td>${escapeHtml(item.ip)}</td>`;
                html += `<td>${escapeHtml(item.hostname) || 'N/A'}</td>`;
                html += `<td>${escapeHtml(item.asn) || 'N/A'}</td>`;
                html += `<td class="count">${item.users}</td>`;
                html += '</tr>';
            }
            html += '</tbody></table>';

            currentIPs = [...ipArray];
            isFirstLoad.ips = false;
            return html;
        }

        async function showUser(encodedNick) {
            const nick = decodeURIComponent(encodedNick);
            try {
                const response = await fetchData('/stats/users/detailed?limit=500');
                const users = Array.isArray(response) ? response : (response.users || []);
                const user = users.find(u => u.nick === nick);
                if (!user) { alert('User not found'); return; }

                const isOp = opsList.some(op => op.nick === nick);
                const isBot = botsList.some(bot => bot.nick === nick);

                let html = '<h3>Basic Information</h3><ul class="info-list">';
                html += `<li><strong>Nickname:</strong> ${escapeHtml(nick)}`;
                if (isBot) html += ' <span class="badge badge-bot">BOT</span>';
                if (isOp) html += ' <span class="badge badge-op">OP</span>';
                html += '</li>';
                html += `<li><strong>Class:</strong> ${escapeHtml(user.class_name || 'Unknown')}</li>`;
                html += `<li><strong>Share:</strong> ${user.share_formatted || formatBytes(user.share || 0)}</li>`;
                if (user.description) html += `<li><strong>Description:</strong> ${escapeHtml(user.description)}</li>`;
                if (user.tag) html += `<li><strong>Tag:</strong> ${escapeHtml(user.tag)}</li>`;
                if (user.email) html += `<li><strong>Email:</strong> ${escapeHtml(user.email)}</li>`;
                html += '</ul>';

                html += '<h3>Geographic Information</h3><ul class="info-list">';
                html += `<li><strong>Country:</strong> <span class="flag">${getFlagEmoji(user.country_code)}</span> ${escapeHtml(user.country || user.country_code || 'Unknown')}</li>`;
                if (user.city) html += `<li><strong>City:</strong> ${escapeHtml(user.city)}</li>`;
                if (user.region) html += `<li><strong>Region:</strong> ${escapeHtml(user.region)}</li>`;
                if (user.asn) html += `<li><strong>ASN:</strong> ${escapeHtml(user.asn)}</li>`;
                html += '</ul>';

                html += '<h3>Connection Information</h3><ul class="info-list">';
                if (user.ip) html += `<li><strong>IP:</strong> ${escapeHtml(user.ip)}</li>`;
                if (user.host) html += `<li><strong>Hostname:</strong> ${escapeHtml(user.host)}</li>`;
                html += '</ul>';

                if (user.is_clone || (user.same_ip_users && user.same_ip_users.length > 0)) {
                    html += '<h3>Network Analysis</h3><ul class="info-list">';
                    if (user.is_clone && user.clone_group) {
                        html += `<li><strong>Clone Group:</strong> ${user.clone_group.map(n => escapeHtml(n)).join(', ')}</li>`;
                    }
                    if (user.same_ip_users && user.same_ip_users.length > 0) {
                        html += `<li><strong>Same IP:</strong> ${user.same_ip_users.map(n => escapeHtml(n)).join(', ')}</li>`;
                    }
                    html += '</ul>';
                }

                document.getElementById('user-detail-title').textContent = nick;
                document.getElementById('user-content').innerHTML = html;
                document.getElementById('user-detail').style.display = 'block';
            } catch (err) {
                alert(`Failed to load user: ${err.message}`);
            }
        }

        async function showCountryUsers(countryCode) {
            try {
                const response = await fetchData('/stats/users/detailed?limit=500');
                const users = Array.isArray(response) ? response : (response.users || []);
                const countryUsers = users.filter(u => (u.country_code || '').toUpperCase() === countryCode.toUpperCase());
                
                if (countryUsers.length === 0) { alert(`No users from ${countryCode}`); return; }
                
                const countryName = countryUsers[0].country || countryCode;
                let html = `<div style="text-align: center; font-size: 3em; margin: 10px 0;">${getFlagEmoji(countryCode)}</div>`;
                html += `<h3>Users from ${escapeHtml(countryName)} (${countryUsers.length})</h3><ul class="info-list">`;
                
                countryUsers.sort((a, b) => (b.share || 0) - (a.share || 0));
                for (const user of countryUsers) {
                    html += `<li><a href="#" onclick="showUser('${encodeURIComponent(user.nick)}'); return false;">${escapeHtml(user.nick)}</a> - ${user.share_formatted || formatBytes(user.share || 0)}</li>`;
                }
                html += '</ul>';
                
                document.getElementById('user-detail-title').textContent = countryName;
                document.getElementById('user-content').innerHTML = html;
                document.getElementById('user-detail').style.display = 'block';
            } catch (err) {
                alert(`Failed to load country users: ${err.message}`);
            }
        }

        async function showCityUsers(encodedCityKey) {
            try {
                const cityKey = decodeURIComponent(encodedCityKey);
                const [cityName, countryCode] = cityKey.split('|||');
                
                const response = await fetchData('/stats/users/detailed?limit=500');
                const users = Array.isArray(response) ? response : (response.users || []);
                const cityUsers = users.filter(u => u.city === cityName && (u.country_code || '').toUpperCase() === countryCode);
                
                if (cityUsers.length === 0) { alert(`No users from ${cityName}`); return; }
                
                let html = `<div style="text-align: center; font-size: 3em; margin: 10px 0;">${getFlagEmoji(countryCode)}</div>`;
                html += `<h3>Users from ${escapeHtml(cityName)} (${cityUsers.length})</h3><ul class="info-list">`;
                
                cityUsers.sort((a, b) => (b.share || 0) - (a.share || 0));
                for (const user of cityUsers) {
                    html += `<li><a href="#" onclick="showUser('${encodeURIComponent(user.nick)}'); return false;">${escapeHtml(user.nick)}</a> - ${user.share_formatted || formatBytes(user.share || 0)}</li>`;
                }
                html += '</ul>';
                
                document.getElementById('user-detail-title').textContent = `${cityName}, ${countryCode}`;
                document.getElementById('user-content').innerHTML = html;
                document.getElementById('user-detail').style.display = 'block';
            } catch (err) {
                alert(`Failed to load city users: ${err.message}`);
            }
        }

        async function showASNUsers(encodedASN) {
            try {
                const asn = decodeURIComponent(encodedASN);
                
                const response = await fetchData('/stats/users/detailed?limit=500');
                const users = Array.isArray(response) ? response : (response.users || []);
                const asnUsers = users.filter(u => u.asn === asn);
                
                if (asnUsers.length === 0) { alert(`No users from ASN ${asn}`); return; }
                
                let html = `<h3>Users from ${escapeHtml(asn)} (${asnUsers.length})</h3><ul class="info-list">`;
                
                asnUsers.sort((a, b) => (b.share || 0) - (a.share || 0));
                for (const user of asnUsers) {
                    html += `<li><a href="#" onclick="showUser('${encodeURIComponent(user.nick)}'); return false;">${escapeHtml(user.nick)}</a> - ${user.share_formatted || formatBytes(user.share || 0)}</li>`;
                }
                html += '</ul>';
                
                document.getElementById('user-detail-title').textContent = asn;
                document.getElementById('user-content').innerHTML = html;
                document.getElementById('user-detail').style.display = 'block';
            } catch (err) {
                alert(`Failed to load ASN users: ${err.message}`);
            }
        }

        async function showIPUsers(encodedIP) {
            try {
                const ip = decodeURIComponent(encodedIP);
                
                const response = await fetchData('/stats/users/detailed?limit=500');
                const users = Array.isArray(response) ? response : (response.users || []);
                const ipUsers = users.filter(u => u.ip === ip);
                
                if (ipUsers.length === 0) { alert(`No users from IP ${ip}`); return; }
                
                const firstUser = ipUsers[0];
                let html = '<ul class="info-list">';
                html += `<li><strong>Hostname:</strong> ${escapeHtml(firstUser.host) || 'N/A'}</li>`;
                html += `<li><strong>ASN:</strong> ${escapeHtml(firstUser.asn) || 'N/A'}</li>`;
                if (firstUser.city) html += `<li><strong>City:</strong> ${escapeHtml(firstUser.city)}</li>`;
                if (firstUser.country) html += `<li><strong>Country:</strong> ${getFlagEmoji(firstUser.country_code)} ${escapeHtml(firstUser.country)}</li>`;
                html += '</ul>';
                
                html += `<h3>Users from this IP (${ipUsers.length})</h3><ul class="info-list">`;
                ipUsers.sort((a, b) => (b.share || 0) - (a.share || 0));
                for (const user of ipUsers) {
                    html += `<li><a href="#" onclick="showUser('${encodeURIComponent(user.nick)}'); return false;">${escapeHtml(user.nick)}</a> - ${user.share_formatted || formatBytes(user.share || 0)}</li>`;
                }
                html += '</ul>';
                
                document.getElementById('user-detail-title').textContent = ip;
                document.getElementById('user-content').innerHTML = html;
                document.getElementById('user-detail').style.display = 'block';
            } catch (err) {
                alert(`Failed to load IP users: ${err.message}`);
            }
        }

        function closeUserDetail() {
            document.getElementById('user-detail').style.display = 'none';
        }

        function toggleHideClones(checked) {
            hideClones = checked;
            loadTab('users');
        }

        // Tab click handlers
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                loadTab(tab.dataset.tab);
            });
        });

        function startPolling() {
            // Polling replaced by WebSocket — kept as fallback if WS fails
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(() => {
                if (!spaWsConnected) {
                    fetchOpsAndBots().then(() => loadTab(currentTab));
                }
            }, 60000); // Very slow fallback only if WS is down
        }

        // =================================================================
        // WebSocket real-time updates for SPA dashboard
        // =================================================================
        let spaWs = null;
        let spaWsConnected = false;
        let spaReconnectTimer = null;
        let spaReconnectDelay = 1000;
        let spaPingTimer = null;

        function spaWsConnect() {
            if (spaWs && (spaWs.readyState === WebSocket.OPEN || spaWs.readyState === WebSocket.CONNECTING)) return;

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/hub`;

            try { spaWs = new WebSocket(wsUrl); } catch (e) {
                spaReconnectSchedule();
                return;
            }

            spaWs.onopen = function() {
                spaWsConnected = true;
                spaReconnectDelay = 1000;
                spaPingStart();
            };

            spaWs.onmessage = function(event) {
                try {
                    const msg = JSON.parse(event.data);
                    spaHandleWsMessage(msg);
                } catch (e) {}
            };

            spaWs.onclose = function() {
                spaWsConnected = false;
                spaPingStop();
                spaReconnectSchedule();
            };

            spaWs.onerror = function() {};
        }

        function spaReconnectSchedule() {
            if (spaReconnectTimer) clearTimeout(spaReconnectTimer);
            spaReconnectTimer = setTimeout(() => {
                spaWsConnect();
                spaReconnectDelay = Math.min(spaReconnectDelay * 1.5, 10000);
            }, spaReconnectDelay);
        }

        function spaPingStart() {
            spaPingStop();
            spaPingTimer = setInterval(() => {
                if (spaWs && spaWs.readyState === WebSocket.OPEN) {
                    spaWs.send(JSON.stringify({ type: 'ping' }));
                }
            }, 25000);
        }

        function spaPingStop() {
            if (spaPingTimer) { clearInterval(spaPingTimer); spaPingTimer = null; }
        }

        function spaHandleWsMessage(msg) {
            if (msg.type === 'pong') return;

            if (msg.type === 'connected' || msg.type === 'stats') {
                // Live update uptime counter
                if (msg.uptime) {
                    hubStartTime = Date.now() - (msg.uptime * 1000);
                    if (!uptimeUpdateInterval) {
                        uptimeUpdateInterval = setInterval(updateUptimeDisplay, 1000);
                    }
                }

                // If we're on the hub tab, update the stats cards in-place
                if (currentTab === 'hub') {
                    const userCountEl = document.querySelector('.card-value');
                    // Re-render hub tab for fresh stats
                    loadTab('hub');
                }

                // If we're on the users tab, update with live user list
                if (currentTab === 'users' && msg.users) {
                    // Store for next render
                    currentUsers = msg.users;
                }
            }

            if (msg.type === 'user_join' || msg.type === 'user_leave' || msg.type === 'user_login') {
                // Refresh users tab if visible
                if (currentTab === 'users') {
                    loadTab('users');
                }
            }
        }

        // Close modal on escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeUserDetail();
        });

        // Reconnect on tab focus
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && !spaWsConnected) spaWsConnect();
        });

        // Initial load
        fetchOpsAndBots().then(() => {
            loadTab('hub');
            startPolling();
            spaWsConnect();
        });
    </script>
</body>
</html>
'''


# Embeddable mini dashboard for iframe embedding
EMBED_DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <title>Hub Stats</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: transparent; color: #333; padding: 10px; }
        .embed-container { max-width: 400px; margin: 0 auto; }
        .hub-header { text-align: center; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #ddd; }
        .hub-name { font-size: 1.3em; color: #1a237e; margin: 0 0 5px 0; }
        .hub-desc { font-size: 0.85em; color: #666; margin: 0; }
        .stats-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 15px; }
        .stat-box { background: #f8f9fa; padding: 12px; border-radius: 8px; text-align: center; }
        .stat-value { font-size: 1.5em; font-weight: 700; color: #1a237e; }
        .stat-label { font-size: 0.75em; color: #666; margin-top: 2px; }
        .top-countries { margin-top: 10px; }
        .top-countries h4 { font-size: 0.9em; color: #333; margin-bottom: 8px; }
        .country-list { list-style: none; padding: 0; }
        .country-item { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #eee; font-size: 0.85em; }
        .country-item:last-child { border-bottom: none; }
        .flag { margin-right: 5px; }
        .status-healthy { color: #2e7d32; }
        .status-degraded { color: #ed6c02; }
        .status-unknown { color: #9e9e9e; }
        .powered-by { text-align: center; margin-top: 15px; font-size: 0.7em; color: #999; }
        .powered-by a { color: #666; text-decoration: none; }
        .powered-by a:hover { text-decoration: underline; }
        .loading { text-align: center; padding: 20px; color: #666; }
        .error { color: #c62828; text-align: center; padding: 10px; }
    </style>
</head>
<body>
    <div class="embed-container" id="embed-content">
        <div class="loading">Loading hub stats...</div>
    </div>

    <script>
        const API_BASE = '/api/v1';

        function formatBytes(bytes) {
            if (!bytes) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
            let i = 0;
            while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
            return bytes.toFixed(1) + ' ' + units[i];
        }

        function getFlagEmoji(cc) {
            if (!cc || cc.length !== 2) return '&#x1F310;';
            return String.fromCodePoint(...[...cc.toUpperCase()].map(c => 0x1F1E6 + c.charCodeAt(0) - 65));
        }

        function escapeHtml(str) {
            if (!str) return '';
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        async function loadStats() {
            try {
                const [info, stats, geo, health] = await Promise.all([
                    fetch(API_BASE + '/hub/info').then(r => r.ok ? r.json() : {}),
                    fetch(API_BASE + '/stats/stats').then(r => r.ok ? r.json() : {}),
                    fetch(API_BASE + '/stats/geo').then(r => r.ok ? r.json() : { distribution: [] }),
                    fetch(API_BASE + '/stats/health').then(r => r.ok ? r.json() : { status: 'unknown' }).catch(() => ({ status: 'unknown' }))
                ]);

                const topCountries = (geo.distribution || []).slice(0, 5);

                let html = `
                    <div class="hub-header">
                        <img src="https://avatars1.githubusercontent.com/u/1856420?v=3&s=300"
                             alt="Verlihub"
                             style="width: 48px; height: 48px; border-radius: 50%; box-shadow: 0 2px 8px rgba(0,0,0,0.15); border: 2px solid #1a237e; margin-bottom: 8px;">
                        <h2 class="hub-name">${escapeHtml(info.name || 'Verlihub')}</h2>
                        ${info.description ? `<p class="hub-desc">${escapeHtml(info.description)}</p>` : ''}
                    </div>
                    <div class="stats-grid">
                        <div class="stat-box">
                            <div class="stat-value">${stats.users_online || 0}</div>
                            <div class="stat-label">Users Online</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-value">${stats.total_share_formatted || formatBytes(stats.total_share || 0)}</div>
                            <div class="stat-label">Total Share</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-value">${stats.operators_online || 0}</div>
                            <div class="stat-label">Operators</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-value status-${health.status}">${health.status || 'unknown'}</div>
                            <div class="stat-label">Status</div>
                        </div>
                    </div>`;

                if (topCountries.length > 0) {
                    html += `
                        <div class="top-countries">
                            <h4>Top Countries</h4>
                            <ul class="country-list">`;
                    for (const c of topCountries) {
                        html += `<li class="country-item"><span><span class="flag">${getFlagEmoji(c.country_code)}</span>${escapeHtml(c.country_name || c.country_code)}</span><span>${c.users}</span></li>`;
                    }
                    html += `</ul></div>`;
                }

                html += `<div class="powered-by">Powered by <a href="/dashboard/spa" target="_blank">Verlihub</a></div>`;

                document.getElementById('embed-content').innerHTML = html;
            } catch (err) {
                document.getElementById('embed-content').innerHTML = `<div class="error">Failed to load: ${escapeHtml(err.message)}</div>`;
            }
        }

        // Initial load
        loadStats();

        // Auto-refresh every 60 seconds
        setInterval(loadStats, 60000);
    </script>
</body>
</html>
'''


@dashboard_router.get("/spa", response_class=HTMLResponse)
async def spa_dashboard(request: Request):
    """
    Single-Page Application dashboard.
    
    No authentication required - public view of hub statistics.
    Full-featured dashboard with tabs for Hub, Users, Countries, Cities, ASNs, IPs.
    """
    return HTMLResponse(content=SPA_DASHBOARD_HTML)


@dashboard_router.get("/embed", response_class=HTMLResponse)
async def embed_dashboard(request: Request):
    """
    Embeddable mini dashboard for iframe embedding.
    
    No authentication required - compact view of hub statistics.
    Designed to be embedded in external websites via iframe.
    
    Example usage:
        <iframe src="https://your-hub.com/dashboard/embed" width="400" height="400" frameborder="0"></iframe>
    """
    return HTMLResponse(content=EMBED_DASHBOARD_HTML)


# Utility functions

def _format_bytes(size: int) -> str:
    """Format bytes to human-readable size."""
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} EB"


def _format_uptime(seconds: int) -> str:
    """Format seconds to human-readable uptime."""
    if seconds < 60:
        return f"{seconds}s"
    
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m {seconds % 60}s"
    
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60}m"
    
    days = hours // 24
    return f"{days}d {hours % 24}h"
