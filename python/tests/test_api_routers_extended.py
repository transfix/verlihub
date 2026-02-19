"""
Extended tests for API routers with mocked hub context.

These tests use FastAPI dependency overrides to inject a mock hub context,
exercising the actual endpoint handler code paths that were not covered
by the basic permission-checking tests in test_api_endpoints.py.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from verlihub.api.auth import Permission, create_access_token


# =============================================================================
# Mock Hub Context
# =============================================================================


def make_mock_hub_ctx(**overrides):
    """Create a mock hub context with configurable attributes."""
    defaults = {
        "is_running": True,
        "user_count": 3,
        "total_share": 1073741824,  # 1 GB
        "hub_name": "TestHub",
        "hub_topic": "Welcome!",
    }
    defaults.update(overrides)

    ctx = mock.MagicMock()

    # Simple attribute access
    for attr in ("is_running", "user_count", "total_share", "hub_name", "hub_topic"):
        setattr(type(ctx), attr, mock.PropertyMock(return_value=defaults[attr]))

    # get_config returns the default if provided, else ""
    config_values = {
        ("config", "hub_name"): "TestHub",
        ("config", "hub_desc"): "A test hub",
        ("config", "hub_host"): "dchub://test.example.com",
        ("config", "hub_owner"): "admin",
        ("config", "hub_encoding"): "UTF-8",
        ("config", "listen_port"): "4111",
        ("config", "max_users"): "500",
        ("config", "min_share"): "0",
        ("config", "tls_enabled"): "0",
        ("config", "hub_icon_url"): "",
        ("config", "hub_logo_url"): "",
        ("config", "hub_version"): "Verlihub 1.5",
    }

    def mock_get_config(section, key, default=""):
        return config_values.get((section, key), default)

    ctx.get_config = mock_get_config

    # User nicks
    ctx.get_user_nicks.return_value = ["Alice", "Bob", "OpUser"]
    ctx.get_bot_nicks.return_value = ["HubSec"]

    # Per-user methods
    user_classes = {"Alice": 1, "Bob": 2, "OpUser": 5}
    user_ips = {"Alice": "10.0.0.1", "Bob": "10.0.0.2", "OpUser": "10.0.0.3"}
    user_shares = {"Alice": 1048576, "Bob": 2097152, "OpUser": 5242880}
    user_ccs = {"Alice": "US", "Bob": "DE", "OpUser": "US"}
    user_hosts = {"Alice": "alice.example.com", "Bob": "bob.example.com", "OpUser": "op.example.com"}

    ctx.get_user_class = lambda nick: user_classes.get(nick, 0)
    ctx.get_user_share = lambda nick: user_shares.get(nick, 0)
    ctx.get_user_ip = lambda nick: user_ips.get(nick, "")
    ctx.get_user_cc = lambda nick: user_ccs.get(nick, "")
    ctx.get_user_host = lambda nick: user_hosts.get(nick, "")
    ctx.get_bot_description = lambda nick: f"{nick} bot"

    # Geo info
    ctx.get_user_geo = lambda nick: {
        "country": "United States" if user_ccs.get(nick) == "US" else "Germany",
        "city": "New York",
        "region": "NY",
        "asn": "AS1234",
    }
    ctx.get_user_myinfo = lambda nick: {
        "description": f"{nick}'s description",
        "tag": "<Client V:1.0>",
        "email": f"{nick.lower()}@example.com",
    }

    # Find user
    ctx.find_user = lambda nick: nick in user_classes

    # Actions
    ctx.kick_user = mock.MagicMock(return_value=True)
    ctx.send_to_user = mock.MagicMock(return_value=True)
    ctx.send_to_all = mock.MagicMock(return_value=True)
    ctx.send_to_class = mock.MagicMock(return_value=True)
    ctx.request_shutdown = mock.MagicMock()
    ctx.cpp = mock.MagicMock()

    return ctx


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_ctx():
    """A default mock hub context."""
    return make_mock_hub_ctx()


@pytest.fixture
def app(mock_ctx):
    """Create test app with mocked hub context."""
    from verlihub.api.app import create_app

    test_app = create_app()

    # Override hub context deps in all routers
    from verlihub.api.routes import hub as hub_mod
    from verlihub.api.routes import stats as stats_mod
    from verlihub.api.routes import users as users_mod

    test_app.dependency_overrides[hub_mod.get_hub_context] = lambda: mock_ctx
    test_app.dependency_overrides[stats_mod.get_hub_context] = lambda: mock_ctx
    test_app.dependency_overrides[users_mod.get_hub_context] = lambda: mock_ctx

    yield test_app

    test_app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def op_header():
    token = create_access_token("op_user", Permission.OPERATOR)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def admin_header():
    token = create_access_token("admin", Permission.ADMIN)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def master_header():
    token = create_access_token("master", Permission.MASTER)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def user_header():
    token = create_access_token("regular", Permission.USER)
    return {"Authorization": f"Bearer {token.access_token}"}


# =============================================================================
# Hub Router — with mock context
# =============================================================================


class TestHubStatusWithContext:
    """Test hub endpoints that return real data from mock context."""

    def test_hub_status(self, client):
        resp = client.get("/api/v1/hub/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_running"] is True
        assert data["hub_name"] == "TestHub"
        assert data["total_share"] == 1073741824
        assert data["total_share_gb"] == pytest.approx(1.0)

    def test_hub_config(self, client):
        resp = client.get("/api/v1/hub/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["hub_name"] == "TestHub"
        assert data["hub_desc"] == "A test hub"
        assert data["listen_port"] == 4111
        assert data["max_users"] == 500
        assert data["hub_encoding"] == "UTF-8"

    def test_hub_info(self, client):
        resp = client.get("/api/v1/hub/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "TestHub"
        assert data["description"] == "A test hub"
        assert data["version"] == "Verlihub 1.5"
        assert data["uptime_seconds"] >= 0
        assert "uptime_formatted" in data
        assert data["tls_enabled"] is False

    def test_set_topic(self, client, op_header):
        resp = client.put("/api/v1/hub/topic", json={"topic": "New Topic"}, headers=op_header)
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["topic"] == "New Topic"

    def test_broadcast(self, client, op_header, mock_ctx):
        resp = client.post(
            "/api/v1/hub/broadcast",
            json={"message": "Hello everyone!"},
            headers=op_header,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_ctx.send_to_all.assert_called_once_with("Hello everyone!")

    def test_broadcast_to_class(self, client, op_header, mock_ctx):
        resp = client.post(
            "/api/v1/hub/broadcast",
            json={"message": "VIP msg", "min_class": 2, "max_class": 5},
            headers=op_header,
        )
        assert resp.status_code == 200
        mock_ctx.send_to_class.assert_called_once_with("VIP msg", 2, 5)

    def test_shutdown(self, client, master_header, mock_ctx):
        resp = client.post("/api/v1/hub/shutdown", headers=master_header)
        assert resp.status_code == 200
        mock_ctx.request_shutdown.assert_called_once_with(0)

    def test_reload(self, client, admin_header, mock_ctx):
        resp = client.post("/api/v1/hub/reload", headers=admin_header)
        assert resp.status_code == 200
        mock_ctx.cpp.RequestReload.assert_called_once()


# =============================================================================
# Stats Router — with mock context
# =============================================================================


class TestStatsWithContext:
    """Test stats endpoints that return real data from mock context."""

    def test_statistics(self, client):
        resp = client.get("/api/v1/stats/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["users_online"] == 3
        assert data["hub_name"] == "TestHub"
        assert data["total_share"] == 1073741824
        assert "total_share_formatted" in data
        assert data["uptime_seconds"] >= 0
        assert "uptime_formatted" in data
        assert data["operators_online"] == 1  # OpUser (class 5)
        assert data["bots_online"] == 1  # HubSec

    def test_geo_distribution(self, client):
        resp = client.get("/api/v1/stats/geo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_countries"] == 2  # US (Alice + OpUser), DE (Bob)
        dist = data["distribution"]
        assert len(dist) == 2
        # US has 2 users, should be first
        assert dist[0]["country_code"] == "US"
        assert dist[0]["users"] == 2
        assert dist[1]["country_code"] == "DE"
        assert dist[1]["users"] == 1

    def test_share_stats(self, client):
        resp = client.get("/api/v1/stats/share")
        assert resp.status_code == 200
        data = resp.json()
        total = 1048576 + 2097152 + 5242880
        assert data["total"] == total
        assert data["min"] == 1048576  # Alice
        assert data["max"] == 5242880  # OpUser
        assert "total_formatted" in data
        assert "average_formatted" in data

    def test_share_stats_empty(self, client, app, mock_ctx):
        """Test share stats when no users are online."""
        mock_ctx.get_user_nicks.return_value = []
        resp = client.get("/api/v1/stats/share")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["average"] == 0

    def test_operators_list(self, client):
        resp = client.get("/api/v1/stats/ops")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["nick"] == "OpUser"
        assert data[0]["user_class"] == 5
        assert data[0]["class_name"] == "Admin"

    def test_bots_list(self, client):
        resp = client.get("/api/v1/stats/bots")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["nick"] == "HubSec"
        assert data[0]["description"] == "HubSec bot"

    def test_health_check(self, client):
        resp = client.get("/api/v1/stats/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["hub_running"] is True
        assert data["uptime_seconds"] >= 0

    def test_detailed_users(self, client):
        resp = client.get("/api/v1/stats/users/detailed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        nicks = {u["nick"] for u in data}
        assert nicks == {"Alice", "Bob", "OpUser"}
        # Check fields are populated
        alice = next(u for u in data if u["nick"] == "Alice")
        assert alice["ip"] == "10.0.0.1"
        assert alice["country_code"] == "US"
        assert alice["share"] == 1048576
        assert "share_formatted" in alice

    def test_detailed_users_pagination(self, client):
        resp = client.get("/api/v1/stats/users/detailed?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_detailed_users_offset_only(self, client):
        resp = client.get("/api/v1/stats/users/detailed?offset=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_detailed_users_clone_detection(self, client, mock_ctx):
        """Test clone detection when two users share same IP and share."""
        mock_ctx.get_user_nicks.return_value = ["Clone1", "Clone2", "Normal"]

        classes = {"Clone1": 1, "Clone2": 1, "Normal": 1}
        ips = {"Clone1": "1.2.3.4", "Clone2": "1.2.3.4", "Normal": "5.6.7.8"}
        shares = {"Clone1": 1000, "Clone2": 1000, "Normal": 2000}

        mock_ctx.get_user_class = lambda nick: classes.get(nick, 0)
        mock_ctx.get_user_ip = lambda nick: ips.get(nick, "")
        mock_ctx.get_user_share = lambda nick: shares.get(nick, 0)
        mock_ctx.get_user_cc = lambda nick: ""
        mock_ctx.get_user_host = lambda nick: ""
        mock_ctx.get_user_geo = lambda nick: {"country": "", "city": "", "region": "", "asn": ""}
        mock_ctx.get_user_myinfo = lambda nick: {"description": "", "tag": "", "email": ""}

        resp = client.get("/api/v1/stats/users/detailed")
        assert resp.status_code == 200
        data = resp.json()

        clone1 = next(u for u in data if u["nick"] == "Clone1")
        assert clone1["is_clone"] is True
        assert "Clone2" in clone1["clone_group"]
        assert "Clone2" in clone1["same_ip_users"]

        normal = next(u for u in data if u["nick"] == "Normal")
        assert normal["is_clone"] is False


# =============================================================================
# Stats Utility Functions
# =============================================================================


class TestStatsUtilFunctions:
    """Test utility functions in stats module."""

    def test_format_bytes(self):
        from verlihub.api.routes.stats import format_bytes
        assert format_bytes(0) == "0.00 B"
        assert format_bytes(1024) == "1.00 KB"
        assert format_bytes(1048576) == "1.00 MB"
        assert format_bytes(1073741824) == "1.00 GB"
        assert format_bytes(1099511627776) == "1.00 TB"

    def test_format_uptime(self):
        from verlihub.api.routes.stats import format_uptime
        assert format_uptime(0) == "0s"
        assert format_uptime(61) == "1m 1s"
        assert format_uptime(3661) == "1h 1m 1s"
        assert format_uptime(90061) == "1d 1h 1m 1s"

    def test_get_class_name(self):
        from verlihub.api.routes.stats import get_class_name
        assert get_class_name(0) == "Guest"
        assert get_class_name(1) == "Regular"
        assert get_class_name(3) == "Operator"
        assert get_class_name(5) == "Admin"
        assert get_class_name(10) == "Master"
        assert get_class_name(99) == "Class99"

    def test_get_country_name(self):
        from verlihub.api.routes.stats import get_country_name
        assert get_country_name("US") == "United States"
        assert get_country_name("us") == "United States"
        assert get_country_name("DE") == "Germany"
        assert get_country_name("XX") == "XX"  # Unknown code

    def test_set_hub_start_time(self):
        from verlihub.api.routes.stats import set_hub_start_time, get_hub_start_time
        import time
        ts = time.time() - 100
        set_hub_start_time(ts)
        assert get_hub_start_time() == ts


# =============================================================================
# Hub Utility Functions
# =============================================================================


class TestHubUtilFunctions:
    """Test utility functions in hub module."""

    def test_format_uptime(self):
        from verlihub.api.routes.hub import format_uptime
        assert format_uptime(0) == "0s"
        assert format_uptime(90061) == "1d 1h 1m 1s"


# =============================================================================
# Users Router — with mock context
# =============================================================================


class TestUsersWithContext:
    """Test user endpoints with mock hub context."""

    def test_online_users(self, client):
        resp = client.get("/api/v1/users/online")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        nicks = [u["nick"] for u in data["users"]]
        assert "Alice" in nicks

    def test_get_online_user_found(self, client):
        resp = client.get("/api/v1/users/online/Alice")
        assert resp.status_code == 200
        assert resp.json()["nick"] == "Alice"

    def test_get_online_user_not_found(self, client):
        resp = client.get("/api/v1/users/online/Ghost")
        assert resp.status_code == 404

    def test_kick_user(self, client, op_header, mock_ctx):
        resp = client.post(
            "/api/v1/users/kick",
            json={"nick": "Alice", "reason": "test"},
            headers=op_header,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_ctx.kick_user.assert_called_once()

    def test_kick_user_fails(self, client, op_header, mock_ctx):
        mock_ctx.kick_user.return_value = False
        resp = client.post(
            "/api/v1/users/kick",
            json={"nick": "Ghost", "reason": "test"},
            headers=op_header,
        )
        assert resp.status_code == 404

    def test_send_message(self, client, op_header, mock_ctx):
        resp = client.post(
            "/api/v1/users/message",
            json={"nick": "Alice", "message": "Hello!"},
            headers=op_header,
        )
        assert resp.status_code == 200
        mock_ctx.send_to_user.assert_called_once_with("Alice", "Hello!")

    def test_send_message_user_not_found(self, client, op_header, mock_ctx):
        mock_ctx.send_to_user.return_value = False
        resp = client.post(
            "/api/v1/users/message",
            json={"nick": "Ghost", "message": "Hello!"},
            headers=op_header,
        )
        assert resp.status_code == 404


# =============================================================================
# Auth Router — /me with different permission levels
# =============================================================================


class TestAuthMePermissions:
    """Test /auth/me returns correct permissions for different user classes."""

    def _get_me(self, client, user_class, nick="testuser"):
        token = create_access_token(nick, user_class)
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        return resp

    def test_me_user_permissions(self, client):
        resp = self._get_me(client, Permission.USER)
        assert resp.status_code == 200
        data = resp.json()
        assert "user" in data["permissions"]
        assert "operator" not in data["permissions"]

    def test_me_operator_permissions(self, client):
        resp = self._get_me(client, Permission.OPERATOR)
        data = resp.json()
        assert "operator" in data["permissions"]
        assert "user" in data["permissions"]
        assert "vip" in data["permissions"]

    def test_me_admin_permissions(self, client):
        resp = self._get_me(client, Permission.ADMIN)
        data = resp.json()
        assert "admin" in data["permissions"]

    def test_me_master_permissions(self, client):
        resp = self._get_me(client, Permission.MASTER)
        data = resp.json()
        assert "master" in data["permissions"]


# =============================================================================
# Auth Router — /logout and /refresh
# =============================================================================


class TestAuthLogoutRefresh:
    """Test logout and token refresh."""

    def test_logout_returns_success(self, client, user_header):
        resp = client.post("/api/v1/auth/logout", headers=user_header)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_refresh_returns_new_token(self, client, user_header):
        resp = client.post("/api/v1/auth/refresh", headers=user_header)
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0


# =============================================================================
# App-level tests (create_app, health check)
# =============================================================================


class TestAppLevel:
    """Tests for app-level functionality."""

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_openapi_schema(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["info"]["title"] == "Thin Verlihub"

    def test_create_app_cors(self):
        """Test CORS middleware is configured."""
        import os
        with mock.patch.dict(os.environ, {"VH_CORS_ORIGINS": "http://localhost:3000,http://example.com"}):
            from verlihub.api.app import create_app
            test_app = create_app()
            # Just verify app was created without error
            assert test_app is not None


# =============================================================================
# Deps module
# =============================================================================


class TestDepsModule:
    """Tests for the deps module."""

    def test_set_and_get_hub_context(self):
        from verlihub.api.deps import get_hub_context, set_hub_context
        ctx = mock.MagicMock()
        original = get_hub_context()
        try:
            set_hub_context(ctx)
            assert get_hub_context() is ctx
            set_hub_context(None)
            assert get_hub_context() is None
        finally:
            set_hub_context(original)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
