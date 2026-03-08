"""
LLM Chat Gateway — AI assistant for the Verlihub dashboard.

Provides REST and WebSocket endpoints for conversational interaction
with the hub via a self-hosted LLM (Ollama, vLLM, llama.cpp, etc.).

The gateway:
1. Receives user messages
2. Sends them to the LLM with hub tool definitions
3. Executes tool calls against the live hub context
4. Returns the LLM's natural-language response

All tool calls go through the same hub context and permission checks
as the REST API — no bypass, no escalation.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import openai
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel

from verlihub.api.auth import (
    Permission,
    TokenData,
    decode_token,
    get_current_user,
    require_permission,
)
from verlihub.api.deps import get_hub_context
from verlihub.config import LlmConfig, get_config_optional

log = logging.getLogger("verlihub.llm")

router = APIRouter()


# =============================================================================
# Response models
# =============================================================================


class LlmStatusResponse(BaseModel):
    """LLM integration status."""
    enabled: bool
    llm_reachable: bool
    model: str
    base_url: str
    min_class: int
    admin_class: int


class ChatRequest(BaseModel):
    """Single-turn chat request."""
    message: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response."""
    response: str
    tool_calls: list[dict[str, Any]] = []
    model: str = ""


class SessionInfo(BaseModel):
    """Summary of a chat session."""
    session_id: str
    title: str
    created_at: float
    message_count: int


class SessionListResponse(BaseModel):
    """List of user sessions."""
    sessions: list[SessionInfo] = []


# =============================================================================
# LLM client — lazy singleton
# =============================================================================

_openai_client = None


def _get_llm_config() -> LlmConfig:
    """Get LLM config from the global config singleton."""
    cfg = get_config_optional()
    if cfg is None:
        return LlmConfig()
    return cfg.llm


def _get_openai_client():
    """Get or create the OpenAI-compatible async client."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="LLM integration requires the 'openai' package. Install with: pip install openai",
        )
    
    llm_cfg = _get_llm_config()
    _openai_client = AsyncOpenAI(
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key,
        default_headers={"User-Agent": "verlihub/1.0"},
    )
    return _openai_client


def reset_openai_client():
    """Reset the client (called when config changes)."""
    global _openai_client
    _openai_client = None


# =============================================================================
# Hub tools — definitions for the LLM
# =============================================================================


def _build_readonly_tools() -> list[dict]:
    """Tools available to any permitted user (read-only hub queries)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_hub_info",
                "description": "Get hub info: name, description, topic, version, user count, total share, uptime.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_online_users",
                "description": "List all currently connected users with nick, IP, country, share size, user class, client tag, and description.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_user_info",
                "description": "Get detailed information about a specific connected user by their nickname.",
                "parameters": {
                    "type": "object",
                    "properties": {"nick": {"type": "string", "description": "User nickname"}},
                    "required": ["nick"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_operators",
                "description": "List all connected operators (class 3+).",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_bots",
                "description": "List all hub bots.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_geo_distribution",
                "description": "Get user geographic distribution — count of users per country with share totals.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_share_statistics",
                "description": "Get file sharing statistics: total share, average, top sharers, distribution.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_hub_statistics",
                "description": "Get full hub statistics: uptime, user counts by class, bandwidth, etc.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_bans",
                "description": "Search bans by nick or IP address.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string", "description": "Nick to search (optional)"},
                        "ip": {"type": "string", "description": "IP to search (optional)"},
                    },
                    "required": [],
                },
            },
        },
    ]


def _build_admin_tools() -> list[dict]:
    """Additional tools for admin-level users (write operations)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "kick_user",
                "description": "Kick a user from the hub. Requires operator nick, target nick, and reason.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string", "description": "Nick of user to kick"},
                        "reason": {"type": "string", "description": "Reason for kick"},
                    },
                    "required": ["nick", "reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_broadcast",
                "description": "Send a message to all connected users.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Message to broadcast"},
                    },
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_message_to_user",
                "description": "Send a private message to a specific user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nick": {"type": "string", "description": "Target user nickname"},
                        "message": {"type": "string", "description": "Message text"},
                    },
                    "required": ["nick", "message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_hub_command",
                "description": "Execute a hub console command (e.g. !help, !reglist, !set config value). Returns the command output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Hub command to execute (with ! or + prefix)"},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_hub_config",
                "description": "Read a hub configuration value.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string", "description": "Config section (e.g. 'config')"},
                        "key": {"type": "string", "description": "Config key name"},
                    },
                    "required": ["section", "key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_hub_config",
                "description": "Set a hub configuration value. Use with caution.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string", "description": "Config section"},
                        "key": {"type": "string", "description": "Config key"},
                        "value": {"type": "string", "description": "New value"},
                    },
                    "required": ["section", "key", "value"],
                },
            },
        },
    ]


# =============================================================================
# Tool execution — calls the hub context directly
# =============================================================================


async def _execute_tool(
    tool_name: str,
    arguments: dict,
    user: TokenData,
    is_admin: bool,
) -> str:
    """Execute a tool call against the live hub. Returns JSON string."""
    ctx = get_hub_context()
    llm_cfg = _get_llm_config()
    
    try:
        # ------------------------------------------------------------------
        # Read-only tools
        # ------------------------------------------------------------------
        if tool_name == "get_hub_info":
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            from verlihub.config import get_config_optional as _gc
            cfg = _gc()
            return json.dumps({
                "name": ctx.hub_name,
                "topic": ctx.hub_topic,
                "description": cfg.hub.description if cfg else "",
                "host": cfg.hub.host if cfg else "",
                "version": "1.7.0.0",
                "users_online": ctx.user_count,
                "total_share_bytes": ctx.total_share,
                "total_share_formatted": _format_bytes(ctx.total_share),
                "uptime_seconds": ctx.uptime,
                "is_running": ctx.is_running,
            })
        
        elif tool_name == "list_online_users":
            if ctx is None:
                return json.dumps([])
            users = ctx.get_user_list() or []
            # Strip IPs for non-admin users
            result = []
            for u in users:
                entry = {
                    "nick": u.get("nick", ""),
                    "country_code": u.get("country_code", ""),
                    "share_bytes": u.get("share", 0),
                    "share_formatted": _format_bytes(u.get("share", 0)),
                    "user_class": u.get("user_class", 0),
                    "client": u.get("client", ""),
                    "description": u.get("description", ""),
                }
                if is_admin:
                    entry["ip"] = u.get("ip", "")
                    entry["hostname"] = u.get("hostname", "")
                result.append(entry)
            return json.dumps(result, default=str)
        
        elif tool_name == "get_user_info":
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            nick = arguments.get("nick", "")
            info = ctx.get_user_info(nick)
            if info is None:
                return json.dumps({"error": f"User '{nick}' not found or not online"})
            result = dict(info)
            if not is_admin:
                result.pop("ip", None)
                result.pop("hostname", None)
            return json.dumps(result, default=str)
        
        elif tool_name == "list_operators":
            if ctx is None:
                return json.dumps([])
            users = ctx.get_user_list() or []
            ops = [u for u in users if u.get("user_class", 0) >= 3]
            return json.dumps([{"nick": u.get("nick"), "class": u.get("user_class")} for u in ops])
        
        elif tool_name == "list_bots":
            if ctx is None:
                return json.dumps([])
            try:
                bots = ctx.get_bot_list() if hasattr(ctx, "get_bot_list") else []
            except Exception:
                bots = []
            return json.dumps(bots, default=str)
        
        elif tool_name == "get_geo_distribution":
            if ctx is None:
                return json.dumps({})
            users = ctx.get_user_list() or []
            geo: dict[str, int] = {}
            for u in users:
                cc = u.get("country_code", "??")
                geo[cc] = geo.get(cc, 0) + 1
            # Sort by count descending
            sorted_geo = sorted(geo.items(), key=lambda x: x[1], reverse=True)
            return json.dumps([{"country": cc, "users": cnt} for cc, cnt in sorted_geo])
        
        elif tool_name == "get_share_statistics":
            if ctx is None:
                return json.dumps({})
            users = ctx.get_user_list() or []
            shares = [u.get("share", 0) for u in users]
            total = sum(shares)
            avg = total // len(shares) if shares else 0
            top = sorted(
                [{"nick": u.get("nick"), "share": _format_bytes(u.get("share", 0))} for u in users],
                key=lambda x: next((u.get("share", 0) for u in users if u.get("nick") == x["nick"]), 0),
                reverse=True,
            )[:10]
            return json.dumps({
                "total_share": _format_bytes(total),
                "total_share_bytes": total,
                "average_share": _format_bytes(avg),
                "user_count": len(users),
                "top_sharers": top,
            })
        
        elif tool_name == "get_hub_statistics":
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            users = ctx.get_user_list() or []
            classes = {}
            for u in users:
                c = u.get("user_class", 0)
                classes[c] = classes.get(c, 0) + 1
            return json.dumps({
                "users_online": ctx.user_count,
                "total_share": _format_bytes(ctx.total_share),
                "uptime_seconds": ctx.uptime,
                "is_running": ctx.is_running,
                "users_by_class": classes,
            })
        
        elif tool_name == "search_bans":
            # Use database query
            nick = arguments.get("nick", "")
            ip = arguments.get("ip", "")
            if not nick and not ip:
                return json.dumps({"error": "Provide nick or ip to search"})
            try:
                from verlihub.models.database import get_async_session
                from verlihub.models import Ban
                from sqlmodel import select
                
                async with get_async_session() as session:
                    stmt = select(Ban)
                    if nick:
                        stmt = stmt.where(Ban.nick.contains(nick))
                    if ip:
                        stmt = stmt.where(Ban.ip.contains(ip))
                    result = await session.execute(stmt.limit(20))
                    bans = result.scalars().all()
                    return json.dumps([
                        {"id": b.id, "nick": b.nick, "ip": b.ip, "reason": b.reason,
                         "type": b.ban_type, "expires": str(b.date_limit) if b.date_limit else None}
                        for b in bans
                    ], default=str)
            except Exception as e:
                return json.dumps({"error": f"Ban search failed: {e}"})
        
        # ------------------------------------------------------------------
        # Admin-only tools
        # ------------------------------------------------------------------
        elif tool_name == "kick_user":
            if not is_admin:
                return json.dumps({"error": "Permission denied — requires admin"})
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            nick = arguments.get("nick", "")
            reason = arguments.get("reason", "Kicked by AI assistant")
            try:
                ctx.kick_user(user.nick, nick, reason)
                return json.dumps({"success": True, "kicked": nick, "reason": reason})
            except Exception as e:
                return json.dumps({"error": f"Kick failed: {e}"})
        
        elif tool_name == "send_broadcast":
            if not is_admin:
                return json.dumps({"error": "Permission denied — requires admin"})
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            message = arguments.get("message", "")
            try:
                ctx.send_to_all(message)
                return json.dumps({"success": True, "message": message})
            except Exception as e:
                return json.dumps({"error": f"Broadcast failed: {e}"})
        
        elif tool_name == "send_message_to_user":
            if not is_admin:
                return json.dumps({"error": "Permission denied — requires admin"})
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            nick = arguments.get("nick", "")
            message = arguments.get("message", "")
            try:
                ctx.send_to_user(nick, message)
                return json.dumps({"success": True, "to": nick})
            except Exception as e:
                return json.dumps({"error": f"Send failed: {e}"})
        
        elif tool_name == "execute_hub_command":
            if not is_admin:
                return json.dumps({"error": "Permission denied — requires admin"})
            command = arguments.get("command", "")
            # Safety: use console execution via the hub context
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            try:
                output = ctx.execute_command(user.nick, command) if hasattr(ctx, "execute_command") else f"Command dispatched: {command}"
                return json.dumps({"success": True, "command": command, "output": str(output)})
            except Exception as e:
                return json.dumps({"error": f"Command failed: {e}"})
        
        elif tool_name == "get_hub_config":
            if not is_admin:
                return json.dumps({"error": "Permission denied — requires admin"})
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            section = arguments.get("section", "config")
            key = arguments.get("key", "")
            try:
                value = ctx.get_config(section, key)
                return json.dumps({"section": section, "key": key, "value": value})
            except Exception as e:
                return json.dumps({"error": f"Config read failed: {e}"})
        
        elif tool_name == "set_hub_config":
            if not is_admin:
                return json.dumps({"error": "Permission denied — requires admin"})
            if user.user_class < 10:  # Master only
                return json.dumps({"error": "Permission denied — requires master (class 10)"})
            if ctx is None:
                return json.dumps({"error": "Hub not running"})
            section = arguments.get("section", "config")
            key = arguments.get("key", "")
            value = arguments.get("value", "")
            try:
                ctx.set_config(section, key, value)
                return json.dumps({"success": True, "section": section, "key": key, "value": value})
            except Exception as e:
                return json.dumps({"error": f"Config write failed: {e}"})
        
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
    
    except Exception as e:
        log.exception(f"Tool execution error: {tool_name}")
        return json.dumps({"error": str(e)})


def _format_bytes(b: int) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} EiB"


def _build_hub_context_snapshot(is_admin: bool) -> str:
    """
    Build a text snapshot of live hub state for injection into the system prompt.

    Used when the LLM endpoint doesn't support tool calling — we pre-fetch
    all the data the tools would return and embed it directly.
    """
    ctx = get_hub_context()
    if ctx is None:
        return "\n[Hub is not running — no live data available.]\n"

    parts: list[str] = ["\n--- LIVE HUB DATA (refreshed per message) ---\n"]

    # Hub info
    try:
        from verlihub.config import get_config_optional as _gc
        cfg = _gc()
        parts.append(f"Hub name: {ctx.hub_name}")
        parts.append(f"Topic: {ctx.hub_topic}")
        if cfg:
            parts.append(f"Host: {cfg.hub.host}")
        parts.append(f"Running: {ctx.is_running}")
        parts.append(f"Uptime: {ctx.uptime} seconds")
        parts.append(f"Users online: {ctx.user_count}")
        parts.append(f"Total share: {_format_bytes(ctx.total_share)}")
    except Exception as exc:
        parts.append(f"(Hub info error: {exc})")

    # User list
    try:
        users = ctx.get_user_list() or []
        parts.append(f"\n### Online Users ({len(users)} total)")
        if users:
            for u in users:
                nick = u.get("nick", "?")
                share = _format_bytes(u.get("share", 0))
                uclass = u.get("user_class", 0)
                client = u.get("client", "")
                country = u.get("country", "") or u.get("country_code", "")
                desc = u.get("description", "")
                line = f"  - {nick} | class {uclass} | share {share} | client {client}"
                if country:
                    line += f" | country {country}"
                if desc:
                    line += f" | desc: {desc}"
                if is_admin:
                    ip = u.get("ip", "")
                    if ip:
                        line += f" | IP {ip}"
                parts.append(line)
        else:
            parts.append("  (no users online)")
    except Exception as exc:
        parts.append(f"(User list error: {exc})")

    # Operators
    try:
        users = ctx.get_user_list() or []
        ops = [u for u in users if u.get("user_class", 0) >= 3]
        if ops:
            parts.append(f"\n### Operators ({len(ops)})")
            for o in ops:
                parts.append(f"  - {o.get('nick', '?')} (class {o.get('user_class', 0)})")
    except Exception:
        pass

    # Share statistics
    try:
        users = ctx.get_user_list() or []
        shares = [u.get("share", 0) for u in users]
        if shares:
            total = sum(shares)
            avg = total // len(shares)
            parts.append(f"\n### Share Statistics")
            parts.append(f"  Total: {_format_bytes(total)}")
            parts.append(f"  Average: {_format_bytes(avg)}")
            top = sorted(users, key=lambda u: u.get("share", 0), reverse=True)[:5]
            if top:
                parts.append("  Top sharers:")
                for t in top:
                    parts.append(f"    - {t.get('nick', '?')}: {_format_bytes(t.get('share', 0))}")
    except Exception:
        pass

    # Geo distribution
    try:
        users = ctx.get_user_list() or []
        geo: dict[str, int] = {}
        for u in users:
            cc = u.get("country", "") or u.get("country_code", "??")
            if cc:
                geo[cc] = geo.get(cc, 0) + 1
        if geo:
            parts.append(f"\n### Geographic Distribution")
            for cc, cnt in sorted(geo.items(), key=lambda x: x[1], reverse=True):
                parts.append(f"  - {cc}: {cnt} user(s)")
    except Exception:
        pass

    parts.append("\n--- END LIVE HUB DATA ---\n")
    return "\n".join(parts)


# =============================================================================
# System prompts
# =============================================================================


SYSTEM_PROMPT_ADMIN = """\
You are a Verlihub DC++ hub assistant with administrator access. You help hub \
operators monitor and manage the hub through natural language.

You have access to tools that query and control the live hub. Use them to answer \
questions accurately — never guess hub state, always check via tools.

Available capabilities:
- Query online users, operators, bots
- View hub statistics, geographic distribution, share statistics
- Look up individual user details (including IP addresses)
- Kick users, send broadcasts, send private messages
- Execute hub console commands (!help for list)
- Read and write hub configuration

Guidelines:
- Always call tools rather than assuming hub state
- Present user lists as clean tables when there are few users
- Summarize when there are many users (20+)
- Flag anything unusual: zero-share users, suspicious clients, connectivity issues
- Be direct and professional — this is an ops tool
- Format numbers readably (e.g. "1.23 TiB" not raw bytes)
- When asked to kick or ban, confirm the action and provide the result
"""

SYSTEM_PROMPT_USER = """\
You are a Verlihub DC++ hub assistant. You help users learn about the hub \
and see who's online.

You have access to read-only tools that show public hub information. Use them \
to answer questions accurately.

You can see: hub info, online users (nicknames, countries, share sizes, classes), \
operators, geographic distribution, and share statistics.

You CANNOT: see IP addresses, kick users, ban users, change configuration, \
execute console commands, or send messages on behalf of users.

Guidelines:
- Always call tools rather than guessing
- Be friendly and helpful
- Never fabricate data — if a tool returns an error, say so
- If asked to do something beyond your access, explain politely
"""


# Context-injected prompts — used when the LLM endpoint doesn't support tools
SYSTEM_PROMPT_ADMIN_CONTEXT = """\
You are a Verlihub DC++ hub assistant with administrator access. You help hub \
operators monitor and manage the hub through natural language.

Below you will find a snapshot of the live hub state. Use ONLY this data to \
answer questions — never invent or guess information that is not in the snapshot.

Available information includes: hub info, online users (with IPs), operators, \
share statistics, geographic distribution.

Guidelines:
- Answer using ONLY the hub data provided below — do not fabricate or hallucinate
- Present user lists as clean tables when there are few users
- Summarize when there are many users (20+)
- Flag anything unusual: zero-share users, suspicious clients, connectivity issues
- Be direct and professional — this is an ops tool
- Format numbers readably (e.g. "1.23 TiB" not raw bytes)
- For write operations (kick, ban, broadcast, config changes), explain that \
tool calling is unavailable and suggest using the hub console directly
"""

SYSTEM_PROMPT_USER_CONTEXT = """\
You are a Verlihub DC++ hub assistant. You help users learn about the hub \
and see who's online.

Below you will find a snapshot of the live hub state. Use ONLY this data to \
answer questions — never invent or guess information that is not in the snapshot.

You can see: hub info, online users (nicknames, countries, share sizes, classes), \
operators, geographic distribution, and share statistics.

You CANNOT: see IP addresses, kick users, ban users, change configuration, \
execute console commands, or send messages on behalf of users.

Guidelines:
- Answer using ONLY the hub data provided below
- Be friendly and helpful
- Never fabricate data
- If asked to do something beyond your access, explain politely
"""


def _inject_hub_context(messages: list[dict], user: "TokenData", is_admin: bool) -> None:
    """
    Replace the system prompt with a context-injected version containing live hub data.

    Called when the LLM endpoint doesn't support tool calling, so the model
    gets all data up-front in the system message instead of calling tools.
    """
    snapshot = _build_hub_context_snapshot(is_admin)
    base_prompt = SYSTEM_PROMPT_ADMIN_CONTEXT if is_admin else SYSTEM_PROMPT_USER_CONTEXT
    full_prompt = base_prompt + f"\n\nThe current operator is: {user.nick} (class {user.user_class})\n" + snapshot

    # Update the system message (always first in the list)
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = full_prompt
    else:
        messages.insert(0, {"role": "system", "content": full_prompt})
    log.info("Injected live hub context into system prompt (%d chars)", len(snapshot))


# =============================================================================
# Chat session manager
# =============================================================================

class ChatSession:
    """Manages conversation history and tool orchestration for one user session."""
    
    def __init__(self, user: TokenData, is_admin: bool, llm_cfg: LlmConfig):
        self.user = user
        self.is_admin = is_admin
        self.llm_cfg = llm_cfg
        self.tools = _build_readonly_tools() + (_build_admin_tools() if is_admin else [])
        self.tools_available = True  # Set to False if endpoint rejects tool_choice
        system_prompt = SYSTEM_PROMPT_ADMIN if is_admin else SYSTEM_PROMPT_USER
        # Personalize the system prompt
        system_prompt += f"\n\nThe current operator is: {user.nick} (class {user.user_class})"
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self.created_at = time.time()
    
    async def chat(self, user_message: str) -> tuple[str, list[dict]]:
        """
        Process a user message through the LLM tool-calling loop.
        
        Returns (response_text, tool_calls_made).
        """
        client = _get_openai_client()
        self.messages.append({"role": "user", "content": user_message})
        tool_calls_made: list[dict] = []
        
        # If tools already known to be unsupported, skip straight to context-injection path
        if not self.tools_available:
            _inject_hub_context(self.messages, self.user, self.is_admin)
            response = await client.chat.completions.create(
                model=self.llm_cfg.model,
                messages=self.messages,
                temperature=self.llm_cfg.temperature,
                max_tokens=self.llm_cfg.max_tokens,
            )
            msg = response.choices[0].message
            self.messages.append(msg.model_dump())
            return msg.content or "(no response)", tool_calls_made
        
        for round_num in range(self.llm_cfg.max_tool_rounds):
            log.debug(f"LLM round {round_num + 1} for {self.user.nick}")
            
            try:
                response = await client.chat.completions.create(
                    model=self.llm_cfg.model,
                    messages=self.messages,
                    tools=self.tools if self.tools else None,
                    tool_choice="auto",
                    temperature=self.llm_cfg.temperature,
                    max_tokens=self.llm_cfg.max_tokens,
                )
            except (openai.BadRequestError, openai.PermissionDeniedError) as exc:
                # Endpoint does not support tool calling — retry without tools
                if round_num == 0:
                    log.warning("Tool calling not supported by endpoint, falling back to plain chat: %s", exc)
                    self.tools_available = False
                    _inject_hub_context(self.messages, self.user, self.is_admin)
                    response = await client.chat.completions.create(
                        model=self.llm_cfg.model,
                        messages=self.messages,
                        temperature=self.llm_cfg.temperature,
                        max_tokens=self.llm_cfg.max_tokens,
                    )
                else:
                    raise
            
            choice = response.choices[0]
            msg = choice.message
            self.messages.append(msg.model_dump())
            
            if not msg.tool_calls:
                return msg.content or "(no response)", tool_calls_made
            
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    fn_args = {}
                
                log.info(f"Tool call by {self.user.nick}: {fn_name}({fn_args})")
                tool_calls_made.append({"name": fn_name, "args": fn_args})
                
                result = await _execute_tool(fn_name, fn_args, self.user, self.is_admin)
                
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        
        # Exhausted rounds — force a summary
        self.messages.append({
            "role": "user",
            "content": "(System: tool call limit reached. Provide your answer with data collected so far.)",
        })
        response = await client.chat.completions.create(
            model=self.llm_cfg.model,
            messages=self.messages,
            temperature=self.llm_cfg.temperature,
            max_tokens=self.llm_cfg.max_tokens,
        )
        return response.choices[0].message.content or "(no response)", tool_calls_made


# Active sessions keyed by "nick:session_id"
_sessions: dict[str, ChatSession] = {}
# Session metadata: titles, timestamps per "nick:session_id"
_session_meta: dict[str, dict] = {}


def _session_key(nick: str, session_id: str) -> str:
    return f"{nick}:{session_id}"


def _ensure_session(
    nick: str, session_id: str, user: TokenData, is_admin: bool, llm_cfg: LlmConfig
) -> ChatSession:
    """Get or create a ChatSession and its metadata entry."""
    key = _session_key(nick, session_id)
    session = _sessions.get(key)
    if session is None:
        session = ChatSession(user, is_admin, llm_cfg)
        _sessions[key] = session
        _session_meta[key] = {
            "session_id": session_id,
            "title": "New chat",
            "created_at": session.created_at,
            "message_count": 0,
        }
    return session


def _update_session_title(key: str, user_message: str) -> None:
    """Set the session title from the first user message (truncated)."""
    meta = _session_meta.get(key)
    if meta and meta["title"] == "New chat":
        meta["title"] = user_message[:80].strip() or "New chat"


def _bump_session_count(key: str) -> None:
    meta = _session_meta.get(key)
    if meta:
        meta["message_count"] += 1


# =============================================================================
# REST endpoints
# =============================================================================


@router.get("/status", response_model=LlmStatusResponse)
async def llm_status(
    user: TokenData = Depends(get_current_user),
):
    """Check LLM integration status."""
    llm_cfg = _get_llm_config()
    
    llm_reachable = False
    if llm_cfg.enabled:
        try:
            client = _get_openai_client()
            await client.models.list()
            llm_reachable = True
        except Exception:
            pass
    
    return LlmStatusResponse(
        enabled=llm_cfg.enabled,
        llm_reachable=llm_reachable,
        model=llm_cfg.model,
        base_url=llm_cfg.base_url,
        min_class=llm_cfg.min_class,
        admin_class=llm_cfg.admin_class,
    )


@router.post("/chat", response_model=ChatResponse)
async def llm_chat(
    request: ChatRequest,
    user: TokenData = Depends(get_current_user),
):
    """Single-turn LLM chat (non-streaming). For the dashboard, prefer the WebSocket endpoint."""
    llm_cfg = _get_llm_config()
    
    if not llm_cfg.enabled:
        raise HTTPException(status_code=503, detail="LLM integration is not enabled")
    
    if user.user_class < llm_cfg.min_class:
        raise HTTPException(status_code=403, detail="Insufficient permissions for AI chat")
    
    is_admin = user.user_class >= llm_cfg.admin_class
    
    # Get or create session
    sid = request.conversation_id or "default"
    session = _ensure_session(user.nick, sid, user, is_admin, llm_cfg)
    key = _session_key(user.nick, sid)
    _update_session_title(key, request.message)
    _bump_session_count(key)

    try:
        response_text, tool_calls = await session.chat(request.message)
    except Exception as e:
        log.exception("LLM chat error")
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")
    
    return ChatResponse(
        response=response_text,
        tool_calls=tool_calls,
        model=llm_cfg.model,
    )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    user: TokenData = Depends(get_current_user),
):
    """List the caller's active chat sessions."""
    prefix = f"{user.nick}:"
    sessions = [
        SessionInfo(**meta)
        for key, meta in _session_meta.items()
        if key.startswith(prefix)
    ]
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return SessionListResponse(sessions=sessions)


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Delete a chat session."""
    key = _session_key(user.nick, session_id)
    _sessions.pop(key, None)
    _session_meta.pop(key, None)
    return {"ok": True}


# =============================================================================
# WebSocket endpoint — streaming chat with tool-call progress
# =============================================================================


async def ws_llm_chat(
    ws: WebSocket,
    token: Optional[str] = None,
    session_id: Optional[str] = None,
):
    """
    WebSocket chat endpoint with tool-call progress.

    Auth: pass JWT as ?token= query param (dashboard sends cookie-derived token).
    Optional: ?session_id= to resume a named session.

    Client sends:  {"message": "who is online?"}
    Server sends:  {"type": "connected", "access": "admin", "model": "llama3.1",
                    "session_id": "abc123"}
                   {"type": "thinking"}
                   {"type": "tool_call", "name": "list_online_users", "args": {}}
                   {"type": "tool_result", "name": "list_online_users", "success": true}
                   {"type": "response", "content": "There are 5 users online..."}
                   {"type": "error", "content": "..."}
    """
    import uuid

    await ws.accept()

    llm_cfg = _get_llm_config()

    if not llm_cfg.enabled:
        await ws.send_json({"type": "error", "content": "LLM integration is not enabled"})
        await ws.close()
        return

    # Authenticate
    user = None
    if token:
        try:
            user = decode_token(token)
        except Exception:
            pass

    if user is None:
        await ws.send_json({"type": "error", "content": "Authentication required"})
        await ws.close()
        return

    if user.user_class < llm_cfg.min_class:
        await ws.send_json({"type": "error", "content": "Insufficient permissions for AI chat"})
        await ws.close()
        return

    is_admin = user.user_class >= llm_cfg.admin_class
    access = "admin" if is_admin else "user"

    # Resolve / create session
    sid = session_id or str(uuid.uuid4())[:8]
    session = _ensure_session(user.nick, sid, user, is_admin, llm_cfg)
    key = _session_key(user.nick, sid)

    log.info("LLM WS session %s for %s (%s)", sid, user.nick, access)
    await ws.send_json({
        "type": "connected",
        "access": access,
        "model": llm_cfg.model,
        "session_id": sid,
    })

    try:
        while True:
            data = await ws.receive_json()
            user_msg = data.get("message", "").strip()
            if not user_msg:
                continue

            _update_session_title(key, user_msg)
            _bump_session_count(key)

            try:
                # Streaming chat with tool-call support.
                # We stream tokens over the WebSocket for a responsive UX.
                client = _get_openai_client()
                session.messages.append({"role": "user", "content": user_msg})
                await ws.send_json({"type": "thinking"})

                for round_num in range(llm_cfg.max_tool_rounds):
                    stream_mode = True

                    # If tools already known to be unsupported, go straight to context-injection
                    if not session.tools_available:
                        _inject_hub_context(session.messages, user, is_admin)
                        try:
                            stream = await client.chat.completions.create(
                                model=llm_cfg.model,
                                messages=session.messages,
                                temperature=llm_cfg.temperature,
                                max_tokens=llm_cfg.max_tokens,
                                stream=True,
                            )
                        except Exception:
                            resp = await client.chat.completions.create(
                                model=llm_cfg.model,
                                messages=session.messages,
                                temperature=llm_cfg.temperature,
                                max_tokens=llm_cfg.max_tokens,
                            )
                            text = resp.choices[0].message.content or "(no response)"
                            session.messages.append(resp.choices[0].message.model_dump())
                            await ws.send_json({"type": "response", "content": text})
                            stream_mode = False
                            stream = None
                            break
                    else:
                        try:
                            stream = await client.chat.completions.create(
                                model=llm_cfg.model,
                                messages=session.messages,
                                tools=session.tools if session.tools else None,
                                tool_choice="auto",
                                temperature=llm_cfg.temperature,
                                max_tokens=llm_cfg.max_tokens,
                                stream=True,
                            )
                        except (openai.BadRequestError, openai.PermissionDeniedError) as exc:
                            if round_num == 0:
                                log.warning("Tool/stream not supported, falling back: %s", exc)
                                session.tools_available = False
                                _inject_hub_context(session.messages, user, is_admin)
                                try:
                                    stream = await client.chat.completions.create(
                                        model=llm_cfg.model,
                                        messages=session.messages,
                                        temperature=llm_cfg.temperature,
                                        max_tokens=llm_cfg.max_tokens,
                                        stream=True,
                                    )
                                except Exception:
                                    # Final fallback: non-streaming, no tools
                                    resp = await client.chat.completions.create(
                                        model=llm_cfg.model,
                                        messages=session.messages,
                                        temperature=llm_cfg.temperature,
                                        max_tokens=llm_cfg.max_tokens,
                                    )
                                    text = resp.choices[0].message.content or "(no response)"
                                    session.messages.append(resp.choices[0].message.model_dump())
                                    await ws.send_json({"type": "response", "content": text})
                                    stream_mode = False
                                    stream = None
                                    break
                            else:
                                raise

                    if not stream_mode:
                        break

                    # -- Consume the async stream --
                    content_parts: list[str] = []
                    tool_calls_acc: dict[int, dict] = {}  # index -> {id, name, arguments}
                    sent_stream_start = False

                    async for chunk in stream:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta

                        # Text content tokens
                        if delta.content:
                            if not sent_stream_start:
                                await ws.send_json({"type": "stream_start"})
                                sent_stream_start = True
                            content_parts.append(delta.content)
                            await ws.send_json({"type": "stream_delta", "content": delta.content})

                        # Tool call deltas
                        if delta.tool_calls:
                            for tc_d in delta.tool_calls:
                                idx = tc_d.index
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                                if tc_d.id:
                                    tool_calls_acc[idx]["id"] = tc_d.id
                                if tc_d.function:
                                    if tc_d.function.name:
                                        tool_calls_acc[idx]["name"] += tc_d.function.name
                                    if tc_d.function.arguments:
                                        tool_calls_acc[idx]["arguments"] += tc_d.function.arguments

                    full_content = "".join(content_parts)

                    # Strip <think>…</think> reasoning blocks (Qwen / DeepSeek models)
                    import re as _re
                    clean_content = _re.sub(r'<think>[\s\S]*?</think>', '', full_content).lstrip()
                    # If still inside an unclosed <think>, drop it
                    think_idx = clean_content.find('<think>')
                    if think_idx >= 0:
                        clean_content = clean_content[:think_idx].rstrip()

                    # Finalise streamed text
                    if sent_stream_start:
                        await ws.send_json({"type": "stream_end", "content": clean_content})

                    # Build message dict for conversation history
                    msg_dict: dict = {"role": "assistant", "content": full_content or None}
                    if tool_calls_acc:
                        msg_dict["tool_calls"] = [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
                        ]
                    session.messages.append(msg_dict)

                    # -- No tool calls → done --
                    if not tool_calls_acc:
                        if not sent_stream_start:
                            await ws.send_json({
                                "type": "response",
                                "content": full_content or "(no response)",
                            })
                        break

                    # -- Execute tool calls --
                    for tc in [tool_calls_acc[i] for i in sorted(tool_calls_acc)]:
                        fn_name = tc["name"]
                        try:
                            fn_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            fn_args = {}

                        await ws.send_json({
                            "type": "tool_call",
                            "name": fn_name,
                            "args": fn_args,
                        })

                        result = await _execute_tool(fn_name, fn_args, user, is_admin)

                        session.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })

                        await ws.send_json({
                            "type": "tool_result",
                            "name": fn_name,
                            "success": "error" not in result,
                        })

                    # Show thinking before next round
                    await ws.send_json({"type": "thinking"})
                else:
                    await ws.send_json({
                        "type": "response",
                        "content": "(Reached tool call limit — here is what I found so far.)",
                    })

            except Exception as e:
                log.exception("LLM WebSocket error")
                await ws.send_json({"type": "error", "content": f"LLM error: {e}"})

    except WebSocketDisconnect:
        log.info("LLM WS disconnected: %s (session %s)", user.nick, sid)
