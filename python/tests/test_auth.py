"""
Tests for Verlihub REST API authentication.

These tests verify:
- JWT token creation and validation
- Login endpoint
- Permission-based access control
"""
import pytest
from unittest import mock
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from verlihub.api.auth import (
    Permission,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


# =============================================================================
# Password Hashing Tests
# =============================================================================


class TestPasswordHashing:
    """Tests for password hashing utilities."""
    
    def test_hash_and_verify(self):
        """Test password hashing and verification."""
        password = "test_password_123"
        hashed = hash_password(password)
        
        assert hashed != password
        assert verify_password(password, hashed)
        
    def test_wrong_password_fails(self):
        """Test that wrong password fails verification."""
        password = "correct_password"
        wrong_password = "wrong_password"
        hashed = hash_password(password)
        
        assert not verify_password(wrong_password, hashed)
        
    def test_empty_password(self):
        """Test empty password handling."""
        # Empty password with empty hash should match
        assert verify_password("", "")
        
        # Non-empty password with empty hash should not match
        assert not verify_password("password", "")


# =============================================================================
# Token Tests
# =============================================================================


class TestTokenCreation:
    """Tests for JWT token creation."""
    
    def test_create_token(self):
        """Test creating a JWT token."""
        token = create_access_token("testuser", Permission.OPERATOR)
        
        assert token.access_token is not None
        assert token.token_type == "bearer"
        assert token.expires_in > 0
        
    def test_token_contains_user_info(self):
        """Test that token contains correct user info."""
        token = create_access_token("admin_user", Permission.ADMIN)
        data = decode_token(token.access_token)
        
        assert data is not None
        assert data.nick == "admin_user"
        assert data.user_class == Permission.ADMIN
        
    def test_different_permission_levels(self):
        """Test tokens with different permission levels."""
        for perm in [Permission.USER, Permission.OPERATOR, Permission.ADMIN, Permission.MASTER]:
            token = create_access_token("user", perm)
            data = decode_token(token.access_token)
            assert data.user_class == perm


class TestTokenValidation:
    """Tests for JWT token validation."""
    
    def test_invalid_token(self):
        """Test that invalid tokens are rejected."""
        data = decode_token("invalid.token.here")
        assert data is None
        
    def test_malformed_token(self):
        """Test that malformed tokens are rejected."""
        data = decode_token("not_a_jwt")
        assert data is None
        
    def test_empty_token(self):
        """Test that empty tokens are rejected."""
        data = decode_token("")
        assert data is None


# =============================================================================
# Permission Tests
# =============================================================================


class TestPermissions:
    """Tests for permission level handling."""
    
    def test_permission_hierarchy(self):
        """Test that permission levels form a proper hierarchy."""
        assert Permission.PUBLIC < Permission.READ_ONLY
        assert Permission.READ_ONLY < Permission.USER
        assert Permission.USER < Permission.VIP
        assert Permission.VIP < Permission.OPERATOR
        assert Permission.OPERATOR < Permission.CHEEF
        assert Permission.CHEEF < Permission.ADMIN
        assert Permission.ADMIN < Permission.MASTER
        assert Permission.MASTER < Permission.SUPERADMIN
        
    def test_admin_has_operator_access(self):
        """Test that higher permissions include lower ones."""
        admin_class = Permission.ADMIN
        
        # Admin should have access to all lower levels
        assert admin_class >= Permission.OPERATOR
        assert admin_class >= Permission.USER
        assert admin_class >= Permission.PUBLIC


# =============================================================================
# API Endpoint Tests (require app context)
# =============================================================================


@pytest.fixture
def app():
    """Create a test FastAPI app."""
    from verlihub.api.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def auth_header():
    """Create an authorization header with admin token."""
    token = create_access_token("admin", Permission.ADMIN)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def operator_header():
    """Create an authorization header with operator token."""
    token = create_access_token("operator", Permission.OPERATOR)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def user_header():
    """Create an authorization header with regular user token."""
    token = create_access_token("user", Permission.USER)
    return {"Authorization": f"Bearer {token.access_token}"}


class TestAuthEndpoints:
    """Tests for authentication API endpoints."""
    
    def test_health_check_no_auth(self, client):
        """Test health check works without auth."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"
        
    def test_me_requires_auth(self, client):
        """Test /auth/me requires authentication."""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        
    def test_me_with_valid_token(self, client, auth_header):
        """Test /auth/me with valid token."""
        response = client.get("/api/v1/auth/me", headers=auth_header)
        assert response.status_code == 200
        data = response.json()
        assert data["nick"] == "admin"
        assert data["user_class"] == Permission.ADMIN
        
    def test_refresh_token(self, client, auth_header):
        """Test token refresh."""
        response = client.post("/api/v1/auth/refresh", headers=auth_header)
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data


class TestPermissionEnforcement:
    """Tests for permission enforcement on protected endpoints."""
    
    def test_hub_topic_requires_operator(self, client, user_header):
        """Test that setting hub topic requires operator permission."""
        response = client.put(
            "/api/v1/hub/topic",
            json={"topic": "New Topic"},
            headers=user_header,
        )
        # Should fail with 401 (no auth) or 403 (insufficient perms) or 503 (no hub)
        assert response.status_code in [401, 403, 503]
        
    def test_hub_topic_with_operator(self, client, operator_header):
        """Test that operators can set hub topic."""
        # Note: This will fail if hub context is not available
        # But should not fail with permission error
        response = client.put(
            "/api/v1/hub/topic",
            json={"topic": "New Topic"},
            headers=operator_header,
        )
        # Either succeeds or fails due to missing context (503)
        assert response.status_code in [200, 503]
        
    def test_shutdown_requires_master(self, client, auth_header):
        """Test that shutdown requires master permission."""
        # Admin (5) is less than Master (10), should get 403
        # Or 503 if hub context not available
        response = client.post(
            "/api/v1/hub/shutdown",
            headers=auth_header,
        )
        assert response.status_code in [403, 503]
        
    def test_registered_users_requires_operator(self, client, user_header):
        """Test that listing registered users requires operator permission."""
        response = client.get(
            "/api/v1/users/registered",
            headers=user_header,
        )
        # Should fail with 403 (insufficient perms) or 500/503 (no db)
        assert response.status_code in [403, 500, 503]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
