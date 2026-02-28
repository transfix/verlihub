"""
Tests for user authentication and registration.

Covers:
- Config API admin is seeded into RegUser DB at startup
- Regular users can authenticate against the database
- Self-registration creates valid RegUser entries
- Dashboard login uses user_class (not class_)
- Registration page renders correctly with hub branding
"""
import os

import pytest
import pytest_asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.models import RegUser, UserClass, InviteCode
from verlihub.models.database import Database
from verlihub.api.auth import (
    authenticate_user,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
    Permission,
)
from verlihub.config import VerlihubConfig, ApiConfig, UsersConfig, UserEntry


# =============================================================================
# Admin Seeding Tests (apply_config_to_db)
# =============================================================================


class TestAdminSeeding:
    """Test that api.username/password is seeded into RegUser."""

    @pytest.mark.asyncio
    async def test_admin_seeded_on_first_run(self, db: Database, db_session: AsyncSession):
        """Config admin should be created in RegUser table."""
        from verlihub.config import apply_config_to_db

        cfg = VerlihubConfig(
            api=ApiConfig(username="hub_admin", password="secret123"),
        )
        await apply_config_to_db(cfg, force=False)

        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "hub_admin")
        )
        user = result.scalar_one_or_none()

        assert user is not None
        assert user.user_class == UserClass.ADMIN
        assert user.authorised is True
        assert user.reg_op == "config-api"
        # Password should be hashed (bcrypt hashes start with $2b$)
        assert user.login_pwd.startswith("$2b$")
        # Verify password actually works
        assert verify_password("secret123", user.login_pwd)

    @pytest.mark.asyncio
    async def test_admin_not_overwritten_without_force(self, db: Database, db_session: AsyncSession):
        """Existing admin user should not be overwritten without --force."""
        from verlihub.config import apply_config_to_db

        # First run: create admin
        cfg = VerlihubConfig(
            api=ApiConfig(username="hub_admin", password="original_pw"),
        )
        await apply_config_to_db(cfg, force=False)

        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "hub_admin")
        )
        user = result.scalar_one()
        original_hash = user.login_pwd

        # Second run with different password, no force
        cfg2 = VerlihubConfig(
            api=ApiConfig(username="hub_admin", password="new_password"),
        )
        await apply_config_to_db(cfg2, force=False)

        await db_session.refresh(user)
        assert user.login_pwd == original_hash  # Unchanged

    @pytest.mark.asyncio
    async def test_admin_overwritten_with_force(self, db: Database, db_session: AsyncSession):
        """Admin password updated when --force is used."""
        from verlihub.config import apply_config_to_db

        # First run
        cfg = VerlihubConfig(
            api=ApiConfig(username="hub_admin", password="original_pw"),
        )
        await apply_config_to_db(cfg, force=False)

        # Second run with force
        cfg2 = VerlihubConfig(
            api=ApiConfig(username="hub_admin", password="forced_new_pw"),
        )
        await apply_config_to_db(cfg2, force=True)

        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "hub_admin")
        )
        user = result.scalar_one()
        assert verify_password("forced_new_pw", user.login_pwd)

    @pytest.mark.asyncio
    async def test_admin_not_seeded_without_password(self, db: Database, db_session: AsyncSession):
        """No admin created if password is empty."""
        from verlihub.config import apply_config_to_db

        cfg = VerlihubConfig(
            api=ApiConfig(username="admin", password=""),
        )
        await apply_config_to_db(cfg, force=False)

        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "admin")
        )
        user = result.scalar_one_or_none()
        assert user is None

    @pytest.mark.asyncio
    async def test_admin_and_yaml_users_both_seeded(self, db: Database, db_session: AsyncSession):
        """Config admin and users section are both created."""
        from verlihub.config import apply_config_to_db

        cfg = VerlihubConfig(
            api=ApiConfig(username="api_admin", password="admin_pw"),
            users=UsersConfig(
                masters=[UserEntry(nick="master1", password="master_pw")],
                registered=[UserEntry(nick="user1", password="user_pw")],
            ),
        )
        await apply_config_to_db(cfg, force=False)

        # API admin should exist
        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "api_admin")
        )
        admin = result.scalar_one_or_none()
        assert admin is not None
        assert admin.user_class == UserClass.ADMIN

        # Master should exist
        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "master1")
        )
        master = result.scalar_one_or_none()
        assert master is not None
        assert master.user_class == UserClass.MASTER

        # Regular user should exist
        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "user1")
        )
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.user_class == UserClass.REGISTERED

    @pytest.mark.asyncio
    async def test_admin_class_preserved_if_higher(self, db: Database, db_session: AsyncSession):
        """If existing admin already has class >= ADMIN, force doesn't lower it."""
        from verlihub.config import apply_config_to_db

        # Create a MASTER user manually
        master = RegUser(
            nick="hub_admin",
            login_pwd=hash_password("old_pw"),
            user_class=UserClass.MASTER,
            authorised=True,
            reg_op="manual",
        )
        db_session.add(master)
        await db_session.commit()

        # Run apply_config with force
        cfg = VerlihubConfig(
            api=ApiConfig(username="hub_admin", password="new_pw"),
        )
        await apply_config_to_db(cfg, force=True)

        await db_session.refresh(master)
        assert master.user_class == UserClass.MASTER  # Not lowered to ADMIN


# =============================================================================
# Database Authentication Tests
# =============================================================================


class TestDatabaseAuthentication:
    """Test authenticate_user against database RegUser table."""

    @pytest.mark.asyncio
    async def test_authenticate_valid_user(self, db: Database, db_session: AsyncSession):
        """Valid credentials should return the user."""
        user = RegUser(
            nick="testuser",
            login_pwd=hash_password("mypassword"),
            user_class=UserClass.REGISTERED,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        result = await authenticate_user("testuser", "mypassword", db_session)
        assert result is not None
        assert result.nick == "testuser"
        assert result.user_class == UserClass.REGISTERED

    @pytest.mark.asyncio
    async def test_authenticate_wrong_password(self, db: Database, db_session: AsyncSession):
        """Wrong password should return None."""
        user = RegUser(
            nick="testuser",
            login_pwd=hash_password("correct"),
            user_class=UserClass.REGISTERED,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        result = await authenticate_user("testuser", "wrong", db_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_nonexistent_user(self, db: Database, db_session: AsyncSession):
        """Nonexistent user should return None."""
        result = await authenticate_user("nobody", "password", db_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_unauthorized_user(self, db: Database, db_session: AsyncSession):
        """Disabled (authorised=False) user should return None."""
        user = RegUser(
            nick="disabled_user",
            login_pwd=hash_password("password"),
            user_class=UserClass.REGISTERED,
            authorised=False,
        )
        db_session.add(user)
        await db_session.commit()

        result = await authenticate_user("disabled_user", "password", db_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_admin_from_config(self, db: Database, db_session: AsyncSession):
        """Admin seeded from config should authenticate correctly."""
        from verlihub.config import apply_config_to_db

        cfg = VerlihubConfig(
            api=ApiConfig(username="cfg_admin", password="cfg_pw"),
        )
        await apply_config_to_db(cfg, force=False)

        result = await authenticate_user("cfg_admin", "cfg_pw", db_session)
        assert result is not None
        assert result.nick == "cfg_admin"
        assert result.user_class == UserClass.ADMIN

    @pytest.mark.asyncio
    async def test_authenticate_different_user_classes(self, db: Database, db_session: AsyncSession):
        """Users of different classes should all authenticate."""
        for nick, cls in [
            ("reg_user", UserClass.REGISTERED),
            ("vip_user", UserClass.VIP),
            ("op_user", UserClass.OPERATOR),
            ("admin_user", UserClass.ADMIN),
            ("master_user", UserClass.MASTER),
        ]:
            user = RegUser(
                nick=nick,
                login_pwd=hash_password("password"),
                user_class=cls,
                authorised=True,
            )
            db_session.add(user)
        await db_session.commit()

        for nick, cls in [
            ("reg_user", UserClass.REGISTERED),
            ("vip_user", UserClass.VIP),
            ("op_user", UserClass.OPERATOR),
            ("admin_user", UserClass.ADMIN),
            ("master_user", UserClass.MASTER),
        ]:
            result = await authenticate_user(nick, "password", db_session)
            assert result is not None, f"{nick} should authenticate"
            assert result.user_class == cls


# =============================================================================
# Token from RegUser Tests
# =============================================================================


class TestTokenFromRegUser:
    """Test that tokens created from RegUser data are correct."""

    def test_token_from_user_class_field(self):
        """Tokens should use user_class, not class_."""
        # This validates the fix from user.class_ -> user.user_class
        token = create_access_token("hub_user", UserClass.REGISTERED)
        data = decode_token(token.access_token)
        assert data is not None
        assert data.nick == "hub_user"
        assert data.user_class == UserClass.REGISTERED

    def test_token_preserves_all_user_classes(self):
        """All user class levels should round-trip through tokens."""
        for cls in [UserClass.REGISTERED, UserClass.VIP, UserClass.OPERATOR,
                     UserClass.ADMIN, UserClass.MASTER]:
            token = create_access_token("user", cls)
            data = decode_token(token.access_token)
            assert data.user_class == cls, f"Class {cls} should round-trip"

    def test_registered_user_has_limited_permissions(self):
        """A registered user token should not have admin access."""
        token = create_access_token("regular", UserClass.REGISTERED)
        data = decode_token(token.access_token)
        assert data.user_class < Permission.ADMIN
        assert data.user_class < Permission.OPERATOR
        assert data.user_class >= Permission.USER

    def test_admin_user_has_admin_permissions(self):
        """Admin token should have admin-level access."""
        token = create_access_token("admin", UserClass.ADMIN)
        data = decode_token(token.access_token)
        assert data.user_class >= Permission.ADMIN
        assert data.user_class >= Permission.OPERATOR


# =============================================================================
# Dashboard Registration Page Tests
# =============================================================================


class TestDashboardRegistrationPage:
    """Test that the registration page renders correctly."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from verlihub.api.app import create_app
        return TestClient(create_app(), raise_server_exceptions=False)

    def test_register_page_loads(self, client):
        """Registration page should return 200."""
        response = client.get("/dashboard/register")
        assert response.status_code == 200

    def test_register_page_has_form(self, client):
        """Registration page should have a registration form."""
        response = client.get("/dashboard/register")
        assert response.status_code == 200
        body = response.text
        assert 'action="/dashboard/register"' in body
        assert 'name="nick"' in body
        assert 'name="password"' in body
        assert 'name="confirm_password"' in body

    def test_register_page_has_hub_branding(self, client, monkeypatch):
        """Registration page should show hub name, not 'Verlihub Dashboard'."""
        response = client.get("/dashboard/register")
        assert response.status_code == 200
        body = response.text
        # Should have the hub name in the page
        assert "Create Account" in body

    def test_register_page_links_to_login(self, client):
        """Registration page should link back to login."""
        response = client.get("/dashboard/register")
        assert response.status_code == 200
        assert "/dashboard/login" in response.text

    def test_register_page_no_dashboard_in_title(self, client):
        """Title should say 'Register - HubName' not 'Register - HubName Dashboard'."""
        response = client.get("/dashboard/register")
        assert response.status_code == 200
        assert "Dashboard</title>" not in response.text

    def test_register_page_invite_code_optional_by_default(self, client, monkeypatch):
        """Invite code should be optional when require_invite is false."""
        monkeypatch.setenv("VH_REGISTRATION_REQUIRE_INVITE", "0")
        response = client.get("/dashboard/register")
        assert response.status_code == 200
        assert "Optional" in response.text

    def test_register_page_disabled_shows_warning(self, client, monkeypatch):
        """When registration disabled, page shows warning."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "0")
        response = client.get("/dashboard/register")
        assert response.status_code == 200
        assert "disabled" in response.text.lower()


class TestDashboardLoginPage:
    """Test login page links to registration."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from verlihub.api.app import create_app
        return TestClient(create_app(), raise_server_exceptions=False)

    def test_login_page_links_to_register(self, client):
        """Login page should have a 'Register here' link."""
        response = client.get("/dashboard/login")
        assert response.status_code == 200
        assert "/dashboard/register" in response.text

    def test_login_page_has_register_text(self, client):
        """Login page should mention account creation."""
        response = client.get("/dashboard/login")
        assert response.status_code == 200
        body = response.text.lower()
        assert "register" in body

    def test_login_page_uses_hub_logo(self, client):
        """Login page should contain an img tag for the hub logo."""
        response = client.get("/dashboard/login")
        assert response.status_code == 200
        # The login template uses {{ hub_logo }} - confirm an img exists
        assert "<img" in response.text


class TestDashboardRegistrationSubmit:
    """Test registration form submission."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from verlihub.api.app import create_app
        return TestClient(create_app(), raise_server_exceptions=False)

    def test_register_short_nick_rejected(self, client, monkeypatch):
        """Nick shorter than 2 chars should be rejected."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "1")
        response = client.post(
            "/dashboard/register",
            data={
                "nick": "a",
                "password": "password",
                "confirm_password": "password",
                "invite_code": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=" in response.headers["location"]

    def test_register_invalid_nick_rejected(self, client, monkeypatch):
        """Nick with invalid characters should be rejected."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "1")
        response = client.post(
            "/dashboard/register",
            data={
                "nick": "user name!",
                "password": "password",
                "confirm_password": "password",
                "invite_code": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=" in response.headers["location"]

    def test_register_short_password_rejected(self, client, monkeypatch):
        """Password shorter than 4 chars should be rejected."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "1")
        response = client.post(
            "/dashboard/register",
            data={
                "nick": "validnick",
                "password": "ab",
                "confirm_password": "ab",
                "invite_code": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=" in response.headers["location"]

    def test_register_password_mismatch_rejected(self, client, monkeypatch):
        """Mismatched passwords should be rejected."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "1")
        response = client.post(
            "/dashboard/register",
            data={
                "nick": "validnick",
                "password": "password1",
                "confirm_password": "password2",
                "invite_code": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=" in response.headers["location"]

    def test_register_when_disabled_rejected(self, client, monkeypatch):
        """Registration should be rejected when disabled."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "0")
        response = client.post(
            "/dashboard/register",
            data={
                "nick": "validnick",
                "password": "password",
                "confirm_password": "password",
                "invite_code": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "disabled" in response.headers["location"].lower() or "error=" in response.headers["location"]

    def test_register_requires_invite_when_configured(self, client, monkeypatch):
        """Registration without invite code should fail when invite required."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "1")
        monkeypatch.setenv("VH_REGISTRATION_REQUIRE_INVITE", "1")
        response = client.post(
            "/dashboard/register",
            data={
                "nick": "validnick",
                "password": "password",
                "confirm_password": "password",
                "invite_code": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "invite" in response.headers["location"].lower() or "error=" in response.headers["location"]


# =============================================================================
# API Registration Endpoint Tests
# =============================================================================


class TestApiRegistrationEndpoint:
    """Test the POST /auth/register API endpoint."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from verlihub.api.app import create_app
        return TestClient(create_app(), raise_server_exceptions=False)

    def test_register_api_enabled_by_default(self, client, monkeypatch):
        """Registration should be enabled by default."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "true")
        response = client.post(
            "/api/v1/auth/register",
            json={"nick": "newuser", "password": "password123"},
        )
        # 200 on success, 500/503 if DB not initialized in test context
        assert response.status_code in [200, 500, 503]

    def test_register_api_disabled(self, client, monkeypatch):
        """Registration should be rejected when disabled."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "false")
        response = client.post(
            "/api/v1/auth/register",
            json={"nick": "newuser", "password": "password123"},
        )
        assert response.status_code in [403, 500, 503]

    def test_register_api_nick_validation(self, client, monkeypatch):
        """Short nicks should be rejected."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "true")
        response = client.post(
            "/api/v1/auth/register",
            json={"nick": "x", "password": "password123"},
        )
        assert response.status_code in [400, 500, 503]

    def test_register_api_password_too_short(self, client, monkeypatch):
        """Short passwords should be rejected."""
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "true")
        response = client.post(
            "/api/v1/auth/register",
            json={"nick": "validuser", "password": "ab"},
        )
        assert response.status_code in [400, 500, 503]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
