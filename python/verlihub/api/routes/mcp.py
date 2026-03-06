"""
In-process MCP endpoint — Model Context Protocol over Streamable HTTP.

Mounts at ``/api/v1/mcp`` inside the main FastAPI application.  Uses the
same JWT authentication and user-class permissions as the rest of the API,
so there is no separate credential management.

The MCP server talks directly to the live hub context (no REST round-trip).

Read-only tools require ``mcp.min_class`` (default 3 / Operator).
Write tools (kick, ban, broadcast, etc.) require ``mcp.admin_class``
(default 5 / Admin).
"""
from __future__ import annotations

import contextlib
import json
import logging
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import Response

from verlihub.api.auth import TokenData, decode_token
from verlihub.api.deps import get_hub_context
from verlihub.config import McpConfig, get_config_optional

logger = logging.getLogger("verlihub.api.mcp")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_mcp_config() -> McpConfig:
    cfg = get_config_optional()
    return cfg.mcp if cfg else McpConfig()


def _format_bytes(b: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} EiB"


# ---------------------------------------------------------------------------
# MCP server builder — uses hub context directly (no REST client)
# ---------------------------------------------------------------------------

def build_inprocess_mcp_server(server_name: str = "verlihub"):
    """
    Build an MCP ``Server`` that operates against the live hub context.

    Each request's JWT is stashed in a context-var so tool handlers can
    check the caller's ``user_class``.
    """
    from mcp.server import Server
    from mcp.types import (
        Resource,
        Tool,
        TextContent,
        Prompt,
        PromptArgument,
        PromptMessage,
    )

    import contextvars
    _current_user: contextvars.ContextVar[Optional[TokenData]] = contextvars.ContextVar(
        "_current_user", default=None,
    )

    server = Server(server_name)

    # Expose the context-var so the auth middleware can set it.
    server._current_user = _current_user          # type: ignore[attr-defined]

    def _is_admin() -> bool:
        user = _current_user.get()
        if user is None:
            return False
        mcp_cfg = _get_mcp_config()
        return user.user_class >= mcp_cfg.admin_class

    # =================================================================
    # Resources
    # =================================================================
    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(uri="hub://info", name="Hub Information",
                     description="Basic hub info — name, topic, user count, share total",
                     mimeType="application/json"),
            Resource(uri="hub://users", name="Online Users",
                     description="List of currently connected users",
                     mimeType="application/json"),
            Resource(uri="hub://stats", name="Hub Statistics",
                     description="Detailed hub statistics and health metrics",
                     mimeType="application/json"),
            Resource(uri="hub://bans", name="Active Bans",
                     description="List of currently active bans",
                     mimeType="application/json"),
        ]

    @server.read_resource()
    async def read_resource(uri):
        from mcp.server.lowlevel.helper_types import ReadResourceContents as RRC

        ctx = get_hub_context()
        if ctx is None:
            return [RRC(content=json.dumps({"error": "Hub not running"}), mime_type="application/json")]

        uri_str = str(uri)

        if uri_str == "hub://info":
            cfg = get_config_optional()
            text = json.dumps({
                "name": ctx.hub_name, "topic": ctx.hub_topic,
                "description": cfg.hub.description if cfg else "",
                "version": "1.7.0.0",
                "users_online": ctx.user_count,
                "total_share_bytes": ctx.total_share,
                "total_share_formatted": _format_bytes(ctx.total_share),
                "uptime_seconds": ctx.uptime,
                "is_running": ctx.is_running,
            }, default=str)
        elif uri_str == "hub://users":
            users = ctx.get_user_list() or []
            is_adm = _is_admin()
            result = []
            for u in users:
                entry = {
                    "nick": u.get("nick", ""),
                    "country_code": u.get("country_code", ""),
                    "share_bytes": u.get("share", 0),
                    "share_formatted": _format_bytes(u.get("share", 0)),
                    "user_class": u.get("user_class", 0),
                    "client": u.get("client", ""),
                }
                if is_adm:
                    entry["ip"] = u.get("ip", "")
                result.append(entry)
            text = json.dumps(result, default=str)
        elif uri_str == "hub://stats":
            users = ctx.get_user_list() or []
            classes: dict[int, int] = {}
            for u in users:
                c = u.get("user_class", 0)
                classes[c] = classes.get(c, 0) + 1
            text = json.dumps({
                "users_online": ctx.user_count,
                "total_share": _format_bytes(ctx.total_share),
                "uptime_seconds": ctx.uptime,
                "is_running": ctx.is_running,
                "users_by_class": classes,
            }, default=str)
        elif uri_str == "hub://bans":
            try:
                from verlihub.models.database import get_async_session
                from verlihub.models import Ban
                from sqlmodel import select
                async with get_async_session() as session:
                    result = await session.execute(select(Ban).limit(200))
                    bans = result.scalars().all()
                    text = json.dumps([
                        {"id": b.id, "nick": b.nick, "ip": b.ip,
                         "reason": b.reason, "type": b.ban_type}
                        for b in bans
                    ], default=str)
            except Exception as exc:
                text = json.dumps({"error": f"Failed to read bans: {exc}"})
        else:
            text = json.dumps({"error": f"Unknown resource: {uri_str}"})

        return [RRC(content=text, mime_type="application/json")]

    # =================================================================
    # Tools
    # =================================================================
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [
            # --- Read-only ---
            Tool(name="get_hub_info",
                 description="Get basic hub information (name, topic, user count, share total, version)",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="list_online_users",
                 description="List all currently connected users with details",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="get_user_info",
                 description="Get detailed information about a specific user",
                 inputSchema={"type": "object",
                              "properties": {"nick": {"type": "string", "description": "User nickname"}},
                              "required": ["nick"]}),
            Tool(name="get_hub_statistics",
                 description="Get comprehensive hub statistics (uptime, bandwidth, connection counts)",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="get_share_statistics",
                 description="Get file-sharing statistics (total share, average per user)",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="get_geo_distribution",
                 description="Get geographic distribution of connected users by country",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="list_operators",
                 description="List currently online hub operators and admins",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="list_bots",
                 description="List hub bots",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
            Tool(name="search_bans",
                 description="Search active bans, optionally filtered by keyword",
                 inputSchema={"type": "object",
                              "properties": {
                                  "query": {"type": "string",
                                            "description": "Search keyword (nick, IP, reason). Leave empty for all bans."},
                                  "limit": {"type": "integer", "description": "Max results (default 50)"},
                              }, "required": []}),
            Tool(name="health_check",
                 description="Run a hub health check",
                 inputSchema={"type": "object", "properties": {}, "required": []}),
        ]

        # Admin-only tools shown only when caller has sufficient class
        if _is_admin():
            tools.extend([
                Tool(name="kick_user",
                     description="Kick a user from the hub",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "nick": {"type": "string", "description": "Nickname to kick"},
                                      "reason": {"type": "string", "description": "Reason for the kick"},
                                  }, "required": ["nick", "reason"]}),
                Tool(name="send_broadcast",
                     description="Send a broadcast message to all connected users",
                     inputSchema={"type": "object",
                                  "properties": {"message": {"type": "string", "description": "Message text"}},
                                  "required": ["message"]}),
                Tool(name="send_message_to_user",
                     description="Send a private message to a specific user",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "nick": {"type": "string", "description": "Recipient nickname"},
                                      "message": {"type": "string", "description": "Message text"},
                                  }, "required": ["nick", "message"]}),
                Tool(name="ban_user",
                     description="Ban a user from the hub",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "nick": {"type": "string", "description": "Nickname to ban"},
                                      "reason": {"type": "string", "description": "Reason for the ban"},
                                  }, "required": ["nick", "reason"]}),
            ])

        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = await _dispatch_tool(name, arguments)
            text = json.dumps(result, indent=2, default=str) if isinstance(result, (dict, list)) else str(result)
            return [TextContent(type="text", text=text)]
        except Exception as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]

    async def _dispatch_tool(name: str, args: dict[str, Any]) -> Any:
        ctx = get_hub_context()
        user = _current_user.get()
        is_adm = _is_admin()

        # --- Read-only tools ---
        if name == "get_hub_info":
            if ctx is None:
                return {"error": "Hub not running"}
            cfg = get_config_optional()
            return {
                "name": ctx.hub_name, "topic": ctx.hub_topic,
                "description": cfg.hub.description if cfg else "",
                "version": "1.7.0.0",
                "users_online": ctx.user_count,
                "total_share_bytes": ctx.total_share,
                "total_share_formatted": _format_bytes(ctx.total_share),
                "uptime_seconds": ctx.uptime,
                "is_running": ctx.is_running,
            }

        elif name == "list_online_users":
            if ctx is None:
                return []
            users = ctx.get_user_list() or []
            result = []
            for u in users:
                entry = {
                    "nick": u.get("nick", ""),
                    "country_code": u.get("country_code", ""),
                    "share_bytes": u.get("share", 0),
                    "share_formatted": _format_bytes(u.get("share", 0)),
                    "user_class": u.get("user_class", 0),
                    "client": u.get("client", ""),
                }
                if is_adm:
                    entry["ip"] = u.get("ip", "")
                result.append(entry)
            return result

        elif name == "get_user_info":
            if ctx is None:
                return {"error": "Hub not running"}
            nick = args.get("nick", "")
            info = ctx.get_user_info(nick)
            if info is None:
                return {"error": f"User '{nick}' not found or not online"}
            result = dict(info)
            if not is_adm:
                result.pop("ip", None)
                result.pop("hostname", None)
            return result

        elif name == "get_hub_statistics":
            if ctx is None:
                return {"error": "Hub not running"}
            users = ctx.get_user_list() or []
            classes: dict[int, int] = {}
            for u in users:
                c = u.get("user_class", 0)
                classes[c] = classes.get(c, 0) + 1
            return {
                "users_online": ctx.user_count,
                "total_share": _format_bytes(ctx.total_share),
                "uptime_seconds": ctx.uptime,
                "is_running": ctx.is_running,
                "users_by_class": classes,
            }

        elif name == "get_share_statistics":
            if ctx is None:
                return {}
            users = ctx.get_user_list() or []
            shares = [u.get("share", 0) for u in users]
            total = sum(shares)
            avg = total // len(shares) if shares else 0
            return {
                "total_share": _format_bytes(total),
                "total_share_bytes": total,
                "average_share": _format_bytes(avg),
                "user_count": len(users),
            }

        elif name == "get_geo_distribution":
            if ctx is None:
                return {}
            users = ctx.get_user_list() or []
            geo: dict[str, int] = {}
            for u in users:
                cc = u.get("country_code", "??")
                geo[cc] = geo.get(cc, 0) + 1
            sorted_geo = sorted(geo.items(), key=lambda x: x[1], reverse=True)
            return [{"country": cc, "users": cnt} for cc, cnt in sorted_geo]

        elif name == "list_operators":
            if ctx is None:
                return []
            users = ctx.get_user_list() or []
            return [
                {"nick": u.get("nick"), "class": u.get("user_class")}
                for u in users if u.get("user_class", 0) >= 3
            ]

        elif name == "list_bots":
            if ctx is None:
                return []
            try:
                return ctx.get_bot_list() if hasattr(ctx, "get_bot_list") else []
            except Exception:
                return []

        elif name == "search_bans":
            try:
                from verlihub.models.database import get_async_session
                from verlihub.models import Ban
                from sqlmodel import select

                limit = args.get("limit", 50)
                query_str = args.get("query", "").lower()
                async with get_async_session() as session:
                    stmt = select(Ban).limit(limit)
                    result = await session.execute(stmt)
                    bans = result.scalars().all()
                    bans_list = [
                        {"id": b.id, "nick": b.nick, "ip": b.ip,
                         "reason": b.reason, "type": b.ban_type}
                        for b in bans
                    ]
                    if query_str:
                        bans_list = [
                            b for b in bans_list
                            if query_str in json.dumps(b, default=str).lower()
                        ]
                    return bans_list
            except Exception as exc:
                return {"error": f"Ban search failed: {exc}"}

        elif name == "health_check":
            running = ctx is not None and ctx.is_running
            return {
                "hub_running": running,
                "users_online": ctx.user_count if ctx else 0,
                "uptime_seconds": ctx.uptime if ctx else 0,
            }

        # --- Admin tools ---
        elif name == "kick_user":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            nick = args.get("nick", "")
            reason = args.get("reason", "Kicked via MCP")
            caller = user.nick if user else "MCP"
            try:
                ctx.kick_user(caller, nick, reason)
                return {"success": True, "kicked": nick, "reason": reason}
            except Exception as exc:
                return {"error": f"Kick failed: {exc}"}

        elif name == "send_broadcast":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            try:
                ctx.send_to_all(args.get("message", ""))
                return {"success": True}
            except Exception as exc:
                return {"error": f"Broadcast failed: {exc}"}

        elif name == "send_message_to_user":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            try:
                ctx.send_to_user(args.get("nick", ""), args.get("message", ""))
                return {"success": True}
            except Exception as exc:
                return {"error": f"Send failed: {exc}"}

        elif name == "ban_user":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            nick = args.get("nick", "")
            reason = args.get("reason", "Banned via MCP")
            caller = user.nick if user else "MCP"
            try:
                ctx.kick_user(caller, nick, f"[BAN] {reason}")
                return {"success": True, "banned": nick, "reason": reason}
            except Exception as exc:
                return {"error": f"Ban failed: {exc}"}

        else:
            return {"error": f"Unknown tool: {name}"}

    # =================================================================
    # Prompts
    # =================================================================
    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return [
            Prompt(name="hub_report",
                   description="Generate a comprehensive hub status report",
                   arguments=[]),
            Prompt(name="user_lookup",
                   description="Look up everything about a specific user",
                   arguments=[PromptArgument(
                       name="nick", description="User nickname to look up", required=True)]),
            Prompt(name="troubleshoot",
                   description="Diagnose potential hub issues",
                   arguments=[PromptArgument(
                       name="symptom", description="Describe the issue (slow, drops, errors...)", required=True)]),
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None = None):
        from mcp.types import GetPromptResult
        arguments = arguments or {}
        messages = []
        if name == "hub_report":
            messages = [PromptMessage(role="user", content=TextContent(type="text", text=(
                "Generate a comprehensive status report for this Verlihub DC++ hub. "
                "Use the get_hub_info, get_hub_statistics, list_online_users, list_operators, "
                "and get_share_statistics tools to gather data. Include:\n"
                "1. Hub overview (name, topic, uptime)\n"
                "2. User statistics (online, peak, by class)\n"
                "3. Share statistics (total, average)\n"
                "4. Geographic distribution\n"
                "5. Active operators\n"
                "6. Any potential issues or anomalies"
            )))]
        elif name == "user_lookup":
            nick = arguments.get("nick", "unknown")
            messages = [PromptMessage(role="user", content=TextContent(type="text", text=(
                f"Look up everything about the user '{nick}' on this Verlihub DC++ hub. "
                f"Use get_user_info to check if they're online, search_bans to check "
                f"ban history. Report their connection details, share size, class, and any bans."
            )))]
        elif name == "troubleshoot":
            symptom = arguments.get("symptom", "general issues")
            messages = [PromptMessage(role="user", content=TextContent(type="text", text=(
                f"Help troubleshoot this Verlihub DC++ hub. The reported symptom is: "
                f"'{symptom}'. Use health_check, get_hub_statistics, list_online_users, "
                f"and search_bans to diagnose. Check for:\n"
                f"1. Resource issues (high user count, bandwidth)\n"
                f"2. Connectivity (hub running, health endpoint)\n"
                f"3. Abuse (ban spikes, repeated kicks)\n"
                f"4. Configuration problems"
            )))]
        return GetPromptResult(messages=messages)

    return server


# ---------------------------------------------------------------------------
# Auth middleware — extract JWT from the incoming request and set the
# context-var before the MCP session manager processes the message.
# ---------------------------------------------------------------------------

class _McpAuthMiddleware:
    """
    ASGI middleware that sits in front of the MCP session manager.

    - Extracts the JWT from ``Authorization: Bearer <token>`` or cookie.
    - Validates the token and checks ``min_class``.
    - Injects the ``TokenData`` into the MCP server's context-var so that
      tool handlers can inspect the caller's permission level.
    - Returns 401/403 on auth failure.
    """

    def __init__(self, app, *, mcp_server):
        self._app = app
        self._mcp_server = mcp_server

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self._app(scope, receive, send)

        request = Request(scope)

        # --- Extract token ---
        token_str: Optional[str] = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token_str = auth_header[7:]
        else:
            cookie_val = request.cookies.get("access_token", "")
            if cookie_val.startswith("Bearer "):
                token_str = cookie_val[7:]
            elif cookie_val:
                token_str = cookie_val

        if not token_str:
            response = Response("Not authenticated", status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
            return await response(scope, receive, send)

        token_data = decode_token(token_str)
        if token_data is None:
            response = Response("Invalid or expired token", status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
            return await response(scope, receive, send)

        # --- Check min_class ---
        mcp_cfg = _get_mcp_config()
        if token_data.user_class < mcp_cfg.min_class:
            response = Response(
                f"Forbidden — requires user class >= {mcp_cfg.min_class}",
                status_code=403,
            )
            return await response(scope, receive, send)

        # --- Set context-var and forward ---
        ctx_var = self._mcp_server._current_user  # type: ignore[attr-defined]
        token = ctx_var.set(token_data)
        try:
            await self._app(scope, receive, send)
        finally:
            ctx_var.reset(token)


# ---------------------------------------------------------------------------
# Public factory — called from app.py to get the ASGI app to mount
# ---------------------------------------------------------------------------

_session_manager = None
_mcp_server = None
_authed_app = None


def create_mcp_mount():
    """
    Return an ASGI app suitable for ``app.mount("/api/v1/mcp", ...)``.

    Idempotent — repeated calls return the same objects.

    Returns ``(authed_asgi_app, session_manager)`` or ``(None, None)`` if
    the MCP SDK is not installed.
    """
    global _session_manager, _mcp_server, _authed_app

    if _authed_app is not None:
        return _authed_app, _session_manager

    try:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    except ImportError:
        logger.info("mcp package not installed — skipping in-process MCP endpoint")
        return None, None

    _mcp_server = build_inprocess_mcp_server()

    _session_manager = StreamableHTTPSessionManager(
        app=_mcp_server,
        json_response=False,
        stateless=True,
    )

    _authed_app = _McpAuthMiddleware(
        _session_manager.handle_request,
        mcp_server=_mcp_server,
    )

    return _authed_app, _session_manager
