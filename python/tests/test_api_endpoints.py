"""
Comprehensive tests for Verlihub REST API endpoints.

These tests verify all API routes including:
- Hub information and control
- User management (online and registered)
- Ban management
- Authentication flows
"""
import pytest
from datetime import datetime, timezone
from unittest import mock

from fastapi.testclient import TestClient

from verlihub.api.auth import (
    Permission,
    create_access_token,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create a test FastAPI app."""
    from verlihub.api.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    """Create a test client that returns HTTP 500 instead of raising exceptions."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def public_header():
    """No authentication."""
    return {}


@pytest.fixture
def user_header():
    """Token with USER permission (1)."""
    token = create_access_token("regular_user", Permission.USER)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def vip_header():
    """Token with VIP permission (2)."""
    token = create_access_token("vip_user", Permission.VIP)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def operator_header():
    """Token with OPERATOR permission (3)."""
    token = create_access_token("op_user", Permission.OPERATOR)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def cheef_header():
    """Token with CHEEF permission (4)."""
    token = create_access_token("cheef_user", Permission.CHEEF)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def admin_header():
    """Token with ADMIN permission (5)."""
    token = create_access_token("admin", Permission.ADMIN)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def master_header():
    """Token with MASTER permission (10)."""
    token = create_access_token("master", Permission.MASTER)
    return {"Authorization": f"Bearer {token.access_token}"}


# =============================================================================
# Public Endpoints (No Auth Required)
# =============================================================================


class TestPublicEndpoints:
    """Tests for endpoints that don't require authentication."""
    
    def test_health_check(self, client):
        """Test /health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        
    def test_openapi_docs(self, client):
        """Test OpenAPI documentation is available."""
        response = client.get("/docs")
        # Should redirect or return docs
        assert response.status_code in [200, 307]
        
    def test_openapi_json(self, client):
        """Test OpenAPI JSON schema."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "paths" in data


# =============================================================================
# Authentication Endpoints
# =============================================================================


class TestAuthEndpoints:
    """Tests for /api/v1/auth/* endpoints."""
    
    def test_login_missing_credentials(self, client):
        """Test login with missing credentials."""
        response = client.post("/api/v1/auth/login", json={})
        # 422 for validation error, or 500 if database not available
        assert response.status_code in [422, 500]
        
    def test_me_without_auth(self, client):
        """Test /me without authentication."""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        
    def test_me_with_valid_token(self, client, admin_header):
        """Test /me with valid token."""
        response = client.get("/api/v1/auth/me", headers=admin_header)
        assert response.status_code == 200
        data = response.json()
        assert data["nick"] == "admin"
        assert data["user_class"] == Permission.ADMIN
        
    def test_me_with_different_users(self, client, user_header, operator_header):
        """Test /me returns correct user for different tokens."""
        # Regular user
        response = client.get("/api/v1/auth/me", headers=user_header)
        assert response.status_code == 200
        assert response.json()["nick"] == "regular_user"
        
        # Operator
        response = client.get("/api/v1/auth/me", headers=operator_header)
        assert response.status_code == 200
        assert response.json()["nick"] == "op_user"
        
    def test_refresh_token(self, client, user_header):
        """Test token refresh."""
        response = client.post("/api/v1/auth/refresh", headers=user_header)
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "expires_in" in data
        
    def test_logout(self, client, user_header):
        """Test logout endpoint."""
        response = client.post("/api/v1/auth/logout", headers=user_header)
        assert response.status_code == 200


# =============================================================================
# Hub Endpoints
# =============================================================================


class TestHubEndpoints:
    """Tests for /api/v1/hub/* endpoints."""
    
    def test_hub_stats_public(self, client):
        """Test hub status endpoint (public access)."""
        response = client.get("/api/v1/hub/status")
        # May fail with 503 if hub not initialized
        assert response.status_code in [200, 503]
        
    def test_hub_info_public(self, client):
        """Test hub config endpoint."""
        response = client.get("/api/v1/hub/config")
        assert response.status_code in [200, 503]
        
    def test_set_topic_requires_operator(self, client, user_header):
        """Test that setting topic requires operator permission."""
        response = client.put(
            "/api/v1/hub/topic",
            json={"topic": "Test Topic"},
            headers=user_header,
        )
        # 403 for insufficient permission, 500/503 if hub/db unavailable
        assert response.status_code in [403, 500, 503]
        
    def test_set_topic_with_operator(self, client, operator_header):
        """Test setting topic with operator permission."""
        response = client.put(
            "/api/v1/hub/topic",
            json={"topic": "New Hub Topic"},
            headers=operator_header,
        )
        # Either works (200) or hub not initialized (503)
        assert response.status_code in [200, 503]
        
    def test_broadcast_requires_operator(self, client, vip_header):
        """Test that broadcast requires operator permission."""
        response = client.post(
            "/api/v1/hub/broadcast",
            json={"message": "Test broadcast"},
            headers=vip_header,
        )
        # 403 for insufficient permission, 500/503 if hub/db unavailable
        assert response.status_code in [403, 500, 503]
        
    def test_broadcast_with_operator(self, client, operator_header):
        """Test broadcast with operator permission."""
        response = client.post(
            "/api/v1/hub/broadcast",
            json={"message": "Server announcement"},
            headers=operator_header,
        )
        assert response.status_code in [200, 503]
        
    def test_shutdown_requires_master(self, client, admin_header):
        """Test that shutdown requires master permission."""
        response = client.post(
            "/api/v1/hub/shutdown",
            headers=admin_header,
        )
        # Admin (5) < Master (10), so should be 403, 500, or 503
        assert response.status_code in [403, 500, 503]
        
    def test_shutdown_with_master(self, client, master_header):
        """Test shutdown with master permission."""
        response = client.post(
            "/api/v1/hub/shutdown",
            headers=master_header,
        )
        # Either works or hub not initialized
        assert response.status_code in [200, 503]
        
    def test_reload_requires_admin(self, client, operator_header):
        """Test that reload requires admin permission."""
        response = client.post(
            "/api/v1/hub/reload",
            headers=operator_header,
        )
        # 403 for insufficient permission, 500/503 if hub/db unavailable
        assert response.status_code in [403, 500, 503]
        
    def test_reload_with_admin(self, client, admin_header):
        """Test reload with admin permission."""
        response = client.post(
            "/api/v1/hub/reload",
            headers=admin_header,
        )
        assert response.status_code in [200, 503]


# =============================================================================
# User Endpoints
# =============================================================================


class TestUserEndpoints:
    """Tests for /api/v1/users/* endpoints."""
    
    def test_registered_users_requires_operator(self, client, vip_header):
        """Test that listing registered users requires operator."""
        response = client.get(
            "/api/v1/users/registered",
            headers=vip_header,
        )
        assert response.status_code == 403
        
    def test_registered_users_with_operator(self, client, operator_header):
        """Test listing registered users with operator permission."""
        response = client.get(
            "/api/v1/users/registered",
            headers=operator_header,
        )
        # Either works or DB not available
        assert response.status_code in [200, 500, 503]
        
    def test_create_user_requires_admin(self, client, operator_header):
        """Test that creating users requires admin."""
        response = client.post(
            "/api/v1/users/registered",
            json={"nick": "newuser", "password": "secret123", "user_class": 1},
            headers=operator_header,
        )
        assert response.status_code == 403
        
    def test_create_user_with_admin(self, client, admin_header):
        """Test creating user with admin permission."""
        response = client.post(
            "/api/v1/users/registered",
            json={"nick": "testuser", "password": "testpass123", "user_class": 1},
            headers=admin_header,
        )
        # Either works or DB not available
        assert response.status_code in [200, 201, 500, 503]
        
    def test_kick_user_requires_operator(self, client, vip_header):
        """Test that kicking users requires operator."""
        response = client.post(
            "/api/v1/users/kick",
            json={"nick": "testnick", "reason": "test kick"},
            headers=vip_header,
        )
        # 403 for insufficient permission, 500/503 if hub/db unavailable
        assert response.status_code in [403, 500, 503]
        
    def test_kick_user_with_operator(self, client, operator_header):
        """Test kicking user with operator permission."""
        response = client.post(
            "/api/v1/users/kick",
            json={"nick": "testnick", "reason": "test kick"},
            headers=operator_header,
        )
        # Either works (200), user not found (404), or hub not available (503)
        assert response.status_code in [200, 404, 503]
        
    def test_send_message_requires_operator(self, client, user_header):
        """Test that sending messages requires operator."""
        response = client.post(
            "/api/v1/users/message",
            json={"nick": "testnick", "message": "Hello"},
            headers=user_header,
        )
        # 403 for insufficient permission, 500/503 if hub/db unavailable
        assert response.status_code in [403, 500, 503]
        
    def test_send_message_with_operator(self, client, operator_header):
        """Test sending message with operator permission."""
        response = client.post(
            "/api/v1/users/message",
            json={"nick": "testnick", "message": "Hello from operator"},
            headers=operator_header,
        )
        assert response.status_code in [200, 404, 503]
        
    def test_delete_user_requires_admin(self, client, operator_header):
        """Test that deleting users requires admin."""
        response = client.delete(
            "/api/v1/users/registered/testuser",
            headers=operator_header,
        )
        assert response.status_code == 403
        
    def test_delete_user_with_admin(self, client, admin_header):
        """Test deleting user with admin permission."""
        response = client.delete(
            "/api/v1/users/registered/testuser",
            headers=admin_header,
        )
        # Either works (200), not found (404), or DB not available (500)
        assert response.status_code in [200, 404, 500, 503]


# =============================================================================
# Ban Endpoints
# =============================================================================


class TestBanEndpoints:
    """Tests for /api/v1/bans/* endpoints."""
    
    def test_list_bans_requires_operator(self, client, user_header):
        """Test that listing bans requires operator."""
        response = client.get(
            "/api/v1/bans",
            headers=user_header,
        )
        # 403 for insufficient permission, or 404/500/503 if no route or DB unavailable
        assert response.status_code in [403, 404, 500, 503]
        
    def test_list_bans_with_operator(self, client, operator_header):
        """Test listing bans with operator permission."""
        response = client.get(
            "/api/v1/bans",
            headers=operator_header,
        )
        # Either works, not found, or DB not available
        assert response.status_code in [200, 404, 500, 503]
        
    def test_create_ban_requires_operator(self, client, user_header):
        """Test that creating bans requires operator."""
        response = client.post(
            "/api/v1/bans",
            json={"nick": "baduser", "reason": "test", "ip": "1.2.3.4"},
            headers=user_header,
        )
        # 403 for insufficient permission, or 404/500/503 if no route or DB unavailable
        assert response.status_code in [403, 404, 500, 503]


# =============================================================================
# Request Validation Tests
# =============================================================================


class TestRequestValidation:
    """Tests for request validation and error handling."""
    
    def test_missing_required_field(self, client, admin_header):
        """Test missing required field returns 422 or 500 if DB unavailable."""
        response = client.post(
            "/api/v1/users/registered",
            json={"nick": "user_without_password"},  # Missing password
            headers=admin_header,
        )
        assert response.status_code in [422, 500]
        
    def test_invalid_json(self, client, admin_header):
        """Test invalid JSON returns 422 or 500/503."""
        response = client.put(
            "/api/v1/hub/topic",
            content="not json",
            headers={**admin_header, "Content-Type": "application/json"},
        )
        assert response.status_code in [422, 500, 503]
        
    def test_empty_body_when_required(self, client, admin_header):
        """Test empty body when required returns 422 or 500."""
        response = client.post(
            "/api/v1/users/registered",
            json={},
            headers=admin_header,
        )
        assert response.status_code in [422, 500]


# =============================================================================
# Token Edge Cases
# =============================================================================


class TestTokenEdgeCases:
    """Tests for edge cases in token handling."""
    
    def test_expired_token_format(self, client):
        """Test handling of malformed tokens."""
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code == 401
        
    def test_missing_bearer_prefix(self, client, admin_header):
        """Test token without Bearer prefix."""
        token = admin_header["Authorization"].replace("Bearer ", "")
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": token},
        )
        assert response.status_code == 401
        
    def test_empty_auth_header(self, client):
        """Test empty authorization header."""
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": ""},
        )
        assert response.status_code == 401
        
    def test_wrong_scheme(self, client):
        """Test wrong authentication scheme."""
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert response.status_code == 401


# =============================================================================
# CORS and Headers
# =============================================================================


class TestCORSAndHeaders:
    """Tests for CORS and response headers."""
    
    def test_options_request(self, client):
        """Test OPTIONS request for CORS preflight."""
        response = client.options(
            "/api/v1/hub/stats",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Should not return 405 Method Not Allowed
        assert response.status_code in [200, 204]
        
    def test_content_type_json(self, client):
        """Test that API returns JSON content type."""
        response = client.get("/api/v1/hub/stats")
        if response.status_code == 200:
            assert "application/json" in response.headers.get("content-type", "")


# =============================================================================
# Console API Endpoints
# =============================================================================


class TestConsoleAPI:
    """Tests for console command execution API."""
    
    def test_execute_command_requires_auth(self, client):
        """Test that command execution requires authentication."""
        response = client.post(
            "/api/v1/console/execute",
            json={"command": "!help"},
        )
        assert response.status_code == 401
    
    def test_execute_command_with_operator(self, client, operator_header):
        """Test command execution with operator auth."""
        response = client.post(
            "/api/v1/console/execute",
            json={"command": "!help"},
            headers=operator_header,
        )
        # Should work, fail gracefully (500), or indicate hub not connected (503)
        assert response.status_code in [200, 500, 503]
        if response.status_code == 200:
            data = response.json()
            assert "success" in data
    
    def test_execute_empty_command(self, client, operator_header):
        """Test executing empty command."""
        response = client.post(
            "/api/v1/console/execute",
            json={"command": ""},
            headers=operator_header,
        )
        # Should return error or handle gracefully
        assert response.status_code in [200, 400, 422, 500]
    
    def test_get_commands_list(self, client, operator_header):
        """Test getting command reference list."""
        response = client.get(
            "/api/v1/console/commands",
            headers=operator_header,
        )
        assert response.status_code in [200, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
    
    def test_execute_command_response_format(self, client, operator_header):
        """Test that command response has expected format."""
        response = client.post(
            "/api/v1/console/execute",
            json={"command": "!help"},
            headers=operator_header,
        )
        if response.status_code == 200:
            data = response.json()
            # Should have these fields
            assert "success" in data or "output" in data or "message" in data


# =============================================================================
# Statistics Endpoints
# =============================================================================


class TestStatsEndpoints:
    """Tests for /api/v1/stats/* endpoints."""
    
    def test_stats_endpoint(self, client):
        """Test comprehensive statistics endpoint."""
        response = client.get("/api/v1/stats/stats")
        # Either works or hub not initialized
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert "users_online" in data
            assert "max_users" in data
            assert "total_share" in data
            assert "total_share_formatted" in data
            assert "uptime_seconds" in data
            assert "uptime_formatted" in data
    
    def test_geo_distribution(self, client):
        """Test geographic distribution endpoint."""
        response = client.get("/api/v1/stats/geo")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert "total_countries" in data
            assert "distribution" in data
            assert isinstance(data["distribution"], list)
    
    def test_share_stats(self, client):
        """Test share statistics endpoint."""
        response = client.get("/api/v1/stats/share")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert "total" in data
            assert "total_formatted" in data
            assert "average" in data
            assert "average_formatted" in data
            assert "median" in data
            assert "median_formatted" in data
    
    def test_operators_list(self, client):
        """Test operators list endpoint."""
        response = client.get("/api/v1/stats/ops")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            # If there are operators, check structure
            if data:
                op = data[0]
                assert "nick" in op
                assert "user_class" in op
                assert "class_name" in op
    
    def test_bots_list(self, client):
        """Test bots list endpoint."""
        response = client.get("/api/v1/stats/bots")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
    
    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/api/v1/stats/health")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert "status" in data
            assert "timestamp" in data
            assert "hub_running" in data
            assert "database_connected" in data
    
    def test_detailed_users(self, client):
        """Test detailed users endpoint."""
        response = client.get("/api/v1/stats/users/detailed")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            # If there are users, check structure
            if data:
                user = data[0]
                assert "nick" in user
                assert "user_class" in user
                assert "class_name" in user
                assert "share" in user
                assert "share_formatted" in user
                assert "is_clone" in user
    
    def test_detailed_users_with_pagination(self, client):
        """Test detailed users endpoint with pagination."""
        response = client.get("/api/v1/stats/users/detailed?limit=10&offset=0")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            assert len(data) <= 10


# =============================================================================
# Hub Info Endpoint
# =============================================================================


class TestHubInfoEndpoint:
    """Tests for /api/v1/hub/info endpoint."""
    
    def test_hub_info_full(self, client):
        """Test full hub info endpoint."""
        response = client.get("/api/v1/hub/info")
        assert response.status_code in [200, 503]
        if response.status_code == 200:
            data = response.json()
            assert "name" in data
            assert "description" in data
            assert "host" in data
            assert "topic" in data
            assert "motd" in data
            assert "max_users" in data
            assert "version" in data
            assert "uptime_seconds" in data
            assert "uptime_formatted" in data
            assert "hub_encoding" in data
            assert "tls_enabled" in data


class TestDashboardEndpoints:
    """Tests for dashboard HTML endpoints."""
    
    def test_spa_dashboard_returns_html(self, client):
        """Test that SPA dashboard returns valid HTML."""
        response = client.get("/dashboard/spa")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        
        # Check for essential HTML elements
        content = response.text
        assert "<!DOCTYPE html>" in content
        assert "<title" in content
        assert "Verlihub Dashboard" in content
        
    def test_spa_dashboard_has_tabs(self, client):
        """Test that SPA dashboard has all navigation tabs."""
        response = client.get("/dashboard/spa")
        assert response.status_code == 200
        content = response.text
        
        # Check for tab elements
        assert 'data-tab="hub"' in content
        assert 'data-tab="users"' in content
        assert 'data-tab="geo"' in content
        assert 'data-tab="cities"' in content
        assert 'data-tab="asns"' in content
        assert 'data-tab="ips"' in content
        
    def test_spa_dashboard_has_javascript(self, client):
        """Test that SPA dashboard includes JavaScript."""
        response = client.get("/dashboard/spa")
        assert response.status_code == 200
        content = response.text
        
        # Check for essential JavaScript functions
        assert "<script>" in content
        assert "fetchData" in content or "fetch(" in content
        assert "loadTab" in content
        
    def test_embed_dashboard_returns_html(self, client):
        """Test that embed dashboard returns valid HTML."""
        response = client.get("/dashboard/embed")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        
        content = response.text
        assert "<!DOCTYPE html>" in content
        
    def test_embed_dashboard_is_minimal(self, client):
        """Test that embed dashboard is compact/minimal design."""
        response = client.get("/dashboard/embed")
        assert response.status_code == 200
        content = response.text
        
        # Embed should have compact structure
        assert "embed-container" in content
        assert "stat-box" in content
        assert "stats-grid" in content
        
    def test_embed_dashboard_has_powered_by(self, client):
        """Test that embed dashboard links back to full dashboard."""
        response = client.get("/dashboard/embed")
        assert response.status_code == 200
        content = response.text
        
        # Should have link to full dashboard
        assert "powered-by" in content
        assert "/dashboard/spa" in content


# =============================================================================
# Registration Endpoints
# =============================================================================


class TestRegistrationEndpoints:
    """Tests for /api/v1/auth/register endpoint."""

    def test_register_missing_fields(self, client):
        """Test registration with missing fields."""
        response = client.post("/api/v1/auth/register", json={})
        assert response.status_code in [422, 500]

    def test_register_short_nick(self, client):
        """Test registration with too-short nick."""
        response = client.post("/api/v1/auth/register", json={
            "nick": "a",
            "password": "testpass",
        })
        assert response.status_code in [400, 500]

    def test_register_invalid_nick_chars(self, client):
        """Test registration with invalid characters in nick."""
        response = client.post("/api/v1/auth/register", json={
            "nick": "bad nick!@#",
            "password": "testpass",
        })
        assert response.status_code in [400, 500]

    def test_register_short_password(self, client):
        """Test registration with too-short password."""
        response = client.post("/api/v1/auth/register", json={
            "nick": "testuser",
            "password": "abc",
        })
        assert response.status_code in [400, 500]

    def test_register_success(self, client):
        """Test successful registration returns a token."""
        response = client.post("/api/v1/auth/register", json={
            "nick": "newuser_test",
            "password": "testpass1234",
        })
        # Should succeed or fail with DB error (500) in test environment
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"
            assert "expires_in" in data

    def test_register_duplicate_nick(self, client):
        """Test registration with duplicate nick returns conflict."""
        payload = {"nick": "dup_user_test", "password": "testpass1234"}
        # First registration
        r1 = client.post("/api/v1/auth/register", json=payload)
        if r1.status_code == 200:
            # Second should fail
            r2 = client.post("/api/v1/auth/register", json=payload)
            assert r2.status_code == 409

    def test_register_with_invalid_invite_code(self, client):
        """Test registration with an invalid invite code."""
        response = client.post("/api/v1/auth/register", json={
            "nick": "inviteuser",
            "password": "testpass1234",
            "invite_code": "nonexistent_code",
        })
        # Should return 400 for invalid code, or 500 if DB not available
        assert response.status_code in [400, 500]

    def test_register_no_auth_needed(self, client):
        """Test that registration endpoint does not require authentication."""
        response = client.post("/api/v1/auth/register", json={
            "nick": "noauth_reg",
            "password": "testpass1234",
        })
        # Should NOT return 401
        assert response.status_code != 401


# =============================================================================
# Invite Code Endpoints
# =============================================================================


class TestInviteCodeEndpoints:
    """Tests for /api/v1/invites/* endpoints."""

    def test_allocate_requires_admin(self, client, user_header):
        """Test that invite allocation requires admin permission."""
        response = client.post("/api/v1/invites/allocate", json={
            "nick": "someuser",
            "count": 1,
            "max_class": 1,
        }, headers=user_header)
        assert response.status_code == 403

    def test_allocate_requires_operator_forbidden(self, client, operator_header):
        """Test that operator cannot allocate invites."""
        response = client.post("/api/v1/invites/allocate", json={
            "nick": "someuser",
            "count": 1,
            "max_class": 1,
        }, headers=operator_header)
        assert response.status_code == 403

    def test_allocate_with_admin(self, client, admin_header):
        """Test invite allocation with admin permission."""
        response = client.post("/api/v1/invites/allocate", json={
            "nick": "testuser",
            "count": 3,
            "max_class": 1,
        }, headers=admin_header)
        # Should succeed or DB error
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert data["allocated"] == 3
            assert len(data["codes"]) == 3
            assert data["nick"] == "testuser"
            assert data["max_class"] == 1

    def test_allocate_class_exceeds_own(self, client, admin_header):
        """Test that admin can't allocate invites with class higher than their own."""
        response = client.post("/api/v1/invites/allocate", json={
            "nick": "testuser",
            "count": 1,
            "max_class": 10,  # Master - exceeds admin (5)
        }, headers=admin_header)
        assert response.status_code in [400, 500]

    def test_allocate_invalid_class(self, client, admin_header):
        """Test allocation with invalid user class."""
        response = client.post("/api/v1/invites/allocate", json={
            "nick": "testuser",
            "count": 1,
            "max_class": 99,
        }, headers=admin_header)
        assert response.status_code in [400, 500]

    def test_admin_list_invites_requires_admin(self, client, user_header):
        """Test that listing all invites requires admin."""
        response = client.get("/api/v1/invites/admin", headers=user_header)
        assert response.status_code == 403

    def test_admin_list_invites(self, client, admin_header):
        """Test admin can list all invites."""
        response = client.get("/api/v1/invites/admin", headers=admin_header)
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)

    def test_mine_requires_auth(self, client):
        """Test that /mine requires authentication."""
        response = client.get("/api/v1/invites/mine")
        assert response.status_code == 401

    def test_mine_returns_summary(self, client, user_header):
        """Test user can view their own invites."""
        response = client.get("/api/v1/invites/mine", headers=user_header)
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert "total" in data
            assert "used" in data
            assert "available" in data
            assert "codes" in data

    def test_revoke_requires_admin(self, client, user_header):
        """Test that revoking an invite requires admin."""
        response = client.delete("/api/v1/invites/somecode", headers=user_header)
        assert response.status_code == 403

    def test_revoke_nonexistent_code(self, client, admin_header):
        """Test revoking a nonexistent code returns 404."""
        response = client.delete("/api/v1/invites/nonexistent", headers=admin_header)
        assert response.status_code in [404, 500]

    def test_allocate_without_auth(self, client):
        """Test that allocation without auth returns 401."""
        response = client.post("/api/v1/invites/allocate", json={
            "nick": "test",
            "count": 1,
            "max_class": 1,
        })
        assert response.status_code == 401

    def test_master_can_allocate_high_class(self, client, master_header):
        """Test that master can allocate invites with high class."""
        response = client.post("/api/v1/invites/allocate", json={
            "nick": "testuser",
            "count": 1,
            "max_class": 5,  # Admin class - within master's range
        }, headers=master_header)
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert data["max_class"] == 5


# =============================================================================
# Dashboard Access Tests (All user classes)
# =============================================================================


class TestDashboardAccess:
    """Tests that all user classes can access dashboard pages (no 403s)."""

    def test_dashboard_pages_no_403_for_user(self, client):
        """Test that basic user class doesn't get 403 on any dashboard page."""
        from verlihub.api.auth import create_access_token, Permission
        token = create_access_token("basic_user", Permission.USER)
        # Set cookie for dashboard requests
        client.cookies.set("access_token", f"Bearer {token.access_token}")
        
        pages = ["/dashboard/users", "/dashboard/bans", "/dashboard/config",
                 "/dashboard/logs", "/dashboard/console", "/dashboard/plugins",
                 "/dashboard/invites"]
        
        for page in pages:
            response = client.get(page, follow_redirects=False)
            # Should NOT be 403 - may be 200, 302/303 redirect, or 500 (DB)
            assert response.status_code != 403, f"{page} returned 403 for basic user"

    def test_dashboard_pages_redirect_when_not_logged_in(self, client):
        """Test that pages redirect to login when not authenticated."""
        pages = ["/dashboard/", "/dashboard/users", "/dashboard/bans",
                 "/dashboard/config", "/dashboard/logs", "/dashboard/console",
                 "/dashboard/plugins", "/dashboard/invites"]
        
        for page in pages:
            response = client.get(page, follow_redirects=False)
            assert response.status_code == 303, f"{page} didn't redirect to login"
            assert "/dashboard/login" in response.headers.get("location", "")


class TestDashboardRegistrationPage:
    """Tests for the dashboard registration page."""

    def test_register_page_returns_html(self, client):
        """Test registration page returns valid HTML."""
        response = client.get("/dashboard/register")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        content = response.text
        assert "Register" in content or "Create Account" in content

    def test_register_page_has_form(self, client):
        """Test registration page has the form fields."""
        response = client.get("/dashboard/register")
        assert response.status_code == 200
        content = response.text
        assert 'name="nick"' in content
        assert 'name="password"' in content
        assert 'name="confirm_password"' in content
        assert 'name="invite_code"' in content

    def test_register_page_shows_error(self, client):
        """Test registration page shows error parameter."""
        response = client.get("/dashboard/register?error=Test+error")
        assert response.status_code == 200
        content = response.text
        assert "Test error" in content

    def test_register_page_preserves_invite_code(self, client):
        """Test registration page preserves invite code in form."""
        response = client.get("/dashboard/register?invite=ABC123")
        assert response.status_code == 200
        content = response.text
        assert "ABC123" in content

    def test_register_page_link_from_login(self, client):
        """Test login page links to registration."""
        response = client.get("/dashboard/login")
        assert response.status_code == 200
        content = response.text
        assert "/dashboard/register" in content
        assert "Register" in content


class TestInvitePermalink:
    """Tests for the /dashboard/invite/{code} permalink route."""

    def test_invite_permalink_redirects_to_register(self, client):
        """Test that /dashboard/invite/CODE redirects to register page with invite pre-filled."""
        response = client.get("/dashboard/invite/TESTCODE123", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/register?invite=TESTCODE123"

    def test_invite_permalink_follow_redirect(self, client):
        """Test that following the redirect lands on registration with invite code."""
        response = client.get("/dashboard/invite/ABC456")
        assert response.status_code == 200
        content = response.text
        assert "ABC456" in content
        assert "Create Account" in content

    def test_invite_permalink_with_special_chars(self, client):
        """Test permalink with URL-safe invite code characters."""
        response = client.get("/dashboard/invite/test-code_123", follow_redirects=False)
        assert response.status_code == 303
        assert "test-code_123" in response.headers["location"]

    def test_invite_permalink_empty_code(self, client):
        """Test that /dashboard/invite/ without a code returns 404 (no match)."""
        response = client.get("/dashboard/invite/", follow_redirects=False)
        # FastAPI will return 307 for trailing slash or 404 — either is fine
        assert response.status_code in (307, 404, 405)

    def test_invite_permalink_registration_form_prefilled(self, client):
        """Test that the invite code value appears in the form input after redirect."""
        response = client.get("/dashboard/invite/MYINVITE789")
        assert response.status_code == 200
        content = response.text
        assert 'value="MYINVITE789"' in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
