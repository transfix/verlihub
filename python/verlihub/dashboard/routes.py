"""
Dashboard routes for the Verlihub web admin interface.

Provides HTML pages for:
- Login/authentication
- Main dashboard with hub status
- User management
- Ban management
- Configuration
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response, HTTPException, status, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from verlihub.api.auth import decode_token, TokenData
from verlihub.api.deps import get_hub_context
from verlihub.models import RegUser

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
    return {
        "request": request,
        "user": user,
        "hub_running": ctx.is_running if ctx else False,
        "hub_name": ctx.hub_name if ctx else "Verlihub",
        "current_year": datetime.now(timezone.utc).year,
        "version": "0.1.0",
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
    context.update({
        "user_count": ctx.user_count if ctx else 0,
        "share_size": _format_bytes(ctx.total_share if ctx else 0),
        "uptime": _format_uptime(ctx.uptime if ctx else 0),
        "hub_port": ctx.port if ctx else 411,
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
    except Exception:
        return RedirectResponse(
            url=f"/dashboard/login?error=Authentication+failed&next_url={next_url}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Create JWT token
    token = create_access_token(user.nick, user.class_)
    
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
    
    # Check permission (class >= 5 for admin)
    if user.user_class < 5:
        raise HTTPException(status_code=403, detail="Admin access required")
    
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
    
    if user.user_class < 3:  # Operator or higher
        raise HTTPException(status_code=403, detail="Operator access required")
    
    context = get_base_context(request, user)
    
    # Get bans from database
    from verlihub.models.database import get_async_session
    from verlihub.models import BanItem
    from sqlmodel import select
    
    per_page = 50
    offset = (page - 1) * per_page
    
    try:
        async with get_async_session() as session:
            query = select(BanItem)
            if search:
                query = query.where(
                    BanItem.nick.contains(search) | BanItem.ip.contains(search)
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
    
    if user.user_class < 10:  # Master only
        raise HTTPException(status_code=403, detail="Master access required")
    
    ctx = get_hub_context()
    context = get_base_context(request, user)
    
    # Get current configuration
    config = {}
    if ctx:
        try:
            config = ctx.get_config() or {}
        except Exception:
            pass
    
    context["config"] = config
    
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
    
    if user.user_class < 5:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    context = get_base_context(request, user)
    context["logs"] = []  # TODO: Implement log retrieval
    
    return templates.TemplateResponse(request, "logs.html", context)


@dashboard_router.get("/console", response_class=HTMLResponse)
async def console_page(
    request: Request,
    user: Optional[TokenData] = Depends(get_user_from_cookie),
    access_token: Optional[str] = Cookie(default=None),
):
    """Hub command console page."""
    if user is None:
        return RedirectResponse(
            url="/dashboard/login?next_url=/dashboard/console",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Require operator or higher (class >= 3)
    if user.user_class < 3:
        raise HTTPException(status_code=403, detail="Operator access required")
    
    ctx = get_hub_context()
    context = get_base_context(request, user)
    
    # Pass the token for API calls
    token = access_token[7:] if access_token and access_token.startswith("Bearer ") else access_token
    context["auth_token"] = token or ""
    
    return templates.TemplateResponse(request, "console.html", context)


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
    
    # Require admin or higher (class >= 5)
    if user.user_class < 5:
        raise HTTPException(status_code=403, detail="Admin access required")
    
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
    
    context["plugins"] = plugins
    context["scripts"] = scripts
    
    return templates.TemplateResponse(request, "plugins.html", context)


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
