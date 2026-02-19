"""
Tests for database-backed API routers (bans, invites, users/registered, auth/register+login).

Uses an in-memory SQLite database to exercise the full code paths of these
endpoints without requiring an external database.

Uses httpx.AsyncClient with ASGITransport instead of FastAPI TestClient so that
async route handlers run in the same event loop and are properly tracked by
coverage instrumentation.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from verlihub.api.auth import (
    Permission,
    create_access_token,
    hash_password,
)
from verlihub.models import (
    Ban,
    BanType,
    InviteCode,
    RegUser,
    UserClass,
    utc_now,
)


# =============================================================================
# In-memory SQLite session factory
# =============================================================================

_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    echo=False,
)

_TestSessionLocal = sessionmaker(
    bind=_test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def _get_test_session():
    """Yield a test database session (in-memory SQLite)."""
    async with _TestSessionLocal() as session:
        yield session


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest.fixture
def app():
    """Create a FastAPI app with DB session overrides and mock hub context."""
    from verlihub.api.app import create_app
    from verlihub.api.routes import bans as bans_mod
    from verlihub.api.routes import invites as invites_mod
    from verlihub.api.routes import users as users_mod
    from verlihub.api.routes import hub as hub_mod
    from verlihub.api.routes import stats as stats_mod
    from verlihub.api import auth as auth_mod

    test_app = create_app()

    # Override all get_session dependencies to use in-memory SQLite
    test_app.dependency_overrides[bans_mod.get_session] = _get_test_session
    test_app.dependency_overrides[invites_mod.get_session] = _get_test_session
    test_app.dependency_overrides[users_mod.get_session] = _get_test_session
    test_app.dependency_overrides[auth_mod.get_db_session] = _get_test_session

    # Mock hub context for routes that need it
    ctx = mock.MagicMock()
    ctx.is_running = True
    ctx.get_user_nicks.return_value = ["Alice"]
    ctx.find_user = lambda nick: nick == "Alice"
    ctx.kick_user = mock.MagicMock(return_value=True)
    ctx.send_to_user = mock.MagicMock(return_value=True)

    test_app.dependency_overrides[hub_mod.get_hub_context] = lambda: ctx
    test_app.dependency_overrides[stats_mod.get_hub_context] = lambda: ctx
    test_app.dependency_overrides[users_mod.get_hub_context] = lambda: ctx

    yield test_app
    test_app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    """Async HTTP client using ASGITransport for proper coverage tracking."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def op_header():
    token = create_access_token("op_user", Permission.OPERATOR)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def cheef_header():
    token = create_access_token("cheef_user", Permission.CHEEF)
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
# Helpers
# =============================================================================


async def _seed_user(nick="testuser", password="testpass", user_class=1):
    """Insert a registered user directly into the test DB."""
    async with _TestSessionLocal() as session:
        user = RegUser(
            nick=nick,
            login_pwd=hash_password(password),
            user_class=user_class,
            authorised=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _seed_ban(ip="1.2.3.4", nick="badguy", reason="testing"):
    """Insert a ban directly into the test DB."""
    async with _TestSessionLocal() as session:
        ban = Ban(
            ip=ip,
            nick=nick,
            ban_type=BanType.IP,
            reason=reason,
            nick_op="admin",
            date_start=utc_now(),
        )
        session.add(ban)
        await session.commit()
        await session.refresh(ban)
        return ban


async def _seed_invite(code="TEST123", created_by="admin", max_class=1, used=False):
    """Insert an invite code directly into the test DB."""
    async with _TestSessionLocal() as session:
        invite = InviteCode(
            code=code,
            created_by=created_by,
            max_class=max_class,
            used=used,
        )
        session.add(invite)
        await session.commit()
        await session.refresh(invite)
        return invite


# =============================================================================
# Ban Router Tests
# =============================================================================


class TestBanListEndpoint:
    """Tests for GET /api/v1/bans/."""

    @pytest.mark.asyncio
    async def test_list_bans_empty(self, client, op_header):
        resp = await client.get("/api/v1/bans/", headers=op_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["bans"] == []

    @pytest.mark.asyncio
    async def test_list_bans_with_data(self, client, op_header):
        await _seed_ban(ip="10.0.0.1", nick="user1")
        await _seed_ban(ip="10.0.0.2", nick="user2")

        resp = await client.get("/api/v1/bans/", headers=op_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_list_bans_pagination(self, client, op_header):
        for i in range(5):
            await _seed_ban(ip=f"10.0.0.{i}", nick=f"user{i}")

        resp = await client.get("/api/v1/bans/?skip=2&limit=2", headers=op_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_list_bans_by_type(self, client, op_header):
        await _seed_ban(ip="10.0.0.1", nick="user1")
        resp = await client.get(f"/api/v1/bans/?ban_type={BanType.IP}", headers=op_header)
        assert resp.status_code == 200


class TestBanGetEndpoint:
    """Tests for GET /api/v1/bans/{ban_id}."""

    @pytest.mark.asyncio
    async def test_get_ban_found(self, client, op_header):
        ban = await _seed_ban()
        resp = await client.get(f"/api/v1/bans/{ban.id}", headers=op_header)
        assert resp.status_code == 200
        assert resp.json()["nick"] == "badguy"

    @pytest.mark.asyncio
    async def test_get_ban_not_found(self, client, op_header):
        resp = await client.get("/api/v1/bans/9999", headers=op_header)
        assert resp.status_code == 404


class TestBanSearchEndpoints:
    """Tests for ban search endpoints."""

    @pytest.mark.asyncio
    async def test_search_by_ip(self, client, op_header):
        await _seed_ban(ip="192.168.1.1", nick="user1")
        resp = await client.get("/api/v1/bans/search/ip/192.168.1.1", headers=op_header)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_search_by_ip_not_found(self, client, op_header):
        resp = await client.get("/api/v1/bans/search/ip/255.255.255.255", headers=op_header)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_search_by_nick(self, client, op_header):
        await _seed_ban(nick="spammer")
        resp = await client.get("/api/v1/bans/search/nick/spammer", headers=op_header)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_search_by_nick_not_found(self, client, op_header):
        resp = await client.get("/api/v1/bans/search/nick/nobody", headers=op_header)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestBanCreateEndpoint:
    """Tests for POST /api/v1/bans/."""

    @pytest.mark.asyncio
    async def test_create_ban(self, client, cheef_header):
        resp = await client.post("/api/v1/bans/", json={
            "ip": "10.10.10.10",
            "nick": "badactor",
            "ban_type": BanType.IP,
            "reason": "Flooding",
        }, headers=cheef_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ip"] == "10.10.10.10"
        assert data["nick"] == "badactor"
        assert data["reason"] == "Flooding"

    @pytest.mark.asyncio
    async def test_create_ban_with_duration(self, client, cheef_header):
        resp = await client.post("/api/v1/bans/", json={
            "ip": "10.10.10.11",
            "duration_hours": 24,
        }, headers=cheef_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["date_limit"] is not None

    @pytest.mark.asyncio
    async def test_create_ban_no_ip_or_nick(self, client, cheef_header):
        resp = await client.post("/api/v1/bans/", json={
            "ip": "",
            "nick": "",
            "reason": "test",
        }, headers=cheef_header)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_ban_requires_cheef(self, client, op_header):
        resp = await client.post("/api/v1/bans/", json={
            "ip": "10.0.0.1",
        }, headers=op_header)
        assert resp.status_code == 403


class TestBanDeleteEndpoints:
    """Tests for DELETE ban endpoints."""

    @pytest.mark.asyncio
    async def test_delete_ban_by_id(self, client, cheef_header):
        ban = await _seed_ban()
        resp = await client.delete(f"/api/v1/bans/{ban.id}", headers=cheef_header)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_delete_ban_not_found(self, client, cheef_header):
        resp = await client.delete("/api/v1/bans/9999", headers=cheef_header)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unban_ip(self, client, cheef_header):
        await _seed_ban(ip="10.0.0.99", nick="u1")
        await _seed_ban(ip="10.0.0.99", nick="u2")
        resp = await client.delete("/api/v1/bans/ip/10.0.0.99", headers=cheef_header)
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    @pytest.mark.asyncio
    async def test_unban_ip_not_found(self, client, cheef_header):
        resp = await client.delete("/api/v1/bans/ip/255.255.255.255", headers=cheef_header)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unban_nick(self, client, cheef_header):
        await _seed_ban(ip="1.1.1.1", nick="banned_nick")
        resp = await client.delete("/api/v1/bans/nick/banned_nick", headers=cheef_header)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_unban_nick_not_found(self, client, cheef_header):
        resp = await client.delete("/api/v1/bans/nick/notbanned", headers=cheef_header)
        assert resp.status_code == 404


# =============================================================================
# Invite Router Tests
# =============================================================================


class TestInviteAllocate:
    """Tests for POST /api/v1/invites/allocate."""

    async def test_allocate_invites(self, client, admin_header):
        resp = await client.post("/api/v1/invites/allocate", json={
            "nick": "targetuser",
            "count": 3,
            "max_class": 1,
        }, headers=admin_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["allocated"] == 3
        assert len(data["codes"]) == 3
        assert data["nick"] == "targetuser"
        assert data["max_class"] == 1

    async def test_allocate_exceeds_own_class(self, client, admin_header):
        resp = await client.post("/api/v1/invites/allocate", json={
            "nick": "user",
            "count": 1,
            "max_class": 10,  # Master > Admin
        }, headers=admin_header)
        assert resp.status_code == 400

    async def test_allocate_invalid_class(self, client, admin_header):
        resp = await client.post("/api/v1/invites/allocate", json={
            "nick": "user",
            "count": 1,
            "max_class": 99,
        }, headers=admin_header)
        assert resp.status_code == 400


class TestInviteAdminList:
    """Tests for GET /api/v1/invites/admin."""

    @pytest.mark.asyncio
    async def test_list_all_invites(self, client, admin_header):
        await _seed_invite(code="INV1", created_by="alice")
        await _seed_invite(code="INV2", created_by="bob")

        resp = await client.get("/api/v1/invites/admin", headers=admin_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_list_invites_filter_by_nick(self, client, admin_header):
        await _seed_invite(code="INV1", created_by="alice")
        await _seed_invite(code="INV2", created_by="bob")

        resp = await client.get("/api/v1/invites/admin?nick=alice", headers=admin_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["created_by"] == "alice"

    @pytest.mark.asyncio
    async def test_list_invites_filter_used(self, client, admin_header):
        await _seed_invite(code="U1", created_by="admin", used=True)
        await _seed_invite(code="U2", created_by="admin", used=False)

        resp = await client.get("/api/v1/invites/admin?used=true", headers=admin_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["used"] is True


class TestInviteRevoke:
    """Tests for DELETE /api/v1/invites/{code}."""

    @pytest.mark.asyncio
    async def test_revoke_unused(self, client, admin_header):
        await _seed_invite(code="REVOKEME", created_by="admin", used=False)
        resp = await client.delete("/api/v1/invites/REVOKEME", headers=admin_header)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_revoke_used_fails(self, client, admin_header):
        await _seed_invite(code="USED", created_by="admin", used=True)
        resp = await client.delete("/api/v1/invites/USED", headers=admin_header)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_revoke_not_found(self, client, admin_header):
        resp = await client.delete("/api/v1/invites/NOPE", headers=admin_header)
        assert resp.status_code == 404


class TestInviteMine:
    """Tests for GET /api/v1/invites/mine."""

    @pytest.mark.asyncio
    async def test_my_invites(self, client):
        # Create invites for "regular" user
        await _seed_invite(code="MY1", created_by="regular")
        await _seed_invite(code="MY2", created_by="regular", used=True)

        token = create_access_token("regular", Permission.USER)
        resp = await client.get(
            "/api/v1/invites/mine",
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["used"] == 1
        assert data["available"] == 1

    @pytest.mark.asyncio
    async def test_my_invites_empty(self, client, user_header):
        resp = await client.get("/api/v1/invites/mine", headers=user_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


# =============================================================================
# Registered Users Router Tests (DB-backed)
# =============================================================================


class TestRegisteredUsersDB:
    """Tests for /api/v1/users/registered endpoints with real DB."""

    @pytest.mark.asyncio
    async def test_list_registered_empty(self, client, op_header):
        resp = await client.get("/api/v1/users/registered", headers=op_header)
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_registered_with_data(self, client, op_header):
        await _seed_user("alice", "pass1", 1)
        await _seed_user("bob", "pass2", 3)

        resp = await client.get("/api/v1/users/registered", headers=op_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_list_registered_class_filter(self, client, op_header):
        await _seed_user("alice", "pass1", 1)
        await _seed_user("bob", "pass2", 3)

        resp = await client.get("/api/v1/users/registered?user_class=3", headers=op_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["nick"] == "bob"

    @pytest.mark.asyncio
    async def test_get_registered_user(self, client, op_header):
        await _seed_user("alice", "pass1", 1)
        resp = await client.get("/api/v1/users/registered/alice", headers=op_header)
        assert resp.status_code == 200
        assert resp.json()["nick"] == "alice"

    @pytest.mark.asyncio
    async def test_get_registered_user_not_found(self, client, op_header):
        resp = await client.get("/api/v1/users/registered/ghost", headers=op_header)
        assert resp.status_code == 404

    async def test_create_registered_user(self, client, admin_header):
        resp = await client.post("/api/v1/users/registered", json={
            "nick": "newuser",
            "login_pwd": "hashed_pass",
            "user_class": 1,
        }, headers=admin_header)
        assert resp.status_code == 200
        assert resp.json()["nick"] == "newuser"

    @pytest.mark.asyncio
    async def test_create_duplicate_user(self, client, admin_header):
        await _seed_user("dupeuser", "pass1", 1)
        resp = await client.post("/api/v1/users/registered", json={
            "nick": "dupeuser",
            "login_pwd": "pass2",
            "user_class": 1,
        }, headers=admin_header)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_update_registered_user(self, client, admin_header):
        await _seed_user("updatable", "pass1", 1)
        resp = await client.patch("/api/v1/users/registered/updatable", json={
            "user_class": 3,
        }, headers=admin_header)
        assert resp.status_code == 200
        assert resp.json()["user_class"] == 3

    @pytest.mark.asyncio
    async def test_update_user_not_found(self, client, admin_header):
        resp = await client.patch("/api/v1/users/registered/ghost", json={
            "user_class": 3,
        }, headers=admin_header)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_registered_user(self, client, admin_header):
        await _seed_user("deleteme", "pass1", 1)
        resp = await client.delete("/api/v1/users/registered/deleteme", headers=admin_header)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_delete_user_not_found(self, client, admin_header):
        resp = await client.delete("/api/v1/users/registered/ghost", headers=admin_header)
        assert resp.status_code == 404


# =============================================================================
# Auth Router — Login with real DB
# =============================================================================


class TestAuthLoginDB:
    """Tests for POST /api/v1/auth/login with real DB."""

    @pytest.mark.asyncio
    async def test_login_success(self, client):
        await _seed_user("loginuser", "mypassword", 3)
        resp = await client.post("/api/v1/auth/login", json={
            "nick": "loginuser",
            "password": "mypassword",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client):
        await _seed_user("loginuser2", "rightpass", 1)
        resp = await client.post("/api/v1/auth/login", json={
            "nick": "loginuser2",
            "password": "wrongpass",
        })
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_user_not_found(self, client):
        resp = await client.post("/api/v1/auth/login", json={
            "nick": "nonexistent",
            "password": "whatever",
        })
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_disabled_user(self, client):
        """Test login for a disabled (unauthorised) user."""
        async with _TestSessionLocal() as session:
            user = RegUser(
                nick="disabled",
                login_pwd=hash_password("pass"),
                user_class=1,
                authorised=False,
            )
            session.add(user)
            await session.commit()

        resp = await client.post("/api/v1/auth/login", json={
            "nick": "disabled",
            "password": "pass",
        })
        assert resp.status_code == 401


# =============================================================================
# Auth Router — Registration with real DB
# =============================================================================


class TestAuthRegisterDB:
    """Tests for POST /api/v1/auth/register with real DB."""

    async def test_register_success(self, client):
        resp = await client.post("/api/v1/auth/register", json={
            "nick": "newreg",
            "password": "testpassword",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

    async def test_register_short_nick(self, client):
        resp = await client.post("/api/v1/auth/register", json={
            "nick": "a",
            "password": "testpassword",
        })
        assert resp.status_code == 400

    async def test_register_invalid_nick(self, client):
        resp = await client.post("/api/v1/auth/register", json={
            "nick": "bad nick!@#",
            "password": "testpassword",
        })
        assert resp.status_code == 400

    async def test_register_short_password(self, client):
        resp = await client.post("/api/v1/auth/register", json={
            "nick": "validnick",
            "password": "abc",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_register_duplicate_nick(self, client):
        await _seed_user("existing", "pass", 1)
        resp = await client.post("/api/v1/auth/register", json={
            "nick": "existing",
            "password": "testpass123",
        })
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_register_with_valid_invite(self, client):
        await _seed_invite(code="VALID123", created_by="admin", max_class=3)
        resp = await client.post("/api/v1/auth/register", json={
            "nick": "invited_user",
            "password": "testpass123",
            "invite_code": "VALID123",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_register_with_invalid_invite(self, client):
        resp = await client.post("/api/v1/auth/register", json={
            "nick": "inviteuser2",
            "password": "testpass123",
            "invite_code": "INVALID",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_register_with_used_invite(self, client):
        await _seed_invite(code="USED123", created_by="admin", max_class=1, used=True)
        resp = await client.post("/api/v1/auth/register", json={
            "nick": "inviteuser3",
            "password": "testpass123",
            "invite_code": "USED123",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_register_with_expired_invite(self, client):
        async with _TestSessionLocal() as session:
            invite = InviteCode(
                code="EXPIRED",
                created_by="admin",
                max_class=1,
                # Use naive UTC datetime to match what SQLite stores/retrieves
                expires_at=datetime.utcnow() - timedelta(hours=1),
            )
            session.add(invite)
            await session.commit()

        resp = await client.post("/api/v1/auth/register", json={
            "nick": "inviteuser4",
            "password": "testpass123",
            "invite_code": "EXPIRED",
        })
        assert resp.status_code == 400

    async def test_register_disabled(self, client):
        """Test registration when disabled via env var."""
        with mock.patch("verlihub.api.routes.auth.REGISTRATION_ENABLED", False):
            resp = await client.post("/api/v1/auth/register", json={
                "nick": "disabledreg",
                "password": "testpass123",
            })
            assert resp.status_code == 403

    async def test_register_require_invite_without_code(self, client):
        """Test registration when invite required but not provided."""
        with mock.patch("verlihub.api.routes.auth.REGISTRATION_REQUIRE_INVITE", True):
            resp = await client.post("/api/v1/auth/register", json={
                "nick": "noinvite",
                "password": "testpass123",
            })
            assert resp.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
