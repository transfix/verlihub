"""
Verlihub MCP — Model Context Protocol server & client CLI.

The **server** exposes Verlihub hub resources and tools to AI coding
assistants (VS Code Copilot, Claude Desktop, Cursor, etc.) via MCP over
stdio or Streamable HTTP.

The **client** connects to a running MCP server (over HTTP) and lets you
list tools/resources/prompts and call them from the terminal.

Both sides communicate with the hub through the REST API using
``verlihub.client.api.AsyncHubClient``.

Server usage:
    verlihub-mcp serve \\
        --hub-url http://localhost:4112/api/v1 \\
        --username admin --password secret

    verlihub-mcp serve --transport http --port 8080 \\
        --hub-url http://localhost:4112/api/v1 \\
        --username admin --password secret

Client usage:
    verlihub-mcp client --url http://localhost:8080/mcp tools
    verlihub-mcp client --url http://localhost:8080/mcp call get_hub_info
    verlihub-mcp client --url http://localhost:8080/mcp resources
    verlihub-mcp client --url http://localhost:8080/mcp read hub://info

VS Code integration (.vscode/mcp.json — stdio):
    {
        "servers": {
            "verlihub": {
                "type": "stdio",
                "command": "verlihub-mcp",
                "args": ["serve",
                         "--hub-url", "http://localhost:4112/api/v1",
                         "--username", "admin", "--password", "secret"]
            }
        }
    }

VS Code integration (.vscode/mcp.json — HTTP):
    {
        "servers": {
            "verlihub": {
                "type": "http",
                "url": "http://localhost:8080/mcp"
            }
        }
    }

Environment variables:
    VERLIHUB_HUB_URL   — Hub REST API base URL
    VERLIHUB_USERNAME   — Login username
    VERLIHUB_PASSWORD   — Login password
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

import click

logger = logging.getLogger("verlihub.client.mcp")

# ---------------------------------------------------------------------------
# Lazy MCP SDK import
# ---------------------------------------------------------------------------

def _ensure_mcp():
    """Import the MCP SDK or give a helpful error."""
    try:
        import mcp  # noqa: F401
    except ImportError:
        print(
            "ERROR: The 'mcp' package is required for the MCP server.\n"
            "Install it with:  pip install 'mcp[cli]'",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Hub connection helper
# ---------------------------------------------------------------------------

async def _create_hub_client(
    hub_url: str,
    username: str,
    password: str,
):
    """Create and authenticate an AsyncHubClient."""
    from verlihub.client.api import AsyncHubClient

    client = AsyncHubClient(hub_url)
    # Manually create the httpx session
    await client.__aenter__()
    await client.login(username, password)
    return client


# ---------------------------------------------------------------------------
# MCP Server builder
# ---------------------------------------------------------------------------

def build_mcp_server(
    hub_url: str,
    username: str,
    password: str,
    server_name: str = "verlihub",
):
    """
    Build and return an MCP ``Server`` instance wired to the given hub.

    The server exposes:
    - **Resources**: hub://info, hub://users, hub://stats, hub://bans
    - **Tools**: 15 hub management tools (read + write)
    - **Prompts**: hub_report, user_lookup, troubleshoot
    """
    _ensure_mcp()

    from mcp.server import Server
    from mcp.types import (
        Resource,
        Tool,
        TextContent,
        Prompt,
        PromptArgument,
        PromptMessage,
    )

    server = Server(server_name)
    _client: dict[str, Any] = {}  # holds {"hub": AsyncHubClient} once connected

    async def _hub():
        """Get or create the hub client."""
        if "hub" not in _client:
            _client["hub"] = await _create_hub_client(hub_url, username, password)
        return _client["hub"]

    # =================================================================
    # Resources
    # =================================================================
    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri="hub://info",
                name="Hub Information",
                description="Basic hub info — name, topic, user count, share total",
                mimeType="application/json",
            ),
            Resource(
                uri="hub://users",
                name="Online Users",
                description="List of currently connected users",
                mimeType="application/json",
            ),
            Resource(
                uri="hub://stats",
                name="Hub Statistics",
                description="Detailed hub statistics and health metrics",
                mimeType="application/json",
            ),
            Resource(
                uri="hub://bans",
                name="Active Bans",
                description="List of currently active bans",
                mimeType="application/json",
            ),
            Resource(
                uri="hub://plugins",
                name="Loaded Plugins",
                description="List of currently loaded hub plugins",
                mimeType="application/json",
            ),
            Resource(
                uri="hub://penalties",
                name="Active Penalties",
                description="List of currently active penalties",
                mimeType="application/json",
            ),
            Resource(
                uri="hub://protocol_stats",
                name="Protocol Statistics",
                description="NMDC protocol-level statistics",
                mimeType="application/json",
            ),
            Resource(
                uri="hub://triggers",
                name="Trigger Commands",
                description="List of configured trigger commands",
                mimeType="application/json",
            ),
            Resource(
                uri="hub://flood_config",
                name="Flood Protection",
                description="Current flood protection settings",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        hub = await _hub()
        if uri == "hub://info":
            return json.dumps(await hub.get_hub_info(), indent=2, default=str)
        elif uri == "hub://users":
            users = await hub.get_detailed_users()
            return json.dumps(users, indent=2, default=str)
        elif uri == "hub://stats":
            return json.dumps(await hub.get_statistics(), indent=2, default=str)
        elif uri == "hub://bans":
            bans = await hub.get_bans(limit=200)
            return json.dumps(bans, indent=2, default=str)
        elif uri == "hub://plugins":
            return json.dumps(await hub.get_plugins(), indent=2, default=str)
        elif uri == "hub://penalties":
            return json.dumps(await hub.get_penalties(), indent=2, default=str)
        elif uri == "hub://protocol_stats":
            return json.dumps(await hub.get_protocol_stats(), indent=2, default=str)
        elif uri == "hub://triggers":
            return json.dumps(await hub.get_triggers(), indent=2, default=str)
        elif uri == "hub://flood_config":
            return json.dumps(await hub.get_flood_config(), indent=2, default=str)
        else:
            return json.dumps({"error": f"Unknown resource: {uri}"})

    # =================================================================
    # Tools
    # =================================================================
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            # --- Read-only tools ---
            Tool(
                name="get_hub_info",
                description="Get basic hub information (name, topic, user count, share total, version)",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="list_online_users",
                description="List all currently connected users with details (nick, share, connection, class)",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="get_user_info",
                description="Get detailed information about a specific user",
                inputSchema={
                    "type": "object",
                    "properties": {"nick": {"type": "string", "description": "User nickname"}},
                    "required": ["nick"],
                },
            ),
            Tool(
                name="get_hub_statistics",
                description="Get comprehensive hub statistics (uptime, bandwidth, connection counts)",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="get_share_statistics",
                description="Get file-sharing statistics (total share, average per user)",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="get_geo_distribution",
                description="Get geographic distribution of connected users by country",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="list_operators",
                description="List currently online hub operators and admins",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="list_bots",
                description="List hub bots",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="search_bans",
                description="Search active bans, optionally filtered by keyword",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search keyword (nick, IP, reason). Leave empty for all bans.",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                    "required": [],
                },
            ),
            Tool(
                name="get_registered_users",
                description="Get list of registered hub users",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Max results (default 100)"},
                    },
                    "required": [],
                },
            ),
            Tool(
                name="health_check",
                description="Run a hub health check",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            # --- Write/Admin tools ---
            Tool(
                name="kick_user",
                description="Kick a user from the hub",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string", "description": "Nickname to kick"},
                        "reason": {"type": "string", "description": "Reason for the kick"},
                    },
                    "required": ["nick", "reason"],
                },
            ),
            Tool(
                name="send_broadcast",
                description="Send a broadcast message to all connected users",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Message text"},
                    },
                    "required": ["message"],
                },
            ),
            Tool(
                name="send_message_to_user",
                description="Send a private message to a specific user",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string", "description": "Recipient nickname"},
                        "message": {"type": "string", "description": "Message text"},
                    },
                    "required": ["nick", "message"],
                },
            ),
            Tool(
                name="ban_user",
                description="Ban a user from the hub",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string", "description": "Nickname to ban"},
                        "reason": {"type": "string", "description": "Reason for the ban"},
                        "duration_hours": {
                            "type": "integer",
                            "description": "Duration in hours (0 = permanent)",
                        },
                    },
                    "required": ["nick", "reason"],
                },
            ),
            Tool(
                name="register_user",
                description="Register a new user on the hub",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string", "description": "Nickname to register"},
                        "password": {"type": "string", "description": "Password"},
                        "user_class": {
                            "type": "integer",
                            "description": "User class (1=registered, 3=op, 5=admin, 10=master)",
                        },
                    },
                    "required": ["nick", "password"],
                },
            ),
            # --- Phase 5: Messaging ---
            Tool(
                name="send_to_opchat",
                description="Send a message to operator chat",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "from_nick": {"type": "string", "description": "Optional sender nick"},
                    },
                    "required": ["message"],
                },
            ),
            Tool(
                name="send_to_active",
                description="Send a message only to active-mode users",
                inputSchema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            ),
            Tool(
                name="send_to_passive",
                description="Send a message only to passive-mode users",
                inputSchema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            ),
            Tool(
                name="broadcast_chat",
                description="Broadcast a main-chat message as a specific nick",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "from_nick": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["from_nick", "message"],
                },
            ),
            # --- Phase 5: Admin ---
            Tool(
                name="force_move",
                description="Force-move a user to another hub address",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string"},
                        "address": {"type": "string", "description": "Target hub address"},
                    },
                    "required": ["nick", "address"],
                },
            ),
            Tool(
                name="disconnect_user",
                description="Disconnect a user from the hub",
                inputSchema={
                    "type": "object",
                    "properties": {"nick": {"type": "string"}},
                    "required": ["nick"],
                },
            ),
            Tool(
                name="add_robot",
                description="Add a bot/robot nick to the hub",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string"},
                        "description": {"type": "string"},
                        "user_class": {"type": "integer"},
                    },
                    "required": ["nick"],
                },
            ),
            Tool(
                name="remove_robot",
                description="Remove a bot/robot nick from the hub",
                inputSchema={
                    "type": "object",
                    "properties": {"nick": {"type": "string"}},
                    "required": ["nick"],
                },
            ),
            Tool(
                name="reload_config",
                description="Reload hub configuration from disk",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            # --- Phase 5: Statistics ---
            Tool(
                name="get_protocol_stats",
                description="Get NMDC protocol statistics (bytes in/out, messages, etc.)",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="lookup_geoip",
                description="Look up GeoIP information for an IP address",
                inputSchema={
                    "type": "object",
                    "properties": {"ip": {"type": "string"}},
                    "required": ["ip"],
                },
            ),
            Tool(
                name="get_active_passive_counts",
                description="Get counts of active-mode and passive-mode users",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            # --- Phase 5: Plugin Management ---
            Tool(
                name="list_plugins",
                description="List currently loaded hub plugins",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="load_plugin",
                description="Load a plugin by file path",
                inputSchema={
                    "type": "object",
                    "properties": {"plugin_path": {"type": "string"}},
                    "required": ["plugin_path"],
                },
            ),
            Tool(
                name="unload_plugin",
                description="Unload a plugin by name",
                inputSchema={
                    "type": "object",
                    "properties": {"plugin_name": {"type": "string"}},
                    "required": ["plugin_name"],
                },
            ),
            Tool(
                name="reload_plugin",
                description="Reload a plugin by name",
                inputSchema={
                    "type": "object",
                    "properties": {"plugin_name": {"type": "string"}},
                    "required": ["plugin_name"],
                },
            ),
            # --- Phase 5: Script Management ---
            Tool(
                name="list_lua_scripts",
                description="List loaded Lua scripts",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="load_lua_script",
                description="Load a Lua script by path",
                inputSchema={
                    "type": "object",
                    "properties": {"script_path": {"type": "string"}},
                    "required": ["script_path"],
                },
            ),
            Tool(
                name="unload_lua_script",
                description="Unload a Lua script by path",
                inputSchema={
                    "type": "object",
                    "properties": {"script_path": {"type": "string"}},
                    "required": ["script_path"],
                },
            ),
            Tool(
                name="list_python_scripts",
                description="List loaded Python scripts",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="load_python_script",
                description="Load a Python script by path",
                inputSchema={
                    "type": "object",
                    "properties": {"script_path": {"type": "string"}},
                    "required": ["script_path"],
                },
            ),
            Tool(
                name="unload_python_script",
                description="Unload a Python script by path",
                inputSchema={
                    "type": "object",
                    "properties": {"script_path": {"type": "string"}},
                    "required": ["script_path"],
                },
            ),
            # --- Phase 5: Flood & Ban ---
            Tool(
                name="get_flood_config",
                description="Get flood protection settings for all message types",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="set_flood_config",
                description="Set flood protection for a message type",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "flood_type": {"type": "string", "description": "chat, pm, search, myinfo, ctm, extjson"},
                        "period_ms": {"type": "integer"},
                        "max_tokens": {"type": "integer"},
                    },
                    "required": ["flood_type", "period_ms", "max_tokens"],
                },
            ),
            Tool(
                name="sync_ban_cache",
                description="Reload the in-memory ban cache from the database",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="add_ban_cache_ip",
                description="Add an IP to the in-memory ban cache",
                inputSchema={
                    "type": "object",
                    "properties": {"ip": {"type": "string"}},
                    "required": ["ip"],
                },
            ),
            Tool(
                name="add_ban_cache_nick",
                description="Add a nick to the in-memory ban cache",
                inputSchema={
                    "type": "object",
                    "properties": {"nick": {"type": "string"}},
                    "required": ["nick"],
                },
            ),
            Tool(
                name="clear_ban_cache",
                description="Clear the entire in-memory ban cache",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            # --- Phase 5: Penalties ---
            Tool(
                name="list_penalties",
                description="List active penalties, optionally filtered by nick",
                inputSchema={
                    "type": "object",
                    "properties": {"nick": {"type": "string", "description": "Filter by nick (optional)"}},
                    "required": [],
                },
            ),
            Tool(
                name="add_penalty",
                description="Add a penalty for a user",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string"},
                        "penalty_type": {"type": "string"},
                        "reason": {"type": "string"},
                        "duration_minutes": {"type": "integer"},
                    },
                    "required": ["nick", "penalty_type"],
                },
            ),
            Tool(
                name="remove_penalty",
                description="Remove a penalty for a user",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string"},
                        "penalty_type": {"type": "string"},
                    },
                    "required": ["nick"],
                },
            ),
            # --- Phase 5: Triggers & Redirects ---
            Tool(
                name="list_triggers",
                description="List all trigger commands",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="add_trigger",
                description="Add a trigger command",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "response": {"type": "string"},
                        "min_class": {"type": "integer"},
                    },
                    "required": ["command", "response"],
                },
            ),
            Tool(
                name="remove_trigger",
                description="Remove a trigger command by ID",
                inputSchema={
                    "type": "object",
                    "properties": {"trigger_id": {"type": "integer"}},
                    "required": ["trigger_id"],
                },
            ),
            Tool(
                name="list_redirects",
                description="List all redirect addresses",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="add_redirect",
                description="Add a redirect address",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "address": {"type": "string"},
                        "flag": {"type": "integer"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["address"],
                },
            ),
            Tool(
                name="remove_redirect",
                description="Remove a redirect by ID",
                inputSchema={
                    "type": "object",
                    "properties": {"redirect_id": {"type": "integer"}},
                    "required": ["redirect_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        hub = await _hub()
        try:
            result = await _dispatch_tool(hub, name, arguments)
            text = json.dumps(result, indent=2, default=str) if isinstance(result, (dict, list)) else str(result)
            return [TextContent(type="text", text=text)]
        except Exception as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]

    async def _dispatch_tool(hub, name: str, args: dict[str, Any]) -> Any:
        """Route a tool call to the appropriate hub client method."""
        if name == "get_hub_info":
            return await hub.get_hub_info()

        elif name == "list_online_users":
            return await hub.get_detailed_users()

        elif name == "get_user_info":
            users = await hub.get_detailed_users()
            nick = args["nick"].lower()
            for u in users:
                if u.get("nick", "").lower() == nick:
                    return u
            return {"error": f"User '{args['nick']}' not found online"}

        elif name == "get_hub_statistics":
            return await hub.get_statistics()

        elif name == "get_share_statistics":
            return await hub.get_share_stats()

        elif name == "get_geo_distribution":
            return await hub.get_geo_distribution()

        elif name == "list_operators":
            return await hub.get_operators()

        elif name == "list_bots":
            return await hub.get_bots()

        elif name == "search_bans":
            limit = args.get("limit", 50)
            bans = await hub.get_bans(limit=limit)
            query = args.get("query", "").lower()
            if query:
                bans = [
                    b for b in bans
                    if query in json.dumps(b, default=str).lower()
                ]
            return bans

        elif name == "get_registered_users":
            return await hub.get_registered_users(limit=args.get("limit", 100))

        elif name == "health_check":
            return await hub.health_check()

        elif name == "kick_user":
            ok = await hub.kick_user(
                op=hub._user_nick,
                nick=args["nick"],
                reason=args["reason"],
            )
            return {"status": "kicked" if ok else "failed", "nick": args["nick"]}

        elif name == "send_broadcast":
            ok = await hub.send_to_all(args["message"])
            return {"status": "sent" if ok else "failed"}

        elif name == "send_message_to_user":
            ok = await hub.send_to_user(args["nick"], args["message"])
            return {"status": "sent" if ok else "failed"}

        elif name == "ban_user":
            return await hub.ban_user(
                nick=args["nick"],
                reason=args["reason"],
                duration_hours=args.get("duration_hours", 0),
            )

        elif name == "register_user":
            return await hub.register_user(
                nick=args["nick"],
                password=args["password"],
                user_class=args.get("user_class", 1),
            )

        # Phase 5: Messaging
        elif name == "send_to_opchat":
            ok = await hub.send_to_opchat(args["message"], args.get("from_nick", ""))
            return {"status": "sent" if ok else "failed"}

        elif name == "send_to_active":
            ok = await hub.send_to_active(args["message"])
            return {"status": "sent" if ok else "failed"}

        elif name == "send_to_passive":
            ok = await hub.send_to_passive(args["message"])
            return {"status": "sent" if ok else "failed"}

        elif name == "broadcast_chat":
            ok = await hub.broadcast_chat(args["from_nick"], args["message"])
            return {"status": "sent" if ok else "failed"}

        # Phase 5: Admin
        elif name == "force_move":
            ok = await hub.force_move(args["nick"], args["address"])
            return {"success": ok, "nick": args["nick"]}

        elif name == "disconnect_user":
            ok = await hub.disconnect_user(args["nick"])
            return {"success": ok, "nick": args["nick"]}

        elif name == "add_robot":
            ok = await hub.add_robot(args["nick"], args.get("description", ""), args.get("user_class", 3))
            return {"success": ok, "nick": args["nick"]}

        elif name == "remove_robot":
            ok = await hub.remove_robot(args["nick"])
            return {"success": ok, "nick": args["nick"]}

        elif name == "reload_config":
            return await hub.reload_config()

        # Phase 5: Statistics
        elif name == "get_protocol_stats":
            return await hub.get_protocol_stats()

        elif name == "lookup_geoip":
            return await hub.lookup_geoip(args["ip"])

        elif name == "get_active_passive_counts":
            return await hub.get_active_passive_counts()

        # Phase 5: Plugin Management
        elif name == "list_plugins":
            return await hub.get_plugins()

        elif name == "load_plugin":
            ok = await hub.load_plugin(args["plugin_path"])
            return {"success": ok}

        elif name == "unload_plugin":
            ok = await hub.unload_plugin(args["plugin_name"])
            return {"success": ok}

        elif name == "reload_plugin":
            ok = await hub.reload_plugin(args["plugin_name"])
            return {"success": ok}

        # Phase 5: Script Management
        elif name == "list_lua_scripts":
            return await hub.get_lua_scripts()

        elif name == "load_lua_script":
            ok = await hub.load_lua_script(args["script_path"])
            return {"success": ok}

        elif name == "unload_lua_script":
            ok = await hub.unload_lua_script(args["script_path"])
            return {"success": ok}

        elif name == "list_python_scripts":
            return await hub.get_python_scripts()

        elif name == "load_python_script":
            ok = await hub.load_python_script(args["script_path"])
            return {"success": ok}

        elif name == "unload_python_script":
            ok = await hub.unload_python_script(args["script_path"])
            return {"success": ok}

        # Phase 5: Flood & Ban Cache
        elif name == "get_flood_config":
            return await hub.get_flood_config()

        elif name == "set_flood_config":
            ok = await hub.set_flood_config(args["flood_type"], args["period_ms"], args["max_tokens"])
            return {"success": ok}

        elif name == "sync_ban_cache":
            ok = await hub.sync_ban_cache()
            return {"success": ok}

        elif name == "add_ban_cache_ip":
            ok = await hub.add_ban_cache_ip(args["ip"])
            return {"success": ok}

        elif name == "add_ban_cache_nick":
            ok = await hub.add_ban_cache_nick(args["nick"])
            return {"success": ok}

        elif name == "clear_ban_cache":
            ok = await hub.clear_ban_cache()
            return {"success": ok}

        # Phase 5: Penalties
        elif name == "list_penalties":
            return await hub.get_penalties(nick=args.get("nick"))

        elif name == "add_penalty":
            return await hub.add_penalty(
                nick=args["nick"],
                penalty_type=args["penalty_type"],
                reason=args.get("reason", ""),
                duration_minutes=args.get("duration_minutes", 0),
            )

        elif name == "remove_penalty":
            return await hub.remove_penalty(
                nick=args["nick"],
                penalty_type=args.get("penalty_type"),
            )

        # Phase 5: Triggers & Redirects
        elif name == "list_triggers":
            return await hub.get_triggers()

        elif name == "add_trigger":
            return await hub.add_trigger(
                command=args["command"],
                response=args["response"],
                min_class=args.get("min_class", 0),
            )

        elif name == "remove_trigger":
            return await hub.remove_trigger(args["trigger_id"])

        elif name == "list_redirects":
            return await hub.get_redirects()

        elif name == "add_redirect":
            return await hub.add_redirect(
                address=args["address"],
                flag=args.get("flag", 0),
                enabled=args.get("enabled", True),
            )

        elif name == "remove_redirect":
            return await hub.remove_redirect(args["redirect_id"])

        else:
            return {"error": f"Unknown tool: {name}"}

    # =================================================================
    # Prompts
    # =================================================================
    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name="hub_report",
                description="Generate a comprehensive hub status report",
                arguments=[],
            ),
            Prompt(
                name="user_lookup",
                description="Look up everything about a specific user",
                arguments=[
                    PromptArgument(
                        name="nick",
                        description="User nickname to look up",
                        required=True,
                    ),
                ],
            ),
            Prompt(
                name="troubleshoot",
                description="Diagnose potential hub issues",
                arguments=[
                    PromptArgument(
                        name="symptom",
                        description="Describe the issue (slow, drops, errors...)",
                        required=True,
                    ),
                ],
            ),
            Prompt(
                name="security_audit",
                description="Perform a security audit of the hub",
                arguments=[],
            ),
            Prompt(
                name="plugin_status",
                description="Review loaded plugins and scripts",
                arguments=[],
            ),
            Prompt(
                name="traffic_analysis",
                description="Analyze hub traffic and protocol statistics",
                arguments=[],
            ),
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> list[PromptMessage]:
        arguments = arguments or {}

        if name == "hub_report":
            return [
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Generate a comprehensive status report for this Verlihub DC++ hub. "
                            "Use the get_hub_info, get_hub_statistics, list_online_users, list_operators, "
                            "and get_share_statistics tools to gather data. Include:\n"
                            "1. Hub overview (name, topic, uptime)\n"
                            "2. User statistics (online, peak, by class)\n"
                            "3. Share statistics (total, average)\n"
                            "4. Geographic distribution\n"
                            "5. Active operators\n"
                            "6. Any potential issues or anomalies"
                        ),
                    ),
                )
            ]

        elif name == "user_lookup":
            nick = arguments.get("nick", "unknown")
            return [
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Look up everything about the user '{nick}' on this Verlihub DC++ hub. "
                            f"Use get_user_info to check if they're online, search_bans to check "
                            f"ban history, and get_registered_users to check registration. Report "
                            f"their connection details, share size, class, and any bans."
                        ),
                    ),
                )
            ]

        elif name == "troubleshoot":
            symptom = arguments.get("symptom", "general issues")
            return [
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Help troubleshoot this Verlihub DC++ hub. The reported symptom is: "
                            f"'{symptom}'. Use health_check, get_hub_statistics, list_online_users, "
                            f"and search_bans to diagnose. Check for:\n"
                            f"1. Resource issues (high user count, bandwidth)\n"
                            f"2. Connectivity (hub running, health endpoint)\n"
                            f"3. Abuse (ban spikes, repeated kicks)\n"
                            f"4. Configuration problems"
                        ),
                    ),
                )
            ]

        elif name == "security_audit":
            return [
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Perform a security audit of this Verlihub DC++ hub. "
                            "Use get_hub_statistics, list_online_users, search_bans, "
                            "get_flood_config, list_penalties, and list_plugins to gather data. "
                            "Check for:\n"
                            "1. Flood protection adequacy\n"
                            "2. Suspicious user patterns\n"
                            "3. Ban/penalty effectiveness\n"
                            "4. Plugin security posture\n"
                            "5. Recommendations for hardening"
                        ),
                    ),
                )
            ]

        elif name == "plugin_status":
            return [
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Review the plugin and script status of this Verlihub DC++ hub. "
                            "Use list_plugins, list_lua_scripts, and list_python_scripts to "
                            "gather data. Report:\n"
                            "1. All loaded plugins with their status\n"
                            "2. All loaded Lua scripts\n"
                            "3. All loaded Python scripts\n"
                            "4. Any potential conflicts or issues\n"
                            "5. Recommendations for optimization"
                        ),
                    ),
                )
            ]

        elif name == "traffic_analysis":
            return [
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Analyze the traffic and protocol statistics of this Verlihub DC++ hub. "
                            "Use get_protocol_stats, get_active_passive_counts, get_hub_statistics, "
                            "and get_geo_distribution to gather data. Report:\n"
                            "1. Protocol message rates and volumes\n"
                            "2. Active vs passive user ratio\n"
                            "3. Geographic traffic distribution\n"
                            "4. Bandwidth utilization\n"
                            "5. Anomalies or optimization opportunities"
                        ),
                    ),
                )
            ]

        return []

    return server


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

# Shared options for hub credentials used by both ``serve`` and ``client``.
_hub_url_option = click.option(
    "--hub-url", envvar="VERLIHUB_HUB_URL",
    default="http://localhost:4112/api/v1", show_default=True, show_envvar=True,
    help="Hub REST API base URL.",
)
_username_option = click.option(
    "--username", envvar="VERLIHUB_USERNAME", default="",
    show_envvar=True, help="Login username.",
)
_password_option = click.option(
    "--password", envvar="VERLIHUB_PASSWORD", default="",
    show_envvar=True, help="Login password.",
)
_log_level_option = click.option(
    "--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default="WARNING", show_default=True, help="Log level.",
)


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """Verlihub MCP — expose hub tools to AI assistants (server + client)."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── serve ─────────────────────────────────────────────────────────────────

@cli.command()
@_hub_url_option
@_username_option
@_password_option
@click.option("--name", default="verlihub", show_default=True, help="MCP server name.")
@_log_level_option
@click.option(
    "--transport", type=click.Choice(["stdio", "http"], case_sensitive=False),
    default="stdio", show_default=True, help="Transport mode.",
)
@click.option("--host", default="0.0.0.0", show_default=True,
              help="HTTP listen address (only with --transport http).")
@click.option("--port", type=int, default=8080, show_default=True,
              help="HTTP listen port (only with --transport http).")
@click.option("--json-response", is_flag=True, default=False,
              help="Use JSON responses instead of SSE streams (HTTP only).")
def serve(hub_url, username, password, name, log_level, transport, host, port, json_response):
    """Start the MCP server (stdio or HTTP)."""
    logging.basicConfig(level=getattr(logging, log_level.upper()), stream=sys.stderr)

    if not username or not password:
        raise click.UsageError(
            "Username and password are required. "
            "Use --username/--password or set VERLIHUB_USERNAME/VERLIHUB_PASSWORD."
        )

    _ensure_mcp()

    server = build_mcp_server(
        hub_url=hub_url,
        username=username,
        password=password,
        server_name=name,
    )

    if transport.lower() == "http":
        _run_http(server, host=host, port=port, json_response=json_response,
                  log_level=log_level.upper())
    else:
        _run_stdio(server)


# ── client ────────────────────────────────────────────────────────────────

@cli.group()
@click.option("--url", envvar="VERLIHUB_MCP_URL",
              default="http://localhost:8080/mcp", show_default=True, show_envvar=True,
              help="MCP server Streamable HTTP URL.")
@_log_level_option
@click.pass_context
def client(ctx, url, log_level):
    """Connect to a running MCP server over HTTP."""
    logging.basicConfig(level=getattr(logging, log_level.upper()), stream=sys.stderr)
    ctx.ensure_object(dict)
    ctx.obj["url"] = url


@client.command("tools")
@click.pass_context
def client_tools(ctx):
    """List available tools on the MCP server."""
    _ensure_mcp()
    asyncio.run(_client_list_tools(ctx.obj["url"]))


@client.command("resources")
@click.pass_context
def client_resources(ctx):
    """List available resources on the MCP server."""
    _ensure_mcp()
    asyncio.run(_client_list_resources(ctx.obj["url"]))


@client.command("prompts")
@click.pass_context
def client_prompts(ctx):
    """List available prompts on the MCP server."""
    _ensure_mcp()
    asyncio.run(_client_list_prompts(ctx.obj["url"]))


@client.command("call")
@click.argument("tool_name")
@click.argument("args_json", default="{}")
@click.pass_context
def client_call(ctx, tool_name, args_json):
    """Call a tool on the MCP server.

    TOOL_NAME is the tool to invoke.  ARGS_JSON is an optional JSON object
    with the tool's input arguments (default: "{}").

    \b
    Examples:
        verlihub-mcp client call get_hub_info
        verlihub-mcp client call get_user_info '{"nick":"admin"}'
        verlihub-mcp client call kick_user '{"nick":"spam","reason":"flooding"}'
    """
    _ensure_mcp()
    try:
        arguments = json.loads(args_json)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"Invalid JSON: {exc}") from exc
    asyncio.run(_client_call_tool(ctx.obj["url"], tool_name, arguments))


@client.command("read")
@click.argument("uri")
@click.pass_context
def client_read(ctx, uri):
    """Read a resource from the MCP server.

    \b
    Examples:
        verlihub-mcp client read hub://info
        verlihub-mcp client read hub://users
    """
    _ensure_mcp()
    asyncio.run(_client_read_resource(ctx.obj["url"], uri))


@client.command("prompt")
@click.argument("prompt_name")
@click.argument("args_json", default="{}")
@click.pass_context
def client_prompt(ctx, prompt_name, args_json):
    """Get a prompt from the MCP server.

    \b
    Examples:
        verlihub-mcp client prompt hub_report
        verlihub-mcp client prompt user_lookup '{"nick":"admin"}'
    """
    _ensure_mcp()
    try:
        arguments = json.loads(args_json)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"Invalid JSON: {exc}") from exc
    asyncio.run(_client_get_prompt(ctx.obj["url"], prompt_name, arguments))


# ── client async helpers ──────────────────────────────────────────────────

async def _connect_client(url: str):
    """Open an MCP client session over Streamable HTTP and return (session, cm).

    Caller is responsible for closing the context managers.
    """
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    return streamablehttp_client(url=url), ClientSession


async def _client_list_tools(url: str):
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    async with streamablehttp_client(url=url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            for tool in result.tools:
                props = tool.inputSchema.get("properties", {})
                req = tool.inputSchema.get("required", [])
                params = ", ".join(
                    f"{k}{'*' if k in req else ''}" for k in props
                ) or "(none)"
                click.echo(f"  {click.style(tool.name, fg='cyan', bold=True)}")
                click.echo(f"    {tool.description}")
                click.echo(f"    params: {params}")


async def _client_list_resources(url: str):
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    async with streamablehttp_client(url=url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_resources()
            for res in result.resources:
                click.echo(
                    f"  {click.style(str(res.uri), fg='green', bold=True)}  "
                    f"{res.description or res.name}"
                )


async def _client_list_prompts(url: str):
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    async with streamablehttp_client(url=url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_prompts()
            for p in result.prompts:
                args = ", ".join(
                    f"{a.name}{'*' if a.required else ''}" for a in (p.arguments or [])
                ) or "(none)"
                click.echo(f"  {click.style(p.name, fg='yellow', bold=True)}")
                click.echo(f"    {p.description}")
                click.echo(f"    args: {args}")


async def _client_call_tool(url: str, tool_name: str, arguments: dict):
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    async with streamablehttp_client(url=url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            for content in result.content:
                text = getattr(content, "text", str(content))
                # Pretty-print JSON if possible
                try:
                    obj = json.loads(text)
                    click.echo(json.dumps(obj, indent=2))
                except (json.JSONDecodeError, TypeError):
                    click.echo(text)


async def _client_read_resource(url: str, uri: str):
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    async with streamablehttp_client(url=url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.read_resource(uri)
            for content in result.contents:
                text = getattr(content, "text", str(content))
                try:
                    obj = json.loads(text)
                    click.echo(json.dumps(obj, indent=2))
                except (json.JSONDecodeError, TypeError):
                    click.echo(text)


async def _client_get_prompt(url: str, prompt_name: str, arguments: dict):
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.session import ClientSession

    async with streamablehttp_client(url=url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.get_prompt(prompt_name, arguments)
            for msg in result.messages:
                role = click.style(msg.role, fg="magenta", bold=True)
                text = getattr(msg.content, "text", str(msg.content))
                click.echo(f"[{role}]\n{text}\n")


# ---------------------------------------------------------------------------
# Transport runners (used by ``serve``)
# ---------------------------------------------------------------------------

def _run_stdio(server):
    """Run the MCP server over stdio (for AI editors)."""
    from mcp.server.stdio import stdio_server

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


def _run_http(server, *, host: str, port: int, json_response: bool, log_level: str):
    """Run the MCP server over Streamable HTTP (for remote/web clients)."""
    import contextlib

    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=json_response,
        stateless=True,
    )

    @contextlib.asynccontextmanager
    async def _lifespan(app):
        async with session_manager.run():
            logger.info("MCP HTTP server ready at http://%s:%d/mcp", host, port)
            yield

    starlette_app = Starlette(
        debug=(log_level == "DEBUG"),
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=_lifespan,
    )

    uvicorn.run(
        starlette_app,
        host=host,
        port=port,
        log_level=log_level.lower(),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Click CLI entry point — registered as ``verlihub-mcp`` console script."""
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
