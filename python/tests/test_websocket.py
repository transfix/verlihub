"""
Tests for verlihub.dashboard.websocket — ConnectionManager and broadcast helpers.

Covers: ConnectionManager.connect/disconnect/broadcast/send_personal,
get_user_from_ws_cookie, broadcast_hub_event, broadcast_log,
emit_hub_event, emit_log, start/stop_stats_task, HubEventBroadcaster events.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from starlette.websockets import WebSocketState


# ======================================================================
# ConnectionManager
# ======================================================================


class TestConnectionManager:

    @pytest.fixture
    def mgr(self):
        from verlihub.dashboard.websocket import ConnectionManager
        return ConnectionManager()

    def _make_ws(self, state=WebSocketState.CONNECTED):
        ws = AsyncMock()
        ws.client_state = state
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    async def test_connect_accepts_websocket(self, mgr):
        ws = self._make_ws()
        result = await mgr.connect(ws, "hub")
        assert result is True
        assert ws in mgr.active_connections["hub"]
        ws.accept.assert_awaited_once()

    async def test_connect_new_channel(self, mgr):
        ws = self._make_ws()
        result = await mgr.connect(ws, "custom")
        assert result is True
        assert "custom" in mgr.active_connections
        assert ws in mgr.active_connections["custom"]

    async def test_connect_failure_returns_false(self, mgr):
        ws = self._make_ws()
        ws.accept.side_effect = RuntimeError("connection refused")
        result = await mgr.connect(ws, "hub")
        assert result is False

    async def test_disconnect_removes_websocket(self, mgr):
        ws = self._make_ws()
        await mgr.connect(ws, "hub")
        await mgr.disconnect(ws, "hub")
        assert ws not in mgr.active_connections["hub"]

    async def test_disconnect_nonexistent_no_error(self, mgr):
        ws = self._make_ws()
        await mgr.disconnect(ws, "hub")  # Not connected — should not raise

    async def test_broadcast_sends_to_all(self, mgr):
        ws1 = self._make_ws()
        ws2 = self._make_ws()
        await mgr.connect(ws1, "hub")
        await mgr.connect(ws2, "hub")

        msg = {"type": "test", "data": "hello"}
        await mgr.broadcast("hub", msg)

        expected = json.dumps(msg)
        ws1.send_text.assert_awaited_with(expected)
        ws2.send_text.assert_awaited_with(expected)

    async def test_broadcast_cleans_dead_connections(self, mgr):
        ws_alive = self._make_ws()
        ws_dead = self._make_ws()
        ws_dead.send_text.side_effect = RuntimeError("broken pipe")

        await mgr.connect(ws_alive, "hub")
        await mgr.connect(ws_dead, "hub")

        await mgr.broadcast("hub", {"type": "test"})
        # Dead connection should be cleaned up
        assert ws_dead not in mgr.active_connections["hub"]
        assert ws_alive in mgr.active_connections["hub"]

    async def test_broadcast_unknown_channel_no_error(self, mgr):
        await mgr.broadcast("nonexistent", {"type": "test"})  # Should not raise

    async def test_broadcast_skips_disconnected(self, mgr):
        ws = self._make_ws(state=WebSocketState.DISCONNECTED)
        await mgr.connect(ws, "hub")
        await mgr.broadcast("hub", {"type": "test"})
        ws.send_text.assert_not_awaited()

    async def test_send_personal(self, mgr):
        ws = self._make_ws()
        msg = {"type": "hello"}
        await mgr.send_personal(ws, msg)
        ws.send_text.assert_awaited_once_with(json.dumps(msg))

    async def test_send_personal_disconnected(self, mgr):
        ws = self._make_ws(state=WebSocketState.DISCONNECTED)
        await mgr.send_personal(ws, {"type": "hello"})
        ws.send_text.assert_not_awaited()

    async def test_send_personal_error_no_raise(self, mgr):
        ws = self._make_ws()
        ws.send_text.side_effect = RuntimeError("broken")
        await mgr.send_personal(ws, {"type": "hello"})  # Should not raise


# ======================================================================
# get_user_from_ws_cookie
# ======================================================================


class TestGetUserFromWsCookie:

    async def test_valid_bearer_cookie(self):
        from verlihub.dashboard.websocket import get_user_from_ws_cookie

        ws = MagicMock()
        ws.cookies = {"access_token": "Bearer testtoken123"}

        with patch("verlihub.dashboard.websocket.decode_token") as mock_decode:
            mock_decode.return_value = MagicMock(username="admin", user_class=5)
            user = await get_user_from_ws_cookie(ws)
            assert user is not None
            assert user.username == "admin"
            mock_decode.assert_called_once_with("testtoken123")

    async def test_raw_token_without_bearer(self):
        from verlihub.dashboard.websocket import get_user_from_ws_cookie

        ws = MagicMock()
        ws.cookies = {"access_token": "rawtoken456"}

        with patch("verlihub.dashboard.websocket.decode_token") as mock_decode:
            mock_decode.return_value = MagicMock(username="user1")
            user = await get_user_from_ws_cookie(ws)
            mock_decode.assert_called_once_with("rawtoken456")

    async def test_no_cookie_returns_none(self):
        from verlihub.dashboard.websocket import get_user_from_ws_cookie

        ws = MagicMock()
        ws.cookies = {}
        user = await get_user_from_ws_cookie(ws)
        assert user is None

    async def test_invalid_token_returns_none(self):
        from verlihub.dashboard.websocket import get_user_from_ws_cookie

        ws = MagicMock()
        ws.cookies = {"access_token": "Bearer invalid"}

        with patch("verlihub.dashboard.websocket.decode_token",
                   side_effect=Exception("invalid token")):
            user = await get_user_from_ws_cookie(ws)
            assert user is None


# ======================================================================
# broadcast_hub_event / broadcast_log
# ======================================================================


class TestBroadcastFunctions:

    async def test_broadcast_hub_event(self):
        from verlihub.dashboard import websocket as ws_mod

        with patch.object(ws_mod.manager, "broadcast", new_callable=AsyncMock) as mock_bc:
            await ws_mod.broadcast_hub_event("user_join", {"nick": "testuser"})
            mock_bc.assert_awaited_once()
            args = mock_bc.call_args
            assert args[0][0] == "hub"
            msg = args[0][1]
            assert msg["type"] == "user_join"
            assert msg["nick"] == "testuser"
            assert "time" in msg

    async def test_broadcast_log(self):
        from verlihub.dashboard import websocket as ws_mod

        with patch.object(ws_mod.manager, "broadcast", new_callable=AsyncMock) as mock_bc:
            await ws_mod.broadcast_log("info", "Hub started", log_type="hub")
            mock_bc.assert_awaited_once()
            args = mock_bc.call_args
            assert args[0][0] == "logs"
            msg = args[0][1]
            assert msg["type"] == "log"
            assert msg["level"] == "info"
            assert msg["message"] == "Hub started"
            assert msg["log_type"] == "hub"


# ======================================================================
# emit_hub_event / emit_log (sync wrappers)
# ======================================================================


class TestSyncWrappers:

    async def test_emit_hub_event_creates_task(self):
        from verlihub.dashboard import websocket as ws_mod

        with patch.object(ws_mod.manager, "broadcast", new_callable=AsyncMock):
            ws_mod.emit_hub_event("test_event", {"key": "val"})
            # Allow the task to run
            await asyncio.sleep(0.05)

    async def test_emit_log_creates_task(self):
        from verlihub.dashboard import websocket as ws_mod

        with patch.object(ws_mod.manager, "broadcast", new_callable=AsyncMock):
            ws_mod.emit_log("warning", "test warning")
            await asyncio.sleep(0.05)

    def test_emit_hub_event_no_loop_no_raise(self):
        """When no event loop is running, emit should silently pass."""
        from verlihub.dashboard import websocket as ws_mod
        # This runs outside of async context — should not raise
        # (The except RuntimeError path)
        # We need to ensure no loop is running:
        with patch("verlihub.dashboard.websocket.asyncio.get_running_loop",
                   side_effect=RuntimeError("no loop")):
            ws_mod.emit_hub_event("test", {})  # Should not raise

    def test_emit_log_no_loop_no_raise(self):
        from verlihub.dashboard import websocket as ws_mod
        with patch("verlihub.dashboard.websocket.asyncio.get_running_loop",
                   side_effect=RuntimeError("no loop")):
            ws_mod.emit_log("info", "test")  # Should not raise


# ======================================================================
# start_stats_task / stop_stats_task
# ======================================================================


class TestStatsTask:

    async def test_start_and_stop_stats_task(self):
        from verlihub.dashboard import websocket as ws_mod

        ws_mod._stats_task = None

        # start_stats_task uses get_running_loop().create_task, so we call it
        # from within an async test that already has a running loop.
        async def fake_loop():
            await asyncio.sleep(100)

        with patch.object(ws_mod, "_stats_broadcast_loop", return_value=fake_loop()):
            ws_mod.start_stats_task()
            assert ws_mod._stats_task is not None

            ws_mod.stop_stats_task()
            # After stop, _stats_task should be set to None
            assert ws_mod._stats_task is None


# ======================================================================
# HubEventBroadcaster remaining methods
# ======================================================================


class TestHubEventBroadcasterExtended:

    async def test_on_user_login(self):
        from verlihub.dashboard.websocket import HubEventBroadcaster
        from verlihub.dashboard import websocket as ws_mod

        broadcaster = HubEventBroadcaster()
        with patch.object(ws_mod, "emit_hub_event") as mock_emit:
            broadcaster.on_user_login("TestNick", 3)
            mock_emit.assert_called_once()
            args = mock_emit.call_args
            assert args[0][0] == "user_login"

    async def test_on_hub_started(self):
        from verlihub.dashboard.websocket import HubEventBroadcaster
        from verlihub.dashboard import websocket as ws_mod

        broadcaster = HubEventBroadcaster()
        with patch.object(ws_mod, "emit_hub_event") as mock_event, \
             patch.object(ws_mod, "emit_log") as mock_log:
            broadcaster.on_hub_started()
            mock_event.assert_called_once()
            mock_log.assert_called_once()
            # Check event type
            assert mock_event.call_args[0][0] == "hub_status"

    async def test_on_hub_stopping(self):
        from verlihub.dashboard.websocket import HubEventBroadcaster
        from verlihub.dashboard import websocket as ws_mod

        broadcaster = HubEventBroadcaster()
        with patch.object(ws_mod, "emit_hub_event") as mock_event, \
             patch.object(ws_mod, "emit_log") as mock_log:
            broadcaster.on_hub_stopping()
            mock_event.assert_called_once()
            mock_log.assert_called_once()
