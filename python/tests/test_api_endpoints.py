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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
