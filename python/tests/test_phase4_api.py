"""
Tests for Phase 4 API endpoints: ForceMove, Protocol Stats, GeoIP, WhoIP,
Flood Config, OpChat, Disconnect.

Uses FastAPI dependency_overrides to inject a mock hub context.
"""
import pytest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from verlihub.api.auth import Permission, create_access_token


@pytest.fixture
def mock_hub_ctx():
    """Create a mock hub context with Phase 4 methods."""
    ctx = MagicMock()
    ctx.is_running = True
    ctx.force_move = MagicMock(return_value=True)
    ctx.disconnect_user = MagicMock(return_value=True)
    ctx.send_to_opchat = MagicMock(return_value=True)
    ctx.get_protocol_stats = MagicMock(return_value={
        "messages_in": 1000, "messages_out": 2000,
        "chat_count": 500, "pm_count": 100,
        "search_count": 200, "myinfo_count": 50,
        "ctm_count": 80, "sr_count": 60,
        "mcto_count": 10, "flood_blocked": 5, "ban_blocked": 2,
    })
    ctx.lookup_geoip = MagicMock(return_value={
        "country_code": "US", "country_name": "United States",
        "city": "New York", "available": True,
    })
    ctx.get_user_list = MagicMock(return_value=[
        {"nick": "Alice", "ip": "192.168.1.1", "user_class": 3, "share": 1024},
        {"nick": "Bob", "ip": "192.168.1.2", "user_class": 1, "share": 512},
        {"nick": "Charlie", "ip": "192.168.1.1", "user_class": 0, "share": 0},
    ])
    ctx.get_flood_config = MagicMock(return_value=(1000, 5))
    ctx.set_flood_config = MagicMock()
    return ctx


@pytest.fixture
def app(mock_hub_ctx):
    from verlihub.api.app import create_app
    from verlihub.api.routes.hub import get_hub_context
    application = create_app()
    application.dependency_overrides[get_hub_context] = lambda: mock_hub_ctx
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def operator_header():
    token = create_access_token("op_user", Permission.OPERATOR)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def admin_header():
    token = create_access_token("admin", Permission.ADMIN)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def user_header():
    token = create_access_token("regular", Permission.USER)
    return {"Authorization": f"Bearer {token.access_token}"}


# =====================================================================
# ForceMove
# =====================================================================

class TestForceMove:
    def test_force_move_success(self, client, admin_header, mock_hub_ctx):
        resp = client.post("/api/v1/hub/force-move", json={"nick": "spammer", "address": "other.hub:411"}, headers=admin_header)
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_hub_ctx.force_move.assert_called_once_with("spammer", "other.hub:411")

    def test_force_move_not_found(self, client, admin_header, mock_hub_ctx):
        mock_hub_ctx.force_move.return_value = False
        resp = client.post("/api/v1/hub/force-move", json={"nick": "ghost", "address": "other.hub:411"}, headers=admin_header)
        assert resp.status_code == 404

    def test_force_move_requires_admin(self, client, operator_header, mock_hub_ctx):
        resp = client.post("/api/v1/hub/force-move", json={"nick": "x", "address": "y"}, headers=operator_header)
        assert resp.status_code == 403

    def test_force_move_missing_fields(self, client, admin_header, mock_hub_ctx):
        resp = client.post("/api/v1/hub/force-move", json={"nick": "", "address": ""}, headers=admin_header)
        assert resp.status_code == 400


# =====================================================================
# Protocol Stats
# =====================================================================

class TestProtocolStats:
    def test_get_stats(self, client, operator_header, mock_hub_ctx):
        resp = client.get("/api/v1/hub/protocol-stats", headers=operator_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages_in"] == 1000
        assert data["flood_blocked"] == 5

    def test_stats_requires_operator(self, client, user_header, mock_hub_ctx):
        resp = client.get("/api/v1/hub/protocol-stats", headers=user_header)
        assert resp.status_code == 403


# =====================================================================
# GeoIP Lookup
# =====================================================================

class TestGeoIPLookup:
    def test_lookup_success(self, client, operator_header, mock_hub_ctx):
        resp = client.get("/api/v1/hub/geoip/8.8.8.8", headers=operator_header)
        assert resp.status_code == 200
        assert resp.json()["country_code"] == "US"

    def test_lookup_not_available(self, client, operator_header, mock_hub_ctx):
        mock_hub_ctx.lookup_geoip.return_value = {"available": False, "country_code": "", "country_name": "", "city": ""}
        resp = client.get("/api/v1/hub/geoip/10.0.0.1", headers=operator_header)
        assert resp.status_code == 404

    def test_lookup_requires_operator(self, client, user_header, mock_hub_ctx):
        resp = client.get("/api/v1/hub/geoip/8.8.8.8", headers=user_header)
        assert resp.status_code == 403


# =====================================================================
# WhoIP
# =====================================================================

class TestWhoIP:
    def test_whoip_found(self, client, operator_header, mock_hub_ctx):
        resp = client.get("/api/v1/hub/whoip/192.168.1.1", headers=operator_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        nicks = [u["nick"] for u in data["users"]]
        assert "Alice" in nicks
        assert "Charlie" in nicks

    def test_whoip_not_found(self, client, operator_header, mock_hub_ctx):
        resp = client.get("/api/v1/hub/whoip/10.10.10.10", headers=operator_header)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# =====================================================================
# Flood Config
# =====================================================================

class TestFloodConfig:
    def test_get_all(self, client, operator_header, mock_hub_ctx):
        resp = client.get("/api/v1/hub/flood-config", headers=operator_header)
        assert resp.status_code == 200
        data = resp.json()
        assert "chat" in data
        assert data["chat"]["period_ms"] == 1000

    def test_set_config(self, client, admin_header, mock_hub_ctx):
        resp = client.put("/api/v1/hub/flood-config", json={
            "flood_type": "chat", "period_ms": 2000, "max_tokens": 10
        }, headers=admin_header)
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_hub_ctx.set_flood_config.assert_called_once_with(0, 2000, 10)

    def test_set_invalid_type(self, client, admin_header, mock_hub_ctx):
        resp = client.put("/api/v1/hub/flood-config", json={
            "flood_type": "invalid", "period_ms": 1000, "max_tokens": 5
        }, headers=admin_header)
        assert resp.status_code == 400

    def test_set_requires_admin(self, client, operator_header, mock_hub_ctx):
        resp = client.put("/api/v1/hub/flood-config", json={
            "flood_type": "chat", "period_ms": 1000, "max_tokens": 5
        }, headers=operator_header)
        assert resp.status_code == 403

    def test_set_period_too_low(self, client, admin_header, mock_hub_ctx):
        resp = client.put("/api/v1/hub/flood-config", json={
            "flood_type": "chat", "period_ms": 50, "max_tokens": 5
        }, headers=admin_header)
        assert resp.status_code == 400


# =====================================================================
# OpChat
# =====================================================================

class TestOpChat:
    def test_send(self, client, admin_header, mock_hub_ctx):
        resp = client.post("/api/v1/hub/opchat", json={"message": "Hello ops"}, headers=admin_header)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_empty_message(self, client, admin_header, mock_hub_ctx):
        resp = client.post("/api/v1/hub/opchat", json={"message": ""}, headers=admin_header)
        assert resp.status_code == 400


# =====================================================================
# Disconnect
# =====================================================================

class TestDisconnect:
    def test_disconnect_success(self, client, admin_header, mock_hub_ctx):
        resp = client.post("/api/v1/hub/disconnect", json={"nick": "baduser"}, headers=admin_header)
        assert resp.status_code == 200
        mock_hub_ctx.disconnect_user.assert_called_once_with("baduser")

    def test_disconnect_not_found(self, client, admin_header, mock_hub_ctx):
        mock_hub_ctx.disconnect_user.return_value = False
        resp = client.post("/api/v1/hub/disconnect", json={"nick": "ghost"}, headers=admin_header)
        assert resp.status_code == 404

    def test_disconnect_requires_admin(self, client, operator_header, mock_hub_ctx):
        resp = client.post("/api/v1/hub/disconnect", json={"nick": "x"}, headers=operator_header)
        assert resp.status_code == 403
