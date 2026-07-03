#!/usr/bin/env python3
"""
Verlihub MCP (Model Context Protocol) Server

Exposes a live Verlihub DC++ hub to LLMs via the Model Context Protocol.
This server connects to the hub's REST API (hub_api.py) and presents hub
state and operations as MCP resources, tools, and prompts.

Architecture:
─────────────────────────────────────────────────────────────────────────
  LLM (Claude, etc.)
    │  stdio / SSE transport
    ▼
  verlihub_mcp_server.py  (this file — standalone process)
    │  HTTP REST calls
    ▼
  hub_api.py  (FastAPI running inside Verlihub Python plugin)
    │  vh.* C bindings (main thread only)
    ▼
  Verlihub C++ hub core
─────────────────────────────────────────────────────────────────────────

This is a STANDALONE process, not a Verlihub plugin script. It runs
outside the hub and communicates via the REST API that hub_api.py exposes.

Requirements:
  pip install mcp httpx

Usage:
  # Start as stdio MCP server (for Claude Desktop, VS Code, etc.):
  python verlihub_mcp_server.py

  # Or with custom hub API URL:
  VERLIHUB_API_URL=http://hub-host:8000 python verlihub_mcp_server.py

Claude Desktop config (~/.config/claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "verlihub": {
        "command": "python",
        "args": ["/path/to/verlihub_mcp_server.py"],
        "env": {
          "VERLIHUB_API_URL": "http://localhost:8000"
        }
      }
    }
  }

VS Code MCP config (.vscode/mcp.json):
  {
    "servers": {
      "verlihub": {
        "type": "stdio",
        "command": "python",
        "args": ["plugins/python/scripts/verlihub_mcp_server.py"],
        "env": {
          "VERLIHUB_API_URL": "http://localhost:8000"
        }
      }
    }
  }

Author: Verlihub Team
Version: 0.1.0
"""

import os
import json
import logging
from datetime import datetime
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HUB_API_URL = os.environ.get("VERLIHUB_API_URL", "http://localhost:8000")
HUB_API_TIMEOUT = float(os.environ.get("VERLIHUB_API_TIMEOUT", "10"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("verlihub-mcp")

mcp = FastMCP(
    "Verlihub Hub Server",
    version="0.1.0",
    description=(
        "MCP server for managing and monitoring a Verlihub DC++ hub. "
        "Provides live user lists, hub statistics, geographic data, "
        "share analytics, and administrative operations."
    ),
)

# ---------------------------------------------------------------------------
# HTTP helper — all hub interaction goes through the REST API
# ---------------------------------------------------------------------------

async def _hub_get(path: str, params: dict | None = None) -> dict | list | str:
    """GET request to the hub REST API. Returns parsed JSON or raises."""
    url = f"{HUB_API_URL}{path}"
    async with httpx.AsyncClient(timeout=HUB_API_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


def _format_share(bytes_str: str | int) -> str:
    """Human-readable share size."""
    try:
        b = int(bytes_str)
    except (ValueError, TypeError):
        return str(bytes_str)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} EiB"


# ═══════════════════════════════════════════════════════════════════════════
# MCP RESOURCES — read-only contextual data the LLM can subscribe to
# ═══════════════════════════════════════════════════════════════════════════


@mcp.resource("verlihub://hub/info")
async def hub_info() -> str:
    """
    Live hub metadata: name, description, host, topic, version,
    current user count, and total share size.
    """
    data = await _hub_get("/hub")
    lines = [
        f"Hub Name:    {data.get('name', 'N/A')}",
        f"Description: {data.get('description', 'N/A')}",
        f"Host:        {data.get('host', 'N/A')}",
        f"Topic:       {data.get('topic', 'N/A')}",
        f"Version:     {data.get('version', 'N/A')}",
        f"Users:       {data.get('users_online', 'N/A')}",
        f"Total Share: {_format_share(data.get('total_share', 0))}",
    ]
    return "\n".join(lines)


@mcp.resource("verlihub://hub/users")
async def hub_users_resource() -> str:
    """Current user list with nicknames, IPs, countries, share, and class."""
    data = await _hub_get("/users")
    if not data:
        return "No users connected."
    lines = []
    for u in data:
        nick = u.get("nick", "?")
        ip = u.get("ip", "?")
        cc = u.get("country_code", "??")
        share = _format_share(u.get("share_size", 0))
        cls = u.get("class", 0)
        lines.append(f"  {nick:20s}  IP={ip:15s}  CC={cc}  Share={share:>10s}  Class={cls}")
    header = f"Connected users ({len(data)}):\n"
    return header + "\n".join(lines)


@mcp.resource("verlihub://hub/stats")
async def hub_stats_resource() -> str:
    """Full hub statistics snapshot."""
    data = await _hub_get("/stats")
    return json.dumps(data, indent=2, default=str)


@mcp.resource("verlihub://hub/health")
async def hub_health_resource() -> str:
    """Hub health check — is the hub reachable and responsive?"""
    try:
        data = await _hub_get("/health")
        return f"Status: {data.get('status', 'unknown')} — Hub API is reachable."
    except Exception as e:
        return f"Status: UNREACHABLE — {e}"


# ═══════════════════════════════════════════════════════════════════════════
# MCP TOOLS — actions the LLM can invoke to query or modify the hub
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_hub_info() -> dict:
    """
    Get current hub information including name, description, host,
    topic, version, users online count, and total shared size.
    """
    return await _hub_get("/hub")


@mcp.tool()
async def list_users() -> list[dict]:
    """
    List all currently connected users with their nickname, IP address,
    country code, share size, user class, description, tag, speed, and email.
    """
    return await _hub_get("/users")


@mcp.tool()
async def get_user_info(nick: str) -> dict:
    """
    Get detailed information about a specific connected user by nickname.
    Returns IP, country, share size, class, client tag, and more.
    """
    return await _hub_get(f"/user/{nick}")


@mcp.tool()
async def list_operators() -> list[dict]:
    """List all connected operators (moderators/admins)."""
    return await _hub_get("/ops")


@mcp.tool()
async def list_bots() -> list[dict]:
    """List all registered bots on the hub."""
    return await _hub_get("/bots")


@mcp.tool()
async def get_geographic_distribution() -> dict:
    """
    Get geographic distribution of connected users — how many users
    per country, useful for understanding the hub's user base.
    """
    return await _hub_get("/geo")


@mcp.tool()
async def get_share_statistics() -> dict:
    """
    Get file sharing statistics: total share, average per user,
    top sharers, share distribution by size brackets.
    """
    return await _hub_get("/share")


@mcp.tool()
async def get_full_statistics() -> dict:
    """
    Get comprehensive hub statistics including uptime, bandwidth,
    user counts by class, connection stats, and more.
    """
    return await _hub_get("/stats")


@mcp.tool()
async def check_hub_health() -> dict:
    """
    Health check — verify the hub is running and responsive.
    Returns status, uptime, and basic connectivity info.
    """
    return await _hub_get("/health")


@mcp.tool()
async def ping_user(ip: str) -> dict:
    """
    Run an ICMP ping to a user's IP address to check network quality.
    Returns latency, packet loss, jitter metrics.
    """
    return await _hub_get(f"/ping/{ip}")


@mcp.tool()
async def traceroute_user(ip: str) -> dict:
    """
    Run a traceroute to a user's IP address.
    Returns the network path with hop-by-hop latency.
    """
    return await _hub_get(f"/traceroute/{ip}")


@mcp.tool()
async def detect_user_os(ip: str) -> dict:
    """
    Detect the operating system of a connected user by IP address
    using TCP/IP fingerprinting.
    """
    return await _hub_get(f"/os/{ip}")


# ═══════════════════════════════════════════════════════════════════════════
# MCP PROMPTS — reusable prompt templates for common hub analysis tasks
# ═══════════════════════════════════════════════════════════════════════════


@mcp.prompt()
async def hub_status_report() -> str:
    """Generate a comprehensive status report for the hub."""
    return (
        "You are a Verlihub DC++ hub administrator assistant. "
        "Please generate a status report by:\n"
        "1. Use get_hub_info to get basic hub metadata\n"
        "2. Use get_full_statistics for detailed stats\n"
        "3. Use list_users to see who is online\n"
        "4. Use get_geographic_distribution for user geography\n"
        "5. Use get_share_statistics for sharing health\n\n"
        "Produce a well-formatted report covering: hub identity, "
        "current load, user demographics, share health, and any "
        "potential issues you notice (e.g., low share ratios, "
        "concentration from a single country, etc.)."
    )


@mcp.prompt()
async def investigate_user(nick: str) -> str:
    """Investigate a specific user — gather all available data."""
    return (
        f"Investigate the hub user '{nick}'. Steps:\n"
        f"1. Use get_user_info('{nick}') for their profile\n"
        f"2. If you have their IP, use ping_user(ip) to check connectivity\n"
        f"3. Use traceroute_user(ip) to see their network path\n"
        f"4. Use detect_user_os(ip) for OS fingerprinting\n\n"
        f"Summarize: who is this user, where are they connecting from, "
        f"what client are they using, what's their network quality, "
        f"and is there anything unusual about their connection?"
    )


@mcp.prompt()
async def network_diagnostics() -> str:
    """Run network diagnostics across all connected users."""
    return (
        "Run network diagnostics for the hub:\n"
        "1. Use list_users to get all connected users\n"
        "2. For each user (or a sample if there are many), "
        "   use ping_user to check latency\n"
        "3. Identify users with poor connectivity\n"
        "4. Use get_geographic_distribution to correlate with geography\n\n"
        "Report: overall network health, any users with connectivity "
        "issues, geographic patterns affecting latency."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info(f"Starting Verlihub MCP server, hub API at {HUB_API_URL}")
    mcp.run(transport="stdio")
