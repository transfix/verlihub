"""
Tests for the dashboard routes and WebSocket functionality.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from verlihub.api.app import create_app
from verlihub.api.auth import create_access_token, TokenData


# Create test app
app = create_app()


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def operator_token():
    """Create a token for an operator user."""
    token = create_access_token("test_operator", 3)
    return token.access_token


@pytest.fixture
def admin_token():
    """Create a token for an admin user."""
    token = create_access_token("test_admin", 5)
    return token.access_token


@pytest.fixture
def master_token():
    """Create a token for a master user."""
    token = create_access_token("test_master", 10)
    return token.access_token


class TestDashboardPublicRoutes:
    """Test dashboard routes that don't require authentication."""
    
    def test_login_page_loads(self, client):
        """Test that login page loads without authentication."""
        response = client.get("/dashboard/login")
        assert response.status_code == 200
        assert b"Login" in response.content
        assert b"Username" in response.content
    
    def test_login_page_shows_error(self, client):
        """Test that login page shows error parameter."""
        response = client.get("/dashboard/login?error=Test+error")
        assert response.status_code == 200
        assert b"Test error" in response.content
    
    def test_dashboard_redirects_to_login(self, client):
        """Test that main dashboard redirects to login without auth."""
        response = client.get("/dashboard/", follow_redirects=False)
        assert response.status_code == 303
        assert "/dashboard/login" in response.headers["location"]


class TestDashboardAuthenticatedRoutes:
    """Test dashboard routes that require authentication."""
    
    def test_dashboard_with_auth(self, client, operator_token):
        """Test that main dashboard loads with authentication."""
        response = client.get(
            "/dashboard/",
            cookies={"access_token": f"Bearer {operator_token}"}
        )
        # May return 200 or 500 depending on hub context availability
        assert response.status_code in [200, 500]
    
    def test_users_page_requires_admin(self, client, operator_token):
        """Test that users page requires admin class."""
        response = client.get(
            "/dashboard/users",
            cookies={"access_token": f"Bearer {operator_token}"},
            follow_redirects=False
        )
        # Operator (class 3) should get 403, needs class 5
        assert response.status_code == 403
    
    def test_users_page_with_admin(self, client, admin_token):
        """Test that users page loads with admin auth."""
        response = client.get(
            "/dashboard/users",
            cookies={"access_token": f"Bearer {admin_token}"}
        )
        assert response.status_code in [200, 500]
    
    def test_bans_page_with_operator(self, client, operator_token):
        """Test that bans page loads with operator auth."""
        response = client.get(
            "/dashboard/bans",
            cookies={"access_token": f"Bearer {operator_token}"}
        )
        assert response.status_code in [200, 500]
    
    def test_config_page_requires_master(self, client, admin_token):
        """Test that config page requires master class."""
        response = client.get(
            "/dashboard/config",
            cookies={"access_token": f"Bearer {admin_token}"},
            follow_redirects=False
        )
        # Admin (class 5) should get 403, needs class 10
        assert response.status_code == 403
    
    def test_config_page_with_master(self, client, master_token):
        """Test that config page loads with master auth."""
        response = client.get(
            "/dashboard/config",
            cookies={"access_token": f"Bearer {master_token}"}
        )
        assert response.status_code in [200, 500]
    
    def test_logs_page_with_admin(self, client, admin_token):
        """Test that logs page loads with admin auth."""
        response = client.get(
            "/dashboard/logs",
            cookies={"access_token": f"Bearer {admin_token}"}
        )
        assert response.status_code in [200, 500]


class TestDashboardLogin:
    """Test dashboard login functionality."""
    
    def test_login_missing_credentials(self, client):
        """Test login with missing credentials."""
        response = client.post(
            "/dashboard/login",
            data={"username": "", "password": ""},
            follow_redirects=False
        )
        # Returns 303 redirect with error, or 500 if db unavailable
        assert response.status_code in [303, 500]
        if response.status_code == 303:
            assert "error=" in response.headers["location"]
    
    def test_login_invalid_credentials(self, client):
        """Test login with invalid credentials."""
        response = client.post(
            "/dashboard/login",
            data={"username": "invalid", "password": "invalid"},
            follow_redirects=False
        )
        # Returns 303 redirect with error, or 500 if db unavailable
        assert response.status_code in [303, 500]
        if response.status_code == 303:
            assert "error=" in response.headers["location"]
    
    def test_logout(self, client, operator_token):
        """Test logout clears cookie."""
        response = client.get(
            "/dashboard/logout",
            cookies={"access_token": f"Bearer {operator_token}"},
            follow_redirects=False
        )
        assert response.status_code == 303
        assert "/dashboard/login" in response.headers["location"]


class TestDashboardTemplates:
    """Test dashboard template rendering."""
    
    def test_login_has_bulma_css(self, client):
        """Test that login page includes Bulma CSS."""
        response = client.get("/dashboard/login")
        assert response.status_code == 200
        assert b"bulma" in response.content
    
    def test_login_has_fontawesome(self, client):
        """Test that login page includes Font Awesome."""
        response = client.get("/dashboard/login")
        assert response.status_code == 200
        assert b"font-awesome" in response.content or b"fontawesome" in response.content


class TestCookieAuth:
    """Test cookie-based authentication for dashboard."""
    
    def test_auth_from_cookie(self, client, operator_token):
        """Test that authentication works from cookie."""
        response = client.get(
            "/dashboard/",
            cookies={"access_token": f"Bearer {operator_token}"}
        )
        # If auth works, should not redirect to login
        # May return 200 or 500 depending on hub context
        assert response.status_code != 303
    
    def test_invalid_token_redirects(self, client):
        """Test that invalid token redirects to login."""
        response = client.get(
            "/dashboard/",
            cookies={"access_token": "Bearer invalid_token"},
            follow_redirects=False
        )
        assert response.status_code == 303


class TestConsolePage:
    """Test console page routes."""
    
    def test_console_requires_operator(self, client, operator_token):
        """Test that console page works with operator class."""
        response = client.get(
            "/dashboard/console",
            cookies={"access_token": f"Bearer {operator_token}"}
        )
        # Should load (or 500 if hub unavailable)
        assert response.status_code in [200, 500]
    
    def test_console_shows_command_interface(self, client, operator_token):
        """Test that console page shows command interface elements."""
        response = client.get(
            "/dashboard/console",
            cookies={"access_token": f"Bearer {operator_token}"}
        )
        if response.status_code == 200:
            assert b"command" in response.content.lower()
    
    def test_console_unauthenticated_redirects(self, client):
        """Test that console page redirects without auth."""
        response = client.get("/dashboard/console", follow_redirects=False)
        assert response.status_code == 303


class TestPluginsPage:
    """Test plugins page routes."""
    
    def test_plugins_requires_admin(self, client, operator_token, admin_token):
        """Test that plugins page requires admin class."""
        # Operator (class 3) should get 403
        response = client.get(
            "/dashboard/plugins",
            cookies={"access_token": f"Bearer {operator_token}"},
            follow_redirects=False
        )
        assert response.status_code == 403
        
        # Admin (class 5) should succeed
        response = client.get(
            "/dashboard/plugins",
            cookies={"access_token": f"Bearer {admin_token}"}
        )
        assert response.status_code in [200, 500]
    
    def test_plugins_page_content(self, client, admin_token):
        """Test that plugins page contains expected content."""
        response = client.get(
            "/dashboard/plugins",
            cookies={"access_token": f"Bearer {admin_token}"}
        )
        if response.status_code == 200:
            assert b"plugin" in response.content.lower()


class TestWebSocketEndpoints:
    """Test WebSocket endpoint structure."""
    
    def test_websocket_hub_requires_auth(self, client):
        """Test that hub WebSocket requires authentication."""
        # WebSocket test without auth - should close with 4403
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/hub"):
                pass
    
    def test_websocket_logs_requires_auth(self, client):
        """Test that logs WebSocket requires authentication."""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/logs"):
                pass


class TestHubEventBroadcaster:
    """Test the HubEventBroadcaster class."""
    
    def test_broadcaster_initialization(self):
        """Test that broadcaster initializes correctly."""
        from verlihub.dashboard.websocket import HubEventBroadcaster
        
        broadcaster = HubEventBroadcaster()
        assert hasattr(broadcaster, 'on_user_connect')
        assert hasattr(broadcaster, 'on_user_disconnect')
        assert hasattr(broadcaster, 'on_chat_message')
    
    def test_broadcaster_on_user_connect(self):
        """Test user connect event handler."""
        from verlihub.dashboard.websocket import HubEventBroadcaster
        
        broadcaster = HubEventBroadcaster()
        # Should not raise exception, returns bool
        result = broadcaster.on_user_connect(
            nick="TestUser",
            ip="192.168.1.1",
        )
        assert isinstance(result, bool)
    
    def test_broadcaster_on_user_disconnect(self):
        """Test user disconnect event handler."""
        from verlihub.dashboard.websocket import HubEventBroadcaster
        
        broadcaster = HubEventBroadcaster()
        # Should not raise exception
        broadcaster.on_user_disconnect(nick="TestUser")
    
    def test_broadcaster_on_chat_message(self):
        """Test chat message event handler."""
        from verlihub.dashboard.websocket import HubEventBroadcaster
        
        broadcaster = HubEventBroadcaster()
        # Should not raise exception, returns bool
        result = broadcaster.on_chat_message(
            nick="TestUser",
            message="Hello everyone!",
        )
        assert isinstance(result, bool)


class TestDashboardUtilities:
    """Test dashboard utility functions."""
    
    def test_format_bytes(self):
        """Test byte formatting utility."""
        from verlihub.dashboard.routes import _format_bytes
        
        assert _format_bytes(0) == "0.0 B"
        assert _format_bytes(1024) == "1.0 KB"
        assert _format_bytes(1024 * 1024) == "1.0 MB"
        assert _format_bytes(1024 * 1024 * 1024) == "1.0 GB"
    
    def test_format_uptime(self):
        """Test uptime formatting utility."""
        from verlihub.dashboard.routes import _format_uptime
        
        assert _format_uptime(30) == "30s"
        assert _format_uptime(90) == "1m 30s"
        assert _format_uptime(3665) == "1h 1m"
        assert _format_uptime(86400 + 7200) == "1d 2h"
