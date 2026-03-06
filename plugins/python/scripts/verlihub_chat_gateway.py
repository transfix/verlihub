#!/usr/bin/env python3
"""
Verlihub LLM Chat Gateway

Bridges a self-hosted LLM (Ollama, vLLM, llama.cpp, etc.) to the Verlihub
hub REST API, enabling natural-language interaction with the live hub.

Runs as a sidecar service alongside the hub. The dashboard (verlihub_client.html)
connects to this gateway via WebSocket for streaming chat, and the gateway
orchestrates tool calls against the hub REST API on the LLM's behalf.

Architecture:
─────────────────────────────────────────────────────────────────────────
  Dashboard (browser)
    │  WebSocket  /ws/chat
    ▼
  verlihub_chat_gateway.py    ← this file, standalone service
    │  OpenAI-compatible API  (works with Ollama, vLLM, llama.cpp, LiteLLM)
    ▼
  Self-hosted LLM  (e.g. Ollama running Llama 3, Mistral, Qwen, etc.)
    │  tool_calls in response
    ▼
  verlihub_chat_gateway.py    ← executes tool calls against hub API
    │  HTTP REST
    ▼
  hub_api.py  (FastAPI inside Verlihub)
─────────────────────────────────────────────────────────────────────────

Requirements:
  pip install fastapi uvicorn httpx openai websockets

Configuration (environment variables):
  VERLIHUB_API_URL    - Hub REST API (default: http://localhost:8000)
  LLM_BASE_URL        - OpenAI-compatible endpoint (default: http://localhost:11434/v1 for Ollama)
  LLM_MODEL           - Model name (default: llama3.1)
  LLM_API_KEY         - API key if needed (default: "ollama" for local)
  GATEWAY_PORT        - Port for this gateway (default: 8001)
  MAX_TOOL_ROUNDS     - Max tool-call round-trips per message (default: 5)
  ADMIN_AUTH_TOKEN    - Optional token for admin-level access

Usage:
  # With Ollama (default):
  ollama pull llama3.1
  python verlihub_chat_gateway.py

  # With vLLM:
  LLM_BASE_URL=http://localhost:8000/v1 LLM_MODEL=meta-llama/Llama-3.1-8B python verlihub_chat_gateway.py

  # With llama.cpp server:
  LLM_BASE_URL=http://localhost:8080/v1 LLM_MODEL=llama3 python verlihub_chat_gateway.py

  # With LiteLLM proxy (routes to any backend):
  LLM_BASE_URL=http://localhost:4000/v1 LLM_MODEL=ollama/llama3.1 python verlihub_chat_gateway.py
"""

import os
import json
import asyncio
import logging
import time
from typing import Any, Optional
from datetime import datetime

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("chat-gateway")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HUB_API_URL = os.environ.get("VERLIHUB_API_URL", "http://localhost:8000")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")  # Ollama default
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "ollama")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "8001"))
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "5"))
ADMIN_AUTH_TOKEN = os.environ.get("ADMIN_AUTH_TOKEN", "")

# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible — works with Ollama, vLLM, llama.cpp, etc.)
# ---------------------------------------------------------------------------

llm = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

# ---------------------------------------------------------------------------
# Hub API tools — definitions the LLM can call
# ---------------------------------------------------------------------------

# Read-only tools available to all users
READONLY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_hub_info",
            "description": "Get current hub info: name, description, topic, version, user count, total share.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_users",
            "description": "List all connected users with nick, IP, country, share size, class, client tag.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_info",
            "description": "Get detailed info about one user by nickname.",
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
            "description": "List all connected operators (mods/admins).",
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
            "name": "get_geography",
            "description": "Get geographic distribution: user counts per country.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_share_stats",
            "description": "Get share statistics: total, average, top sharers.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_statistics",
            "description": "Get full hub statistics: uptime, users, share, cache age.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_health",
            "description": "Health check — is the hub responsive?",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# Admin-only tools (network diagnostics, potentially destructive)
ADMIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ping_ip",
            "description": "ICMP ping an IP address. Returns latency and packet loss.",
            "parameters": {
                "type": "object",
                "properties": {"ip": {"type": "string", "description": "IP address to ping"}},
                "required": ["ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "traceroute_ip",
            "description": "Traceroute to an IP. Returns network path with hop latencies.",
            "parameters": {
                "type": "object",
                "properties": {"ip": {"type": "string", "description": "IP address"}},
                "required": ["ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_os",
            "description": "Detect OS of a host by TCP/IP fingerprinting.",
            "parameters": {
                "type": "object",
                "properties": {"ip": {"type": "string", "description": "IP address"}},
                "required": ["ip"],
            },
        },
    },
]

# Map tool names → hub API endpoints
TOOL_ENDPOINTS = {
    "get_hub_info": "/hub",
    "list_users": "/users",
    "get_user_info": "/user/{nick}",
    "list_operators": "/ops",
    "list_bots": "/bots",
    "get_geography": "/geo",
    "get_share_stats": "/share",
    "get_statistics": "/stats",
    "check_health": "/health",
    "ping_ip": "/ping/{ip}",
    "traceroute_ip": "/traceroute/{ip}",
    "detect_os": "/os/{ip}",
}

# ---------------------------------------------------------------------------
# Hub API client
# ---------------------------------------------------------------------------

async def call_hub_api(tool_name: str, arguments: dict) -> str:
    """Execute a tool call against the hub REST API. Returns JSON string."""
    endpoint_template = TOOL_ENDPOINTS.get(tool_name)
    if not endpoint_template:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    # Substitute path parameters
    endpoint = endpoint_template
    for key, value in arguments.items():
        endpoint = endpoint.replace(f"{{{key}}}", str(value))

    url = f"{HUB_API_URL}{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return json.dumps(resp.json(), default=str)
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"Hub API returned {e.response.status_code}", "detail": e.response.text[:500]})
    except httpx.ConnectError:
        return json.dumps({"error": "Cannot reach hub API", "url": url})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_ADMIN = """\
You are a Verlihub DC++ hub assistant with administrator access. You help hub \
operators monitor and manage the hub through natural language.

You have access to tools that query the live hub. Use them to answer questions \
accurately — never guess hub state, always check. When presenting data, be \
concise but thorough. Format numbers readably (e.g. "1.23 TiB" not "1352914698240").

You can see: hub info, online users, operators, bots, geography, share stats, \
full statistics, health status, and run network diagnostics (ping, traceroute, \
OS detection) on user IPs.

Guidelines:
- Always call tools rather than assuming hub state
- Present user lists as clean tables when there are few users
- Summarize when there are many users (20+)
- Flag anything unusual: zero-share users, suspicious clients, connectivity issues
- Be direct and professional — this is an ops tool, not a social chatbot
"""

SYSTEM_PROMPT_USER = """\
You are a Verlihub DC++ hub assistant. You help users learn about the hub \
and see who's online.

You have access to read-only tools that show public hub information. Use them \
to answer questions accurately. You cannot perform administrative actions, \
run network diagnostics, or access private user data like IP addresses.

When showing user info, show only: nickname, country, share size, and client. \
Never show IP addresses or other private data to regular users.

Guidelines:
- Always call tools rather than assuming hub state
- Be friendly and helpful
- If asked to do something beyond your access, explain politely
- You cannot kick, ban, or modify the hub
"""

# ---------------------------------------------------------------------------
# Chat session — manages conversation state per WebSocket connection
# ---------------------------------------------------------------------------

class ChatSession:
    """One conversation with an LLM, maintaining message history."""

    def __init__(self, is_admin: bool = False):
        self.is_admin = is_admin
        self.tools = READONLY_TOOLS + (ADMIN_TOOLS if is_admin else [])
        system_prompt = SYSTEM_PROMPT_ADMIN if is_admin else SYSTEM_PROMPT_USER
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self.created_at = time.time()

    async def chat(self, user_message: str) -> str:
        """
        Send a user message, let the LLM reason and call tools in a loop,
        return the final text response.
        """
        self.messages.append({"role": "user", "content": user_message})

        for round_num in range(MAX_TOOL_ROUNDS):
            log.info(f"LLM round {round_num + 1}, messages={len(self.messages)}")

            response = await llm.chat.completions.create(
                model=LLM_MODEL,
                messages=self.messages,
                tools=self.tools if self.tools else None,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2048,
            )

            choice = response.choices[0]
            assistant_msg = choice.message

            # Append the assistant message (may contain tool_calls)
            self.messages.append(assistant_msg.model_dump())

            if not assistant_msg.tool_calls:
                # No tool calls — we have the final answer
                return assistant_msg.content or "(no response)"

            # Execute each tool call and append results
            for tc in assistant_msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    fn_args = {}

                log.info(f"Tool call: {fn_name}({fn_args})")

                # Check admin-only tools
                if fn_name in ("ping_ip", "traceroute_ip", "detect_os") and not self.is_admin:
                    result = json.dumps({"error": "Permission denied — admin tool"})
                else:
                    result = await call_hub_api(fn_name, fn_args)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        # Exhausted tool rounds — ask LLM to wrap up
        self.messages.append({
            "role": "user",
            "content": "(System: maximum tool call rounds reached. Please provide your best answer with the data collected so far.)",
        })
        response = await llm.chat.completions.create(
            model=LLM_MODEL,
            messages=self.messages,
            temperature=0.3,
            max_tokens=2048,
        )
        return response.choices[0].message.content or "(no response)"


# ---------------------------------------------------------------------------
# FastAPI app + WebSocket endpoint
# ---------------------------------------------------------------------------

app = FastAPI(title="Verlihub Chat Gateway", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Active sessions keyed by WebSocket id
sessions: dict[int, ChatSession] = {}


@app.get("/health")
async def health():
    """Gateway health check."""
    # Also check LLM connectivity
    llm_ok = False
    try:
        models = await llm.models.list()
        llm_ok = True
    except Exception:
        pass

    hub_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{HUB_API_URL}/health")
            hub_ok = r.status_code == 200
    except Exception:
        pass

    return {
        "gateway": "ok",
        "llm_reachable": llm_ok,
        "llm_endpoint": LLM_BASE_URL,
        "llm_model": LLM_MODEL,
        "hub_api_reachable": hub_ok,
        "hub_api_url": HUB_API_URL,
        "active_sessions": len(sessions),
    }


@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket, token: Optional[str] = Query(None)):
    """
    WebSocket chat endpoint.

    Query params:
      ?token=<admin_token>  — authenticate as admin for full tool access
    
    Client sends: {"message": "who is online?"}
    Server sends: {"type": "response", "content": "..."} 
                  {"type": "tool_call", "name": "...", "args": {...}}  (progress)
                  {"type": "error", "content": "..."}
    """
    await ws.accept()

    # Determine access level
    is_admin = bool(ADMIN_AUTH_TOKEN and token == ADMIN_AUTH_TOKEN)
    session = ChatSession(is_admin=is_admin)
    session_id = id(ws)
    sessions[session_id] = session

    level = "admin" if is_admin else "user"
    log.info(f"New chat session {session_id} ({level})")

    await ws.send_json({
        "type": "connected",
        "access": level,
        "model": LLM_MODEL,
    })

    try:
        while True:
            data = await ws.receive_json()
            user_msg = data.get("message", "").strip()
            if not user_msg:
                continue

            await ws.send_json({"type": "thinking"})

            try:
                # Intercept to show tool-call progress
                # (We use the session.chat method which handles the loop internally,
                #  but we could also break it apart for streaming progress — shown
                #  in the streaming variant below)
                answer = await session.chat(user_msg)
                await ws.send_json({"type": "response", "content": answer})
            except Exception as e:
                log.exception("Chat error")
                await ws.send_json({"type": "error", "content": f"Error: {e}"})

    except WebSocketDisconnect:
        log.info(f"Session {session_id} disconnected")
    finally:
        sessions.pop(session_id, None)


@app.websocket("/ws/chat/stream")
async def websocket_chat_stream(ws: WebSocket, token: Optional[str] = Query(None)):
    """
    Streaming variant — sends tool-call progress and streams the final
    response token-by-token for a more interactive UX.
    """
    await ws.accept()

    is_admin = bool(ADMIN_AUTH_TOKEN and token == ADMIN_AUTH_TOKEN)
    tools = READONLY_TOOLS + (ADMIN_TOOLS if is_admin else [])
    system_prompt = SYSTEM_PROMPT_ADMIN if is_admin else SYSTEM_PROMPT_USER
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    level = "admin" if is_admin else "user"
    await ws.send_json({"type": "connected", "access": level, "model": LLM_MODEL})

    try:
        while True:
            data = await ws.receive_json()
            user_msg = data.get("message", "").strip()
            if not user_msg:
                continue

            messages.append({"role": "user", "content": user_msg})
            await ws.send_json({"type": "thinking"})

            for round_num in range(MAX_TOOL_ROUNDS):
                response = await llm.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=2048,
                )

                choice = response.choices[0]
                assistant_msg = choice.message
                messages.append(assistant_msg.model_dump())

                if not assistant_msg.tool_calls:
                    # Final answer — send it
                    await ws.send_json({
                        "type": "response",
                        "content": assistant_msg.content or "(no response)",
                    })
                    break

                # Execute tool calls and report progress
                for tc in assistant_msg.tool_calls:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        fn_args = {}

                    await ws.send_json({
                        "type": "tool_call",
                        "name": fn_name,
                        "args": fn_args,
                    })

                    if fn_name in ("ping_ip", "traceroute_ip", "detect_os") and not is_admin:
                        result = json.dumps({"error": "Permission denied"})
                    else:
                        result = await call_hub_api(fn_name, fn_args)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                    await ws.send_json({
                        "type": "tool_result",
                        "name": fn_name,
                        "success": "error" not in result,
                    })
            else:
                # Max rounds exhausted
                await ws.send_json({
                    "type": "response",
                    "content": "(Reached maximum tool call rounds. Here's what I found so far based on the data collected.)",
                })

    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    log.info(f"Starting Verlihub Chat Gateway on port {GATEWAY_PORT}")
    log.info(f"  Hub API:  {HUB_API_URL}")
    log.info(f"  LLM:     {LLM_BASE_URL} / {LLM_MODEL}")
    log.info(f"  Admin auth: {'enabled' if ADMIN_AUTH_TOKEN else 'disabled (all sessions are user-level)'}")

    uvicorn.run(app, host="0.0.0.0", port=GATEWAY_PORT, log_level="info")
