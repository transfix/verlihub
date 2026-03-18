"""
Tests for log-related WebSocket and emit_log integration.

Covers: emit_log persists to ring buffer, broadcast_log sends to WS clients,
HubEventBroadcaster log emissions, HubEventHandler.OnLog callback.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from starlette.websockets import WebSocketState


# ======================================================================
# emit_log → ring buffer integration
# ======================================================================


class TestEmitLogPersistence:
    """emit_log() should store in the ring buffer AND push to WebSocket."""

    @pytest.fixture(autouse=True)
    def fresh_buffer(self):
        from verlihub.log_buffer import get_log_buffer
        buf = get_log_buffer()
        buf.clear()
        yield buf
        buf.clear()

    def test_emit_log_stores_in_buffer(self, fresh_buffer):
        """emit_log persists entry even when no event loop is running."""
        from verlihub.dashboard.websocket import emit_log
        emit_log("info", "test-msg", "system")
        assert len(fresh_buffer) == 1
        entry = fresh_buffer.get_all()[0]
        assert entry["message"] == "test-msg"
        assert entry["level"] == "info"
        assert entry["log_type"] == "system"

    def test_emit_log_multiple(self, fresh_buffer):
        from verlihub.dashboard.websocket import emit_log
        emit_log("info", "a", "core")
        emit_log("debug", "b", "connection")
        emit_log("warning", "c", "system")
        assert len(fresh_buffer) == 3
        msgs = [e["message"] for e in fresh_buffer.get_all()]
        assert msgs == ["a", "b", "c"]

    def test_emit_log_core_type(self, fresh_buffer):
        from verlihub.dashboard.websocket import emit_log
        emit_log("info", "cpp log line", "core")
        entry = fresh_buffer.get_all()[0]
        assert entry["log_type"] == "core"


# ======================================================================
# broadcast_log
# ======================================================================


class TestBroadcastLog:

    async def test_broadcast_log_sends_to_channel(self):
        from verlihub.dashboard.websocket import broadcast_log, manager

        ws = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()

        await manager.connect(ws, "logs")
        try:
            await broadcast_log("info", "hello", "system")
            ws.send_text.assert_awaited()
            msg = json.loads(ws.send_text.call_args[0][0])
            assert msg["type"] == "log"
            assert msg["level"] == "info"
            assert msg["message"] == "hello"
            assert msg["log_type"] == "system"
            assert "time" in msg
        finally:
            await manager.disconnect(ws, "logs")


# ======================================================================
# HubEventBroadcaster log emissions
# ======================================================================


class TestBroadcasterLogEmissions:
    """HubEventBroadcaster methods should call emit_log for logging events."""

    @pytest.fixture(autouse=True)
    def fresh_buffer(self):
        from verlihub.log_buffer import get_log_buffer
        buf = get_log_buffer()
        buf.clear()
        yield buf
        buf.clear()

    def test_on_user_connect_emits_connection_log(self, fresh_buffer):
        from verlihub.dashboard.websocket import hub_event_broadcaster
        hub_event_broadcaster.on_user_connect("TestUser", "192.168.1.1")
        # Should have stored a "connection" log
        entries = fresh_buffer.get_all()
        conn_logs = [e for e in entries if e["log_type"] == "connection"]
        assert len(conn_logs) >= 1
        assert "TestUser" in conn_logs[0]["message"]

    def test_on_user_disconnect_emits_connection_log(self, fresh_buffer):
        from verlihub.dashboard.websocket import hub_event_broadcaster
        hub_event_broadcaster.on_user_disconnect("TestUser")
        entries = fresh_buffer.get_all()
        conn_logs = [e for e in entries if e["log_type"] == "connection"]
        assert len(conn_logs) >= 1
        assert "TestUser" in conn_logs[0]["message"]

    def test_on_hub_started_emits_system_log(self, fresh_buffer):
        from verlihub.dashboard.websocket import hub_event_broadcaster
        hub_event_broadcaster.on_hub_started()
        entries = fresh_buffer.get_all()
        system_logs = [e for e in entries if e["log_type"] == "system"]
        assert len(system_logs) >= 1

    def test_on_hub_stopping_emits_system_log(self, fresh_buffer):
        from verlihub.dashboard.websocket import hub_event_broadcaster
        hub_event_broadcaster.on_hub_stopping()
        entries = fresh_buffer.get_all()
        system_logs = [e for e in entries if e["log_type"] == "system"]
        assert len(system_logs) >= 1

    def test_on_private_message_emits_pm_log(self, fresh_buffer):
        from verlihub.dashboard.websocket import hub_event_broadcaster
        hub_event_broadcaster.on_private_message("alice", "bob", "hi")
        entries = fresh_buffer.get_all()
        pm_logs = [e for e in entries if e["log_type"] == "pm"]
        assert len(pm_logs) >= 1


# ======================================================================
# HubEventHandler.OnLog
# ======================================================================


class TestHubEventHandlerOnLog:
    """Test the OnLog override in core.py's HubEventHandler."""

    @pytest.fixture(autouse=True)
    def fresh_buffer(self):
        from verlihub.log_buffer import get_log_buffer
        buf = get_log_buffer()
        buf.clear()
        yield buf
        buf.clear()

    _on_log_fn = None  # cached across test methods

    @classmethod
    def _get_on_log(cls):
        """Get the OnLog method, skipping if SWIG module unavailable."""
        if cls._on_log_fn is not None:
            return cls._on_log_fn

        from verlihub import verlihub_core
        if verlihub_core is None:
            pytest.skip("verlihub_core SWIG module not built")

        from verlihub.core import HubEventHandler
        cls._on_log_fn = HubEventHandler.OnLog
        return cls._on_log_fn

    def _make_handler(self):
        """Return a plain object with OnLog bound to it."""
        on_log = self._get_on_log()

        class _FakeHandler:
            OnLog = on_log

        return _FakeHandler()

    def test_on_log_stores_in_buffer(self, fresh_buffer):
        """OnLog should store the C++ log entry in the ring buffer via emit_log."""
        handler = self._make_handler()
        handler.OnLog(0, "[2025-01-01] [L0] [hub.cpp:42] Hub started")

        assert len(fresh_buffer) == 1
        entry = fresh_buffer.get_all()[0]
        assert entry["log_type"] == "core"
        assert entry["level"] == "info"
        assert "Hub started" in entry["message"]

    def test_on_log_no_double_storage(self, fresh_buffer):
        """OnLog should store exactly one entry per call (no double-store)."""
        handler = self._make_handler()
        handler.OnLog(0, "single entry test")
        assert len(fresh_buffer) == 1

    def test_on_log_does_not_raise(self, fresh_buffer):
        """OnLog must swallow exceptions (called from C++ context)."""
        handler = self._make_handler()

        with patch("verlihub.log_buffer.get_log_buffer", side_effect=RuntimeError("boom")):
            # Should not raise
            handler.OnLog(0, "test")

    def test_on_log_level_mapping(self, fresh_buffer):
        """Different C++ levels should map to correct string levels."""
        handler = self._make_handler()

        handler.OnLog(0, "level-0")
        handler.OnLog(2, "level-2")
        handler.OnLog(4, "level-4")

        entries = fresh_buffer.get_all()
        assert len(entries) == 3
        assert entries[0]["level"] == "info"
        assert entries[1]["level"] == "debug"
        assert entries[2]["level"] == "debug"
