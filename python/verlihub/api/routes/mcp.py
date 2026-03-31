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
            # --- Phase 5.8: New MCP Resources ---
            Resource(uri="hub://plugins", name="Loaded Plugins",
                     description="List of loaded plugins and scripts",
                     mimeType="application/json"),
            Resource(uri="hub://penalties", name="Active Penalties",
                     description="Current penalty restrictions by user",
                     mimeType="application/json"),
            Resource(uri="hub://protocol_stats", name="Protocol Statistics",
                     description="Message counters and throughput",
                     mimeType="application/json"),
            Resource(uri="hub://triggers", name="Triggers",
                     description="Configured auto-response triggers",
                     mimeType="application/json"),
            Resource(uri="hub://flood_config", name="Flood Config",
                     description="Current flood protection settings",
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

        elif uri_str == "hub://plugins":
            plugins = ctx.get_loaded_plugins() if ctx else []
            lua = ctx.get_loaded_lua_scripts() if ctx else []
            py = ctx.get_loaded_python_scripts() if ctx else []
            text = json.dumps({"plugins": plugins, "lua_scripts": lua, "python_scripts": py}, default=str)

        elif uri_str == "hub://penalties":
            try:
                from verlihub.penalty_service import get_penalty_service
                svc = get_penalty_service()
                penalties = svc.get_active_penalties()
                text = json.dumps([p.to_dict() if hasattr(p, "to_dict") else vars(p) for p in penalties], default=str)
            except Exception as exc:
                text = json.dumps({"error": f"Failed to read penalties: {exc}"})

        elif uri_str == "hub://protocol_stats":
            if ctx is None:
                text = json.dumps({"error": "Hub not running"})
            else:
                text = json.dumps(ctx.get_protocol_stats(), default=str)

        elif uri_str == "hub://triggers":
            try:
                from verlihub.trigger_service import get_trigger_cache
                cache = get_trigger_cache()
                text = json.dumps([t.to_dict() if hasattr(t, "to_dict") else vars(t) for t in cache.get_all()], default=str)
            except Exception as exc:
                text = json.dumps({"error": f"Failed to read triggers: {exc}"})

        elif uri_str == "hub://flood_config":
            if ctx is None:
                text = json.dumps({"error": "Hub not running"})
            else:
                flood_types = ["Chat", "PM", "Search", "MyINFO", "CTM", "ExtJSON"]
                config = {}
                for i, name in enumerate(flood_types):
                    period, tokens = ctx.get_flood_config(i)
                    config[name] = {"period_ms": period, "max_tokens": tokens}
                text = json.dumps(config)

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
                # --- Phase 5.1: Messaging tools ---
                Tool(name="send_to_opchat",
                     description="Send a message to the operator chat channel",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "message": {"type": "string", "description": "Message text"},
                                      "from_nick": {"type": "string", "description": "Sender nick (optional)"},
                                  }, "required": ["message"]}),
                Tool(name="send_to_class",
                     description="Send a message to users in a user-class range",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "message": {"type": "string", "description": "Message text"},
                                      "min_class": {"type": "integer", "description": "Minimum user class"},
                                      "max_class": {"type": "integer", "description": "Maximum user class"},
                                  }, "required": ["message", "min_class", "max_class"]}),
                Tool(name="send_to_active",
                     description="Send a message to all active-mode users",
                     inputSchema={"type": "object",
                                  "properties": {"message": {"type": "string", "description": "Message text"}},
                                  "required": ["message"]}),
                Tool(name="send_to_passive",
                     description="Send a message to all passive-mode users",
                     inputSchema={"type": "object",
                                  "properties": {"message": {"type": "string", "description": "Message text"}},
                                  "required": ["message"]}),
                Tool(name="broadcast_chat",
                     description="Broadcast a chat message appearing as a specific nick",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "from_nick": {"type": "string", "description": "Nick the message appears from"},
                                      "message": {"type": "string", "description": "Message text"},
                                  }, "required": ["from_nick", "message"]}),
                Tool(name="send_pm_as",
                     description="Send a private message from one nick to another",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "from_nick": {"type": "string", "description": "Sender nick"},
                                      "to_nick": {"type": "string", "description": "Recipient nick"},
                                      "message": {"type": "string", "description": "Message text"},
                                  }, "required": ["from_nick", "to_nick", "message"]}),
                # --- Phase 5.2: Administration tools ---
                Tool(name="force_move",
                     description="Redirect a user to another hub address",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "nick": {"type": "string", "description": "User to redirect"},
                                      "address": {"type": "string", "description": "Target hub address"},
                                  }, "required": ["nick", "address"]}),
                Tool(name="disconnect_user",
                     description="Disconnect a user from the hub without redirect",
                     inputSchema={"type": "object",
                                  "properties": {"nick": {"type": "string", "description": "User to disconnect"}},
                                  "required": ["nick"]}),
                Tool(name="add_robot",
                     description="Register a bot on the hub",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "nick": {"type": "string", "description": "Bot nickname"},
                                      "description": {"type": "string", "description": "Bot description"},
                                      "user_class": {"type": "integer", "description": "Bot user class (default 3)"},
                                  }, "required": ["nick"]}),
                Tool(name="remove_robot",
                     description="Remove a bot from the hub",
                     inputSchema={"type": "object",
                                  "properties": {"nick": {"type": "string", "description": "Bot name to remove"}},
                                  "required": ["nick"]}),
                Tool(name="set_hub_topic",
                     description="Set the hub topic shown in client title bars",
                     inputSchema={"type": "object",
                                  "properties": {"topic": {"type": "string", "description": "New hub topic"}},
                                  "required": ["topic"]}),
                Tool(name="get_hub_config",
                     description="Read a hub configuration value",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "section": {"type": "string", "description": "Config section"},
                                      "key": {"type": "string", "description": "Config key"},
                                  }, "required": ["section", "key"]}),
                Tool(name="set_hub_config",
                     description="Set a hub configuration value",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "section": {"type": "string", "description": "Config section"},
                                      "key": {"type": "string", "description": "Config key"},
                                      "value": {"type": "string", "description": "New value"},
                                  }, "required": ["section", "key", "value"]}),
                Tool(name="reload_config",
                     description="Request a hub configuration reload",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                # --- Phase 5.3: Statistics & GeoIP ---
                Tool(name="get_protocol_stats",
                     description="Get protocol-level message counters (chat, PM, search, flood, ban blocked)",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                Tool(name="lookup_geoip",
                     description="Look up GeoIP data for a specific IP address",
                     inputSchema={"type": "object",
                                  "properties": {"ip": {"type": "string", "description": "IP address to look up"}},
                                  "required": ["ip"]}),
                Tool(name="get_active_passive_counts",
                     description="Get active and passive user counts",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                # --- Phase 5.4: Plugin & Script Management ---
                Tool(name="list_plugins",
                     description="List loaded native plugins",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                Tool(name="load_plugin",
                     description="Load a native plugin from a file path",
                     inputSchema={"type": "object",
                                  "properties": {"plugin_path": {"type": "string", "description": "Path to plugin"}},
                                  "required": ["plugin_path"]}),
                Tool(name="unload_plugin",
                     description="Unload a native plugin by name",
                     inputSchema={"type": "object",
                                  "properties": {"plugin_name": {"type": "string", "description": "Plugin name"}},
                                  "required": ["plugin_name"]}),
                Tool(name="reload_plugin",
                     description="Reload a native plugin by name",
                     inputSchema={"type": "object",
                                  "properties": {"plugin_name": {"type": "string", "description": "Plugin name"}},
                                  "required": ["plugin_name"]}),
                Tool(name="list_lua_scripts",
                     description="List loaded Lua scripts",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                Tool(name="load_lua_script",
                     description="Load a Lua script",
                     inputSchema={"type": "object",
                                  "properties": {"script_path": {"type": "string", "description": "Path to Lua script"}},
                                  "required": ["script_path"]}),
                Tool(name="unload_lua_script",
                     description="Unload a Lua script",
                     inputSchema={"type": "object",
                                  "properties": {"script_path": {"type": "string", "description": "Path to Lua script"}},
                                  "required": ["script_path"]}),
                Tool(name="list_python_scripts",
                     description="List loaded Python scripts",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                Tool(name="load_python_script",
                     description="Load a Python script",
                     inputSchema={"type": "object",
                                  "properties": {"script_path": {"type": "string", "description": "Path to Python script"}},
                                  "required": ["script_path"]}),
                Tool(name="unload_python_script",
                     description="Unload a Python script",
                     inputSchema={"type": "object",
                                  "properties": {"script_path": {"type": "string", "description": "Path to Python script"}},
                                  "required": ["script_path"]}),
                # --- Phase 5.5: Flood & Ban Cache ---
                Tool(name="set_flood_config",
                     description="Set flood protection for a message type (0=Chat,1=PM,2=Search,3=MyINFO,4=CTM,5=ExtJSON)",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "flood_type": {"type": "integer", "description": "Type index (0-5)"},
                                      "period_ms": {"type": "integer", "description": "Period in milliseconds"},
                                      "max_tokens": {"type": "integer", "description": "Max tokens (burst)"},
                                  }, "required": ["flood_type", "period_ms", "max_tokens"]}),
                Tool(name="sync_ban_cache",
                     description="Reload the ban cache from the database",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                Tool(name="add_ban_cache_ip",
                     description="Add an IP address to the ban cache",
                     inputSchema={"type": "object",
                                  "properties": {"ip": {"type": "string", "description": "IP to ban-cache"}},
                                  "required": ["ip"]}),
                Tool(name="add_ban_cache_nick",
                     description="Add a nickname to the ban cache",
                     inputSchema={"type": "object",
                                  "properties": {"nick": {"type": "string", "description": "Nick to ban-cache"}},
                                  "required": ["nick"]}),
                Tool(name="clear_ban_cache",
                     description="Clear all entries from the ban cache",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                # --- Phase 5.6: Penalty Management ---
                Tool(name="list_penalties",
                     description="List active penalties (optional nick filter)",
                     inputSchema={"type": "object",
                                  "properties": {"nick": {"type": "string", "description": "Filter by nick (optional)"}},
                                  "required": []}),
                Tool(name="add_penalty",
                     description="Apply a penalty to a user",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "nick": {"type": "string", "description": "Target nick"},
                                      "penalty_type": {"type": "string", "description": "Type: chat_gag, search_ban, pm_ban, ctm_ban"},
                                      "reason": {"type": "string", "description": "Reason"},
                                      "duration_minutes": {"type": "integer", "description": "Duration in minutes (0=permanent)"},
                                  }, "required": ["nick", "penalty_type"]}),
                Tool(name="remove_penalty",
                     description="Remove a penalty from a user",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "nick": {"type": "string", "description": "Target nick"},
                                      "penalty_type": {"type": "string", "description": "Type to remove (optional, removes all if empty)"},
                                  }, "required": ["nick"]}),
                Tool(name="cleanup_penalties",
                     description="Remove all expired penalties",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                # --- Phase 5.7: Triggers & Redirects ---
                Tool(name="list_triggers",
                     description="List configured auto-response triggers",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                Tool(name="add_trigger",
                     description="Add a new trigger",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "command": {"type": "string", "description": "Trigger command (e.g. !rules)"},
                                      "response": {"type": "string", "description": "Response text"},
                                      "min_class": {"type": "integer", "description": "Minimum class to trigger (default 0)"},
                                  }, "required": ["command", "response"]}),
                Tool(name="remove_trigger",
                     description="Remove a trigger by its command",
                     inputSchema={"type": "object",
                                  "properties": {"command": {"type": "string", "description": "Trigger command to remove"}},
                                  "required": ["command"]}),
                Tool(name="list_redirects",
                     description="List configured redirect rules",
                     inputSchema={"type": "object", "properties": {}, "required": []}),
                Tool(name="add_redirect",
                     description="Add a redirect rule",
                     inputSchema={"type": "object",
                                  "properties": {
                                      "address": {"type": "string", "description": "Target hub address"},
                                      "flag": {"type": "integer", "description": "Redirect trigger bitmask"},
                                      "enabled": {"type": "boolean", "description": "Whether enabled (default true)"},
                                  }, "required": ["address"]}),
                Tool(name="remove_redirect",
                     description="Remove a redirect rule by address",
                     inputSchema={"type": "object",
                                  "properties": {"address": {"type": "string", "description": "Address to remove"}},
                                  "required": ["address"]}),
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

        # --- Phase 5.1: Messaging ---
        elif name == "send_to_opchat":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.send_to_opchat(args.get("message", ""), args.get("from_nick", ""))
            return {"success": True}

        elif name == "send_to_class":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.send_to_class(args["message"], args["min_class"], args["max_class"])
            return {"success": True}

        elif name == "send_to_active":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.send_to_active(args["message"])
            return {"success": True}

        elif name == "send_to_passive":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.send_to_passive(args["message"])
            return {"success": True}

        elif name == "broadcast_chat":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.broadcast_chat(args["from_nick"], args["message"])
            return {"success": True}

        elif name == "send_pm_as":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.send_pm_as(args["from_nick"], args["to_nick"], args["message"])
            return {"success": True}

        # --- Phase 5.2: Administration ---
        elif name == "force_move":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.force_move(args["nick"], args["address"])
            return {"success": ok, "nick": args["nick"], "address": args["address"]}

        elif name == "disconnect_user":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.disconnect_user(args["nick"])
            return {"success": ok, "nick": args["nick"]}

        elif name == "add_robot":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.add_robot(args["nick"], args.get("description", ""), args.get("user_class", 3))
            return {"success": ok, "nick": args["nick"]}

        elif name == "remove_robot":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.remove_robot(args["nick"])
            return {"success": ok, "nick": args["nick"]}

        elif name == "set_hub_topic":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.hub_topic = args["topic"]
            return {"success": True, "topic": args["topic"]}

        elif name == "get_hub_config":
            if ctx is None:
                return {"error": "Hub not running"}
            val = ctx.get_config(args["section"], args["key"])
            return {"section": args["section"], "key": args["key"], "value": val}

        elif name == "set_hub_config":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.set_config(args["section"], args["key"], args["value"])
            return {"success": True}

        elif name == "reload_config":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.request_reload()
            return {"success": True}

        # --- Phase 5.3: Statistics & GeoIP ---
        elif name == "get_protocol_stats":
            if ctx is None:
                return {"error": "Hub not running"}
            return ctx.get_protocol_stats()

        elif name == "lookup_geoip":
            if ctx is None:
                return {"error": "Hub not running"}
            return ctx.lookup_geoip(args["ip"])

        elif name == "get_active_passive_counts":
            if ctx is None:
                return {"error": "Hub not running"}
            return {"active": ctx.get_active_user_count(), "passive": ctx.get_passive_user_count()}

        # --- Phase 5.4: Plugin & Script Management ---
        elif name == "list_plugins":
            if ctx is None:
                return {"error": "Hub not running"}
            return ctx.get_loaded_plugins()

        elif name == "load_plugin":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.load_plugin(args["plugin_path"])
            return {"success": ok}

        elif name == "unload_plugin":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.unload_plugin(args["plugin_name"])
            return {"success": ok}

        elif name == "reload_plugin":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.reload_plugin(args["plugin_name"])
            return {"success": ok}

        elif name == "list_lua_scripts":
            if ctx is None:
                return {"error": "Hub not running"}
            return ctx.get_loaded_lua_scripts()

        elif name == "load_lua_script":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.execute_lua_script(args["script_path"])
            return {"success": ok}

        elif name == "unload_lua_script":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.unload_lua_script(args["script_path"])
            return {"success": ok}

        elif name == "list_python_scripts":
            if ctx is None:
                return {"error": "Hub not running"}
            return ctx.get_loaded_python_scripts()

        elif name == "load_python_script":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.execute_python_script(args["script_path"])
            return {"success": ok}

        elif name == "unload_python_script":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ok = ctx.unload_python_script(args["script_path"])
            return {"success": ok}

        # --- Phase 5.5: Flood & Ban Cache ---
        elif name == "set_flood_config":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.set_flood_config(args["flood_type"], args["period_ms"], args["max_tokens"])
            return {"success": True}

        elif name == "sync_ban_cache":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.load_ban_cache()
            return {"success": True}

        elif name == "add_ban_cache_ip":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.add_ban_cache_ip(args["ip"])
            return {"success": True}

        elif name == "add_ban_cache_nick":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.add_ban_cache_nick(args["nick"])
            return {"success": True}

        elif name == "clear_ban_cache":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            if ctx is None:
                return {"error": "Hub not running"}
            ctx.clear_ban_cache()
            return {"success": True}

        # --- Phase 5.6: Penalty Management ---
        elif name == "list_penalties":
            try:
                from verlihub.penalty_service import get_penalty_service
                svc = get_penalty_service()
                penalties = svc.get_active_penalties(nick=args.get("nick"))
                return [p.to_dict() if hasattr(p, "to_dict") else vars(p) for p in penalties]
            except Exception as exc:
                return {"error": f"Penalty list failed: {exc}"}

        elif name == "add_penalty":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            try:
                from verlihub.penalty_service import get_penalty_service
                svc = get_penalty_service()
                svc.add_penalty(
                    nick=args["nick"],
                    penalty_type=args["penalty_type"],
                    reason=args.get("reason", ""),
                    duration_minutes=args.get("duration_minutes", 0),
                )
                return {"success": True}
            except Exception as exc:
                return {"error": f"Add penalty failed: {exc}"}

        elif name == "remove_penalty":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            try:
                from verlihub.penalty_service import get_penalty_service
                svc = get_penalty_service()
                svc.remove_penalty(nick=args["nick"], penalty_type=args.get("penalty_type"))
                return {"success": True}
            except Exception as exc:
                return {"error": f"Remove penalty failed: {exc}"}

        elif name == "cleanup_penalties":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            try:
                from verlihub.penalty_service import get_penalty_service
                svc = get_penalty_service()
                count = svc.cleanup_expired()
                return {"success": True, "removed": count}
            except Exception as exc:
                return {"error": f"Cleanup failed: {exc}"}

        # --- Phase 5.7: Triggers & Redirects ---
        elif name == "list_triggers":
            try:
                from verlihub.trigger_service import get_trigger_cache
                cache = get_trigger_cache()
                return [t.to_dict() if hasattr(t, "to_dict") else vars(t) for t in cache.get_all()]
            except Exception as exc:
                return {"error": f"Trigger list failed: {exc}"}

        elif name == "add_trigger":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            try:
                from verlihub.trigger_service import get_trigger_cache
                cache = get_trigger_cache()
                cache.add(
                    command=args["command"],
                    response=args["response"],
                    min_class=args.get("min_class", 0),
                )
                return {"success": True}
            except Exception as exc:
                return {"error": f"Add trigger failed: {exc}"}

        elif name == "remove_trigger":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            try:
                from verlihub.trigger_service import get_trigger_cache
                cache = get_trigger_cache()
                cache.remove(command=args["command"])
                return {"success": True}
            except Exception as exc:
                return {"error": f"Remove trigger failed: {exc}"}

        elif name == "list_redirects":
            try:
                from verlihub.redirect_service import get_redirect_cache
                cache = get_redirect_cache()
                return [r.to_dict() if hasattr(r, "to_dict") else vars(r) for r in cache.get_all()]
            except Exception as exc:
                return {"error": f"Redirect list failed: {exc}"}

        elif name == "add_redirect":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            try:
                from verlihub.redirect_service import get_redirect_cache
                cache = get_redirect_cache()
                cache.add(
                    address=args["address"],
                    flag=args.get("flag", 0),
                    enabled=args.get("enabled", True),
                )
                return {"success": True}
            except Exception as exc:
                return {"error": f"Add redirect failed: {exc}"}

        elif name == "remove_redirect":
            if not is_adm:
                return {"error": "Permission denied — requires admin"}
            try:
                from verlihub.redirect_service import get_redirect_cache
                cache = get_redirect_cache()
                cache.remove(address=args["address"])
                return {"success": True}
            except Exception as exc:
                return {"error": f"Remove redirect failed: {exc}"}

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
            # --- Phase 5.9: New MCP Prompts ---
            Prompt(name="security_audit",
                   description="Analyze flood stats, ban cache, and active penalties for threats",
                   arguments=[]),
            Prompt(name="plugin_status",
                   description="Report on loaded plugins, scripts, and their health",
                   arguments=[]),
            Prompt(name="traffic_analysis",
                   description="Analyze protocol stats for anomalies (flood, spam patterns)",
                   arguments=[]),
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
        elif name == "security_audit":
            messages = [PromptMessage(role="user", content=TextContent(type="text", text=(
                "Perform a security audit of this Verlihub DC++ hub. "
                "Use get_protocol_stats to check flood/ban blocked counters, "
                "list_penalties to review active restrictions, "
                "search_bans to examine recent bans, and get_hub_statistics for anomalies. "
                "Report:\n"
                "1. Flood protection effectiveness (blocked counts vs total traffic)\n"
                "2. Active penalties and their justification\n"
                "3. Recent ban patterns (targeted attacks, repeat offenders)\n"
                "4. Recommendations for tightening security"
            )))]
        elif name == "plugin_status":
            messages = [PromptMessage(role="user", content=TextContent(type="text", text=(
                "Report on the plugin and script ecosystem of this Verlihub DC++ hub. "
                "Use list_plugins, list_lua_scripts, and list_python_scripts to gather data. "
                "Report:\n"
                "1. Loaded native plugins (names, versions)\n"
                "2. Active Lua scripts\n"
                "3. Active Python scripts\n"
                "4. Any potential issues (missing plugins, version mismatches)"
            )))]
        elif name == "traffic_analysis":
            messages = [PromptMessage(role="user", content=TextContent(type="text", text=(
                "Analyze protocol traffic for this Verlihub DC++ hub. "
                "Use get_protocol_stats and get_active_passive_counts to gather data. "
                "Report:\n"
                "1. Message volume breakdown (chat, PM, search, CTM, SR, MCTo)\n"
                "2. Flood blocked rate vs total messages\n"
                "3. Active vs passive user ratio\n"
                "4. Any anomalies suggesting abuse (high search/CTM ratios, spam patterns)"
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
