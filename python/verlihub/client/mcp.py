"""
Verlihub MCP Server — Model Context Protocol interface for AI assistants.

Exposes Verlihub hub resources and tools to AI coding assistants
(VS Code Copilot, Claude Desktop, Cursor, etc.) via the MCP stdio protocol.

This module uses the ``verlihub.client.api.AsyncHubClient`` to communicate
with the hub's REST API, so it can run in a separate process or on a
different machine from the hub itself.

Usage (stdio mode — for AI editors):
    python -m verlihub.client.mcp \\
        --hub-url http://localhost:4112/api/v1 \\
        --username admin --password secret

Usage (from VS Code .vscode/mcp.json):
    {
        "servers": {
            "verlihub": {
                "type": "stdio",
                "command": "python",
                "args": ["-m", "verlihub.client.mcp",
                         "--hub-url", "http://localhost:4112/api/v1",
                         "--username", "admin", "--password", "secret"]
            }
        }
    }

Environment variables:
    VERLIHUB_HUB_URL   — Hub REST API base URL
    VERLIHUB_USERNAME   — Login username
    VERLIHUB_PASSWORD   — Login password
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

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

        return []

    return server


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server in stdio mode."""
    parser = argparse.ArgumentParser(
        prog="verlihub-mcp",
        description="Verlihub MCP Server — expose hub tools to AI assistants",
    )
    parser.add_argument(
        "--hub-url",
        default=os.environ.get("VERLIHUB_HUB_URL", "http://localhost:4112/api/v1"),
        help="Hub REST API base URL (default: $VERLIHUB_HUB_URL or http://localhost:4112/api/v1)",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("VERLIHUB_USERNAME", ""),
        help="Login username (default: $VERLIHUB_USERNAME)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("VERLIHUB_PASSWORD", ""),
        help="Login password (default: $VERLIHUB_PASSWORD)",
    )
    parser.add_argument(
        "--name",
        default="verlihub",
        help="MCP server name (default: verlihub)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: WARNING)",
    )

    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), stream=sys.stderr)

    if not args.username or not args.password:
        parser.error(
            "Username and password are required. "
            "Use --username/--password or set VERLIHUB_USERNAME/VERLIHUB_PASSWORD."
        )

    _ensure_mcp()
    from mcp.server.stdio import stdio_server

    server = build_mcp_server(
        hub_url=args.hub_url,
        username=args.username,
        password=args.password,
        server_name=args.name,
    )

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
