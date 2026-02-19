"""
Extended tests for dashboard WebSocket module.

Covers: ConnectionManager edge cases, broadcast_hub_event, broadcast_log,
emit_hub_event, emit_log, start/stop_stats_task, HubEventBroadcaster extras.
"""
import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from starlette.websockets import WebSocketState


# ===================================================================
# ConnectionManager
# ===================================================================

class TestConnectionManager:
    """Extended tests for ConnectionManager."""

    async def test_connect_success(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        result = await mgr.connect(ws, "hub")
        assert result is True
        ws.accept.assert_awaited_once()

    async def test_connect_failure(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.accept = AsyncMock(side_effect=RuntimeError("refused"))
        result = await mgr.connect(ws, "hub")
        assert result is False

    async def test_disconnect(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        await mgr.connect(ws, "hub")
        await mgr.disconnect(ws, "hub")
        assert ws not in mgr.active_connections["hub"]

    async def test_disconnect_not_present(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.disconnect(ws, "hub")  # Should not raise

    async def test_disconnect_unknown_channel(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.disconnect(ws, "nonexistent")  # Should not raise

    async def test_broadcast_to_connected(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED
        ws.send_text = AsyncMock()

        await mgr.connect(ws, "hub")
        await mgr.broadcast("hub", {"type": "test"})
        ws.send_text.assert_awaited_once()
        sent = json.loads(ws.send_text.call_args[0][0])
        assert sent["type"] == "test"

    async def test_broadcast_removes_dead_connections(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        good_ws = AsyncMock()
        good_ws.accept = AsyncMock()
        good_ws.client_state = WebSocketState.CONNECTED
        good_ws.send_text = AsyncMock()

        dead_ws = AsyncMock()
        dead_ws.accept = AsyncMock()
        dead_ws.client_state = WebSocketState.CONNECTED
        dead_ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))

        await mgr.connect(good_ws, "hub")
        await mgr.connect(dead_ws, "hub")

        await mgr.broadcast("hub", {"type": "x"})
        # Dead connection should be removed
        assert dead_ws not in mgr.active_connections["hub"]
        assert good_ws in mgr.active_connections["hub"]

    async def test_broadcast_unknown_channel(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        await mgr.broadcast("nonexistent", {"type": "x"})  # Should not raise

    async def test_send_personal(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED
        ws.send_text = AsyncMock()

        await mgr.send_personal(ws, {"type": "hello"})
        ws.send_text.assert_awaited_once()

    async def test_send_personal_error(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED
        ws.send_text = AsyncMock(side_effect=RuntimeError("err"))

        await mgr.send_personal(ws, {"type": "hello"})  # Should not raise

    async def test_send_personal_disconnected(self):
        from verlihub.dashboard.websocket import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.client_state = WebSocketState.DISCONNECTED
        ws.send_text = AsyncMock()

        await mgr.send_personal(ws, {"type": "x"})
        ws.send_text.assert_not_awaited()


# ===================================================================
# broadcast_hub_event / broadcast_log
# ===================================================================

class TestBroadcastFunctions:

    @patch("verlihub.dashboard.websocket.manager")
    async def test_broadcast_hub_event(self, mock_mgr):
        from verlihub.dashboard.websocket import broadcast_hub_event
        mock_mgr.broadcast = AsyncMock()
        await broadcast_hub_event("user_join", {"nick": "Alice"})
        mock_mgr.broadcast.assert_awaited_once()
        call_args = mock_mgr.broadcast.call_args
        assert call_args[0][0] == "hub"
        msg = call_args[0][1]
        assert msg["type"] == "user_join"
        assert msg["nick"] == "Alice"

    @patch("verlihub.dashboard.websocket.manager")
    async def test_broadcast_log(self, mock_mgr):
        from verlihub.dashboard.websocket import broadcast_log
        mock_mgr.broadcast = AsyncMock()
        await broadcast_log("info", "hello", "system")
        mock_mgr.broadcast.assert_awaited_once()
        call_args = mock_mgr.broadcast.call_args
        assert call_args[0][0] == "logs"
        msg = call_args[0][1]
        assert msg["type"] == "log"
        assert msg["level"] == "info"
        assert msg["message"] == "hello"


# ===================================================================
# emit_hub_event / emit_log (sync wrappers)
# ===================================================================

class TestEmitFunctions:

    def test_emit_hub_event_with_loop(self):
        """When an event loop is running, emit_hub_event creates a task."""
        from verlihub.dashboard.websocket import emit_hub_event

        async def _run():
            with patch("verlihub.dashboard.websocket.broadcast_hub_event", new_callable=AsyncMock) as mock_bhe:
                emit_hub_event("test", {"key": "val"})
                # Give the task a chance to run
                await asyncio.sleep(0.01)

        asyncio.get_event_loop().run_until_complete(_run())

    def test_emit_hub_event_no_loop(self):
        """When no event loop is running, emit_hub_event does nothing."""
        from verlihub.dashboard.websocket import emit_hub_event
        # This should not raise
        emit_hub_event("test", {"key": "val"})

    def test_emit_log_with_loop(self):
        from verlihub.dashboard.websocket import emit_log

        async def _run():
            with patch("verlihub.dashboard.websocket.broadcast_log", new_callable=AsyncMock):
                emit_log("info", "test message", "system")
                await asyncio.sleep(0.01)

        asyncio.get_event_loop().run_until_complete(_run())

    def test_emit_log_no_loop(self):
        from verlihub.dashboard.websocket import emit_log
        emit_log("info", "test message")  # Should not raise


# ===================================================================
# start_stats_task / stop_stats_task
# ===================================================================

class TestStatsTask:

    async def test_start_and_stop_stats_task(self):
        import verlihub.dashboard.websocket as ws_mod
        old_task = ws_mod._stats_task

        try:
            ws_mod._stats_task = None
            ws_mod.start_stats_task()
            assert ws_mod._stats_task is not None
            assert not ws_mod._stats_task.done()

            ws_mod.stop_stats_task()
            assert ws_mod._stats_task is None
        finally:
            # Cleanup
            if ws_mod._stats_task and not ws_mod._stats_task.done():
                ws_mod._stats_task.cancel()
            ws_mod._stats_task = old_task

    async def test_stop_stats_task_when_none(self):
        import verlihub.dashboard.websocket as ws_mod
        old_task = ws_mod._stats_task
        try:
            ws_mod._stats_task = None
            ws_mod.stop_stats_task()  # Should not raise
        finally:
            ws_mod._stats_task = old_task

    def test_start_stats_task_no_loop(self):
        """start_stats_task when no loop is running — does nothing."""
        import verlihub.dashboard.websocket as ws_mod
        old_task = ws_mod._stats_task
        try:
            ws_mod._stats_task = None
            # Outside async context
            ws_mod.start_stats_task()
            # Might still be None since no loop
        finally:
            if ws_mod._stats_task and not ws_mod._stats_task.done():
                ws_mod._stats_task.cancel()
            ws_mod._stats_task = old_task


# ===================================================================
# HubEventBroadcaster — extended
# ===================================================================

class TestHubEventBroadcasterExtended:

    def test_on_user_login(self):
        from verlihub.dashboard.websocket import HubEventBroadcaster
        b = HubEventBroadcaster()
        result = b.on_user_login("Alice", 3)
        assert result is True

    def test_on_private_message(self):
        from verlihub.dashboard.websocket import HubEventBroadcaster
        b = HubEventBroadcaster()
        result = b.on_private_message("Alice", "Bob", "hi")
        assert result is True

    def test_on_hub_started(self):
        from verlihub.dashboard.websocket import HubEventBroadcaster
        b = HubEventBroadcaster()
        b.on_hub_started()  # Should not raise

    def test_on_hub_stopping(self):
        from verlihub.dashboard.websocket import HubEventBroadcaster
        b = HubEventBroadcaster()
        b.on_hub_stopping()  # Should not raise


# ===================================================================
# get_user_from_ws_cookie
# ===================================================================

class TestWsCookieAuth:

    async def test_no_cookie(self):
        from verlihub.dashboard.websocket import get_user_from_ws_cookie
        ws = MagicMock()
        ws.cookies = {}
        result = await get_user_from_ws_cookie(ws)
        assert result is None

    async def test_valid_bearer_cookie(self):
        from verlihub.dashboard.websocket import get_user_from_ws_cookie
        from verlihub.api.auth import create_access_token
        token = create_access_token("admin", 5)

        ws = MagicMock()
        ws.cookies = {"access_token": f"Bearer {token.access_token}"}
        result = await get_user_from_ws_cookie(ws)
        assert result is not None
        assert result.nick == "admin"

    async def test_bare_token_cookie(self):
        from verlihub.dashboard.websocket import get_user_from_ws_cookie
        from verlihub.api.auth import create_access_token
        token = create_access_token("admin", 5)

        ws = MagicMock()
        ws.cookies = {"access_token": token.access_token}
        result = await get_user_from_ws_cookie(ws)
        assert result is not None

    async def test_invalid_token_cookie(self):
        from verlihub.dashboard.websocket import get_user_from_ws_cookie
        ws = MagicMock()
        ws.cookies = {"access_token": "Bearer invalid"}
        result = await get_user_from_ws_cookie(ws)
        assert result is None
