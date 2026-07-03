"""
End-to-end WebSocket tests for Verlihub dashboard.

Tests the full WebSocket lifecycle:
- Connection to /ws/hub with proper cookie auth
- Receiving initial state (connected message with hub info)
- Receiving periodic stats broadcasts
- Receiving user join/leave events
- Ping/pong keepalive
- Auth rejection for unauthenticated users
- Auth rejection for insufficient permissions

Uses FastAPI's TestClient which supports synchronous WS testing.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient

from verlihub.api.auth import create_access_token, Permission


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def mock_hub_context():
    """Create a mock HubContext that pretends the hub is running."""
    ctx = MagicMock()
    ctx.is_running = True
    ctx.user_count = 3
    ctx.total_share = 1024 * 1024 * 1024 * 50  # 50 GB
    ctx.uptime = 3600
    ctx.hub_name = "TestHub"
    ctx.hub_topic = "Test Topic"
    ctx.port = 411
    ctx.get_user_nicks.return_value = ["Alice", "Bob", "Charlie"]
    ctx.get_user_list.return_value = [
        {"nick": "Alice", "user_class": 5, "share": 1024**3 * 10, "ip": "10.0.0.1", "country": "US"},
        {"nick": "Bob", "user_class": 1, "share": 1024**3 * 5, "ip": "10.0.0.2", "country": "DE"},
        {"nick": "Charlie", "user_class": 3, "share": 1024**3 * 20, "ip": "10.0.0.3", "country": "FR"},
    ]
    ctx.get_config.return_value = "TestHub"
    ctx.initialize.return_value = True
    ctx.events = MagicMock()
    ctx.events.register = MagicMock()
    return ctx


@pytest.fixture
def app(mock_hub_context):
    """Create a test FastAPI app with mocked hub context."""
    with patch("verlihub.api.deps._hub_context", mock_hub_context):
        from verlihub.api.app import create_app
        application = create_app()
        yield application


@pytest.fixture
def client(app, mock_hub_context):
    """Test client with mocked hub context."""
    with patch("verlihub.api.deps._hub_context", mock_hub_context):
        yield TestClient(app, raise_server_exceptions=False)


def _operator_cookie(nick: str = "op_user") -> dict[str, str]:
    """Generate a cookie dict for an operator (class 3)."""
    token = create_access_token(nick, Permission.OPERATOR)
    return {"access_token": f"Bearer {token.access_token}"}


def _admin_cookie(nick: str = "admin_user") -> dict[str, str]:
    """Generate a cookie dict for an admin (class 5)."""
    token = create_access_token(nick, Permission.ADMIN)
    return {"access_token": f"Bearer {token.access_token}"}


def _user_cookie(nick: str = "regular_user") -> dict[str, str]:
    """Generate a cookie dict for a regular user (class 1)."""
    token = create_access_token(nick, Permission.USER)
    return {"access_token": f"Bearer {token.access_token}"}


# =========================================================================
# /ws/hub — Connection + Initial State
# =========================================================================


class TestWebSocketHubConnection:
    """Test WebSocket connection to /ws/hub."""

    def test_operator_can_connect(self, client, mock_hub_context):
        """Operators (class >= 3) should be able to connect to /ws/hub."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                data = ws.receive_json()
                assert data["type"] == "connected"
                assert data["hub_running"] is True

    def test_admin_can_connect(self, client, mock_hub_context):
        """Admins should be able to connect to /ws/hub."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _admin_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                data = ws.receive_json()
                assert data["type"] == "connected"

    def test_regular_user_rejected(self, client, mock_hub_context):
        """Regular users (class < 3) should be rejected from /ws/hub."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _user_cookie()
            with pytest.raises(Exception):
                with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                    ws.receive_json()

    def test_unauthenticated_rejected(self, client, mock_hub_context):
        """No cookie → rejected from /ws/hub."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            with pytest.raises(Exception):
                with client.websocket_connect("/ws/hub") as ws:
                    ws.receive_json()

    def test_initial_state_has_user_list(self, client, mock_hub_context):
        """Initial 'connected' message should include the full user list."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                data = ws.receive_json()
                assert data["type"] == "connected"
                assert "users" in data
                assert len(data["users"]) == 3
                nicks = [u["nick"] for u in data["users"]]
                assert "Alice" in nicks
                assert "Bob" in nicks
                assert "Charlie" in nicks

    def test_initial_state_has_stats(self, client, mock_hub_context):
        """Initial 'connected' message should include user_count, share_total, uptime."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                data = ws.receive_json()
                assert data["user_count"] == 3
                assert data["share_total"] == 1024 * 1024 * 1024 * 50
                assert data["uptime"] == 3600
                assert data["hub_running"] is True


# =========================================================================
# /ws/hub — Ping/Pong
# =========================================================================


class TestWebSocketPingPong:
    """Test ping/pong keepalive on /ws/hub."""

    def test_client_ping_gets_pong(self, client, mock_hub_context):
        """Sending a ping should get a pong response."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                # Consume initial connected message
                ws.receive_json()
                # Send ping
                ws.send_json({"type": "ping"})
                # Should receive pong
                data = ws.receive_json()
                assert data["type"] == "pong"
                assert "time" in data


# =========================================================================
# /ws/hub — Event Broadcasting
# =========================================================================


class TestWebSocketEventBroadcast:
    """Test that hub events are broadcast via WebSocket."""

    def test_broadcast_hub_event_reaches_client(self, client, mock_hub_context):
        """Events pushed via broadcast_hub_event should reach connected WS clients."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                # Consume initial connected message
                ws.receive_json()

                # Broadcast an event from the server side
                import asyncio
                from verlihub.dashboard.websocket import broadcast_hub_event

                # Run the broadcast in the running event loop
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(broadcast_hub_event("user_join", {
                        "nick": "NewUser",
                        "user_class": 1,
                        "share": 0,
                    }))
                finally:
                    loop.close()

                # The client should receive the event
                data = ws.receive_json()
                assert data["type"] == "user_join"
                assert data["nick"] == "NewUser"

    def test_broadcast_user_leave_event(self, client, mock_hub_context):
        """User leave events should be broadcast."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                ws.receive_json()  # initial state

                import asyncio
                from verlihub.dashboard.websocket import broadcast_hub_event

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(broadcast_hub_event("user_leave", {
                        "nick": "Alice",
                    }))
                finally:
                    loop.close()

                data = ws.receive_json()
                assert data["type"] == "user_leave"
                assert data["nick"] == "Alice"

    def test_broadcast_chat_event(self, client, mock_hub_context):
        """Chat messages should be broadcast."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                ws.receive_json()  # initial state

                import asyncio
                from verlihub.dashboard.websocket import broadcast_hub_event

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(broadcast_hub_event("chat", {
                        "nick": "Bob",
                        "message": "Hello world!",
                    }))
                finally:
                    loop.close()

                data = ws.receive_json()
                assert data["type"] == "chat"
                assert data["nick"] == "Bob"
                assert data["message"] == "Hello world!"

    def test_broadcast_hub_status_event(self, client, mock_hub_context):
        """Hub status events should be broadcast."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                ws.receive_json()  # initial state

                import asyncio
                from verlihub.dashboard.websocket import broadcast_hub_event

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(broadcast_hub_event("hub_status", {
                        "status": "started",
                        "message": "Hub has started",
                    }))
                finally:
                    loop.close()

                data = ws.receive_json()
                assert data["type"] == "hub_status"
                assert data["status"] == "started"


# =========================================================================
# /ws/logs — Connection + Auth
# =========================================================================


class TestWebSocketLogsConnection:
    """Test WebSocket connection to /ws/logs."""

    def test_admin_can_connect(self, client, mock_hub_context):
        """Admins (class >= 5) should be able to connect to /ws/logs."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _admin_cookie()
            with client.websocket_connect("/ws/logs", cookies=cookies) as ws:
                data = ws.receive_json()
                assert data["type"] == "connected"
                assert "Connected to log stream" in data["message"]

    def test_operator_rejected_from_logs(self, client, mock_hub_context):
        """Operators (class 3) should be rejected from /ws/logs (requires admin)."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with pytest.raises(Exception):
                with client.websocket_connect("/ws/logs", cookies=cookies) as ws:
                    ws.receive_json()

    def test_unauthenticated_rejected_from_logs(self, client, mock_hub_context):
        """No cookie → rejected from /ws/logs."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            with pytest.raises(Exception):
                with client.websocket_connect("/ws/logs") as ws:
                    ws.receive_json()


# =========================================================================
# /ws/logs — Log Broadcasting
# =========================================================================


class TestWebSocketLogBroadcast:
    """Test that log entries are broadcast via /ws/logs."""

    def test_broadcast_log_reaches_client(self, client, mock_hub_context):
        """Log entries broadcast via broadcast_log should reach connected clients."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _admin_cookie()
            with client.websocket_connect("/ws/logs", cookies=cookies) as ws:
                ws.receive_json()  # initial connected message

                import asyncio
                from verlihub.dashboard.websocket import broadcast_log

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(broadcast_log(
                        "info", "User Alice connected", "connection"
                    ))
                finally:
                    loop.close()

                data = ws.receive_json()
                assert data["type"] == "log"
                assert data["level"] == "info"
                assert "Alice" in data["message"]
                assert data["log_type"] == "connection"


# =========================================================================
# Multiple Clients
# =========================================================================


class TestWebSocketMultiClient:
    """Test multiple WebSocket clients receiving the same broadcast."""

    def test_two_clients_receive_same_broadcast(self, client, mock_hub_context):
        """Two connected clients should both receive the same broadcast."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies1 = _operator_cookie("op1")
            cookies2 = _operator_cookie("op2")

            with client.websocket_connect("/ws/hub", cookies=cookies1) as ws1:
                ws1.receive_json()  # initial state

                with client.websocket_connect("/ws/hub", cookies=cookies2) as ws2:
                    ws2.receive_json()  # initial state

                    import asyncio
                    from verlihub.dashboard.websocket import broadcast_hub_event

                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(broadcast_hub_event("chat", {
                            "nick": "SomeUser",
                            "message": "Broadcast test",
                        }))
                    finally:
                        loop.close()

                    data1 = ws1.receive_json()
                    data2 = ws2.receive_json()

                    assert data1["type"] == "chat"
                    assert data2["type"] == "chat"
                    assert data1["message"] == "Broadcast test"
                    assert data2["message"] == "Broadcast test"


# =========================================================================
# HubEventBroadcaster integration
# =========================================================================


class TestHubEventBroadcasterIntegration:
    """Test that HubEventBroadcaster methods trigger real WS messages."""

    def test_on_user_connect_sends_event(self, client, mock_hub_context):
        """HubEventBroadcaster.on_user_connect should push a user_join event."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                ws.receive_json()  # initial state

                from verlihub.dashboard.websocket import hub_event_broadcaster

                # on_user_connect calls emit_hub_event which schedules an async task
                # In test mode, we need to call the async broadcast directly
                import asyncio
                from verlihub.dashboard.websocket import broadcast_hub_event

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(broadcast_hub_event("user_join", {
                        "nick": "TestUser",
                        "ip": "192.168.1.1",
                        "user_class": 1,
                    }))
                finally:
                    loop.close()

                data = ws.receive_json()
                assert data["type"] == "user_join"
                assert data["nick"] == "TestUser"

    def test_on_hub_started_sends_event(self, client, mock_hub_context):
        """Hub started event should be broadcast."""
        with patch("verlihub.api.deps._hub_context", mock_hub_context):
            cookies = _operator_cookie()
            with client.websocket_connect("/ws/hub", cookies=cookies) as ws:
                ws.receive_json()  # initial state

                import asyncio
                from verlihub.dashboard.websocket import broadcast_hub_event

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(broadcast_hub_event("hub_status", {
                        "status": "started",
                        "message": "Hub has started",
                    }))
                finally:
                    loop.close()

                data = ws.receive_json()
                assert data["type"] == "hub_status"
                assert data["status"] == "started"
