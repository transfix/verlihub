"""
WebSocket endpoints for real-time dashboard updates.

Provides:
- /ws/hub - Real-time hub events (user join/leave, chat, etc.)
- /ws/logs - Real-time log streaming
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from starlette.websockets import WebSocketState

from verlihub.api.auth import decode_token, TokenData

logger = logging.getLogger(__name__)

ws_router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections."""
    
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {
            "hub": [],
            "logs": [],
            "hublist": [],
        }
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, channel: str) -> bool:
        """Accept a WebSocket connection on a channel."""
        try:
            await websocket.accept()
            async with self._lock:
                if channel not in self.active_connections:
                    self.active_connections[channel] = []
                self.active_connections[channel].append(websocket)
            logger.debug(f"WebSocket connected to {channel}")
            return True
        except Exception as e:
            logger.error(f"Failed to accept WebSocket: {e}")
            return False
    
    async def disconnect(self, websocket: WebSocket, channel: str):
        """Remove a WebSocket from a channel."""
        async with self._lock:
            if channel in self.active_connections:
                try:
                    self.active_connections[channel].remove(websocket)
                except ValueError:
                    pass
        logger.debug(f"WebSocket disconnected from {channel}")
    
    async def broadcast(self, channel: str, message: dict):
        """Broadcast a message to all connections on a channel."""
        if channel not in self.active_connections:
            return
        
        message_json = json.dumps(message)
        dead_connections = []
        
        async with self._lock:
            connections = list(self.active_connections[channel])
        
        for connection in connections:
            try:
                if connection.client_state == WebSocketState.CONNECTED:
                    await connection.send_text(message_json)
            except Exception:
                dead_connections.append(connection)
        
        # Clean up dead connections
        if dead_connections:
            async with self._lock:
                for conn in dead_connections:
                    try:
                        self.active_connections[channel].remove(conn)
                    except ValueError:
                        pass
    
    async def send_personal(self, websocket: WebSocket, message: dict):
        """Send a message to a specific connection."""
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps(message))
        except Exception as e:
            logger.error(f"Failed to send personal message: {e}")


manager = ConnectionManager()


async def get_user_from_ws_cookie(websocket: WebSocket) -> Optional[TokenData]:
    """Get user from WebSocket cookie."""
    try:
        access_token = websocket.cookies.get("access_token")
        if access_token is None:
            return None
        
        # Cookie format is "Bearer <token>"
        if access_token.startswith("Bearer "):
            token = access_token[7:]
        else:
            token = access_token
        
        return decode_token(token)
    except Exception:
        return None


@ws_router.websocket("/llm-chat")
async def websocket_llm_chat(websocket: WebSocket):
    """LLM chat WebSocket — delegates to the llm route module."""
    from verlihub.api.routes.llm import ws_llm_chat
    token = websocket.query_params.get("token")
    session_id = websocket.query_params.get("session_id")
    await ws_llm_chat(websocket, token=token, session_id=session_id)


@ws_router.websocket("/hub")
async def websocket_hub(websocket: WebSocket):
    """
    WebSocket endpoint for real-time hub events.
    
    Broadcasts events like:
    - user_join: {type: "user_join", user: {nick, class, share}}
    - user_leave: {type: "user_leave", nick: "..."}
    - chat: {type: "chat", nick: "...", message: "..."}
    - hub_stats: {type: "stats", user_count, share_total, uptime}
    """
    user = await get_user_from_ws_cookie(websocket)
    
    if not user or user.user_class < 3:  # Require operator
        await websocket.close(code=4403, reason="Operator access required")
        return
    
    if not await manager.connect(websocket, "hub"):
        return
    
    try:
        # Send initial connection confirmation with full hub state
        from verlihub.api.deps import get_hub_context as _get_ctx
        _ctx = _get_ctx()
        initial_state = {
            "type": "connected",
            "message": "Connected to hub events",
            "time": datetime.now(timezone.utc).isoformat(),
            "hub_running": _ctx.is_running if _ctx else False,
            "user_count": _ctx.user_count if _ctx else 0,
            "share_total": _ctx.total_share if _ctx else 0,
            "uptime": _ctx.uptime if _ctx else 0,
            "users": _ctx.get_user_list() if _ctx and hasattr(_ctx, 'get_user_list') else [],
        }
        await manager.send_personal(websocket, initial_state)
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0,  # 30 second timeout
                )
                
                # Handle ping/pong
                try:
                    message = json.loads(data)
                    if message.get("type") == "ping":
                        await manager.send_personal(websocket, {
                            "type": "pong",
                            "time": datetime.now(timezone.utc).isoformat(),
                        })
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                # Send keepalive ping
                await manager.send_personal(websocket, {
                    "type": "ping",
                    "time": datetime.now(timezone.utc).isoformat(),
                })
                
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, "hub")


@ws_router.websocket("/logs")
async def websocket_logs(websocket: WebSocket):
    """
    WebSocket endpoint for real-time log streaming.
    
    Broadcasts log entries like:
    - {type: "log", level: "info", message: "...", time: "..."}
    """
    user = await get_user_from_ws_cookie(websocket)
    
    if not user or user.user_class < 5:  # Require admin
        await websocket.close(code=4403, reason="Admin access required")
        return
    
    if not await manager.connect(websocket, "logs"):
        return
    
    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "message": "Connected to log stream",
            "time": datetime.now(timezone.utc).isoformat(),
        })
        
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0,
                )
                
                try:
                    message = json.loads(data)
                    if message.get("type") == "ping":
                        await manager.send_personal(websocket, {
                            "type": "pong",
                            "time": datetime.now(timezone.utc).isoformat(),
                        })
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                await manager.send_personal(websocket, {
                    "type": "ping",
                    "time": datetime.now(timezone.utc).isoformat(),
                })
                
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, "logs")


@ws_router.websocket("/hublist")
async def websocket_hublist(websocket: WebSocket):
    """
    WebSocket endpoint for real-time hublist updates (master-only).

    Broadcasts events like:
    - hublist_register: A new hub registered
    - hublist_update: Existing hub refreshed
    - hublist_offline: Hub went stale/offline
    - hublist_removed: Hub deleted by admin
    - hublist_blocked: Registration was blocked
    - hublist_block_added / hublist_block_removed: Block rule changes
    """
    user = await get_user_from_ws_cookie(websocket)

    if not user or user.user_class < 10:  # Require master
        await websocket.close(code=4403, reason="Master access required")
        return

    if not await manager.connect(websocket, "hublist"):
        return

    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "message": "Connected to hublist events",
            "time": datetime.now(timezone.utc).isoformat(),
        })

        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0,
                )

                try:
                    message = json.loads(data)
                    if message.get("type") == "ping":
                        await manager.send_personal(websocket, {
                            "type": "pong",
                            "time": datetime.now(timezone.utc).isoformat(),
                        })
                except json.JSONDecodeError:
                    pass

            except asyncio.TimeoutError:
                await manager.send_personal(websocket, {
                    "type": "ping",
                    "time": datetime.now(timezone.utc).isoformat(),
                })

    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, "hublist")


# --- Functions to be called by the hub core to push events ---


async def broadcast_hub_event(event_type: str, data: dict):
    """Broadcast a hub event to all connected clients."""
    message = {
        "type": event_type,
        "time": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    await manager.broadcast("hub", message)


async def broadcast_log(level: str, message: str, log_type: str = "system"):
    """Broadcast a log entry to all connected clients."""
    entry = {
        "type": "log",
        "level": level,
        "message": message,
        "log_type": log_type,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("logs", entry)


# ---------------------------------------------------------------------------
# Thread-safe synchronous wrappers for pushing events from any thread
# (including the C++ server thread which is NOT an asyncio thread).
# ---------------------------------------------------------------------------

_ws_loop: Optional[asyncio.AbstractEventLoop] = None


def set_ws_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store the asyncio event loop that the WebSocket manager runs on.

    Must be called from within the running event loop (e.g. during lifespan
    startup or ``start_stats_task``).
    """
    global _ws_loop
    _ws_loop = loop


def emit_hub_event(event_type: str, data: dict) -> None:
    """Schedule a hub event broadcast (safe to call from any thread)."""
    loop = _ws_loop
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast_hub_event(event_type, data), loop)


def emit_log(level: str, message: str, log_type: str = "system") -> None:
    """Schedule a log broadcast (safe to call from any thread)."""
    loop = _ws_loop
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast_log(level, message, log_type), loop)


# --- Background task for periodic stats updates ---

_stats_task: Optional[asyncio.Task] = None


def _collect_stats_sync() -> Optional[dict]:
    """Collect hub stats in a worker thread (avoids blocking the event loop)."""
    from verlihub.api.deps import get_hub_context
    ctx = get_hub_context()
    if ctx is None:
        return None
    try:
        return {
            "type": "stats",
            "user_count": ctx.user_count,
            "share_total": ctx.total_share,
            "hub_running": ctx.is_running,
            "uptime": ctx.uptime,
            "users": ctx.get_user_list() if hasattr(ctx, 'get_user_list') else [],
        }
    except Exception:
        return None


async def _stats_broadcast_loop():
    """Background task to broadcast hub stats periodically."""
    while True:
        try:
            await asyncio.sleep(5)  # Update every 5 seconds
            stats = await asyncio.to_thread(_collect_stats_sync)
            if stats is not None:
                await manager.broadcast("hub", stats)
        except Exception as e:
            logger.error(f"Stats broadcast error: {e}")


def start_stats_task():
    """Start the background stats broadcast task."""
    global _stats_task
    if _stats_task is None or _stats_task.done():
        try:
            loop = asyncio.get_running_loop()
            set_ws_event_loop(loop)  # remember for cross-thread emit
            _stats_task = loop.create_task(_stats_broadcast_loop())
            logger.info("Started hub stats broadcast task")
        except RuntimeError:
            pass


def stop_stats_task():
    """Stop the background stats broadcast task."""
    global _stats_task
    if _stats_task and not _stats_task.done():
        _stats_task.cancel()
        _stats_task = None


# --- Hub event callback integration ---

class HubEventBroadcaster:
    """
    Broadcaster that hooks into hub events and pushes them via WebSocket.
    
    Use this in core.py to connect hub events to the WebSocket system:
    
        from verlihub.dashboard.websocket import hub_event_broadcaster
        
        ctx.events.register('user_connect', hub_event_broadcaster.on_user_connect)
        ctx.events.register('user_disconnect', hub_event_broadcaster.on_user_disconnect)
        ctx.events.register('chat_message', hub_event_broadcaster.on_chat_message)
    """
    
    def on_user_connect(self, nick: str, ip: str) -> bool:
        """Handle user connect event.

        IMPORTANT: This is called from the C++ server thread while
        m_clients_mutex is held.  We must NOT call back into C++
        methods that acquire the same mutex (e.g. get_user_list /
        GetUserInfoSnapshots) or we will deadlock.
        """
        emit_hub_event("user_join", {"nick": nick, "ip": ip})
        emit_log("info", f"User connected: {nick}", "connection")
        return True

    def on_user_login(self, nick: str, user_class: int) -> bool:
        """Handle user login event.

        Called from C++ while m_clients_mutex is held — do NOT call
        back into the C++ core.
        """
        emit_hub_event("user_login", {"nick": nick, "user_class": user_class})
        return True

    def on_user_disconnect(self, nick: str) -> None:
        """Handle user disconnect event.

        Called from C++ while m_clients_mutex is held — do NOT call
        back into the C++ core.
        """
        emit_hub_event("user_leave", {"nick": nick})
        emit_log("info", f"User disconnected: {nick}", "connection")

    def on_chat_message(self, nick: str, message: str) -> bool:
        """Handle main chat message."""
        emit_hub_event("chat", {
            "nick": nick,
            "message": message,
        })
        return True

    def on_private_message(self, from_nick: str, to_nick: str, message: str) -> bool:
        """Handle private message (for logging, not broadcast)."""
        emit_log("debug", f"PM from {from_nick} to {to_nick}", "pm")
        return True

    def on_hub_started(self) -> None:
        """Handle hub started event."""
        emit_hub_event("hub_status", {
            "status": "started",
            "message": "Hub has started",
        })
        emit_log("info", "Hub started", "system")

    def on_hub_stopping(self) -> None:
        """Handle hub stopping event."""
        emit_hub_event("hub_status", {
            "status": "stopping",
            "message": "Hub is shutting down",
        })
        emit_log("warning", "Hub stopping", "system")


# Global broadcaster instance
hub_event_broadcaster = HubEventBroadcaster()
