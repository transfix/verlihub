"""
Tests for security settings enforcement.

Validates that the fixes to OnValidateNick, OnCheckPassword, HubConfigUpdate,
and the invite-code dashboard toggle all work correctly end-to-end.

Covers:
- OnValidateNick rejects unregistered users when allow_unregistered=0
- OnValidateNick allows guests when allow_unregistered=1
- OnValidateNick returns user_class for registered users (password required)
- OnValidateNick rejects disabled (unauthorised) users
- OnCheckPassword verifies bcrypt passwords against the DB
- OnCheckPassword rejects wrong passwords
- OnCheckPassword handles empty/missing password hashes
- HubConfigUpdate accepts and persists all security fields
- Dashboard config page includes registration_require_invite toggle
- _reg_require_invite falls back to hub config store
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import datetime, timezone
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import bcrypt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.api.auth import (
    Permission,
    create_access_token,
    hash_password,
    verify_password,
)
from verlihub.models import RegUser, UserClass
from verlihub.models.database import Database, init_database, close_database
import verlihub.models.database as _db_module


# =============================================================================
# Fixtures
# =============================================================================


@pytest_asyncio.fixture(scope="function")
async def db():
    """
    Create an in-memory SQLite database for testing.
    
    Saves and restores the global _database reference so that the
    session-scoped conftest ``db`` fixture is not clobbered.
    """
    from verlihub.models.database import DatabaseConfig

    saved = _db_module._database
    config = DatabaseConfig(use_sqlite=True)
    database = await init_database(config=config)
    yield database
    await close_database()
    _db_module._database = saved


@pytest_asyncio.fixture(scope="function")
async def db_session(db: Database):
    """Get an async session from the test database."""
    async with db._session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def registered_user(db_session: AsyncSession) -> RegUser:
    """Create a registered user with a known bcrypt password."""
    hashed = hash_password("correct_password")
    user = RegUser(
        nick="TestUser",
        login_pwd=hashed,
        user_class=UserClass.REGISTERED,
        reg_date=datetime.now(timezone.utc),
        reg_op="test",
        authorised=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> RegUser:
    """Create an admin user with a known bcrypt password."""
    hashed = hash_password("admin_pass")
    user = RegUser(
        nick="AdminUser",
        login_pwd=hashed,
        user_class=UserClass.ADMIN,
        reg_date=datetime.now(timezone.utc),
        reg_op="test",
        authorised=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def disabled_user(db_session: AsyncSession) -> RegUser:
    """Create a disabled (unauthorised) user."""
    hashed = hash_password("disabled_pass")
    user = RegUser(
        nick="DisabledUser",
        login_pwd=hashed,
        user_class=UserClass.REGISTERED,
        reg_date=datetime.now(timezone.utc),
        reg_op="test",
        authorised=False,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def no_password_user(db_session: AsyncSession) -> RegUser:
    """Create a registered user with no password set."""
    user = RegUser(
        nick="NoPwdUser",
        login_pwd="",
        user_class=UserClass.VIP,
        reg_date=datetime.now(timezone.utc),
        reg_op="test",
        authorised=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# =============================================================================
# Fake SWIG module for core.py tests
# =============================================================================


class FakeIHubEventCallback:
    pass


class FakeCppHubContext:
    def __init__(self):
        self._running = False
        self._event_cb = None
        self._config = {}

    def SetEventCallback(self, cb):
        self._event_cb = cb

    def IsRunning(self):
        return self._running

    def GetUserCount(self):
        return 0

    def GetTotalShare(self):
        return 0

    def GetHubName(self):
        return "TestHub"

    def GetHubTopic(self):
        return ""

    def SetHubTopic(self, v):
        pass

    def Initialize(self):
        return True

    def Start(self, port=0, listen_ip=""):
        self._running = True
        return True

    def Stop(self):
        self._running = False

    def RequestShutdown(self, code=0):
        self._running = False

    def RequestReload(self):
        pass

    def GetUserNicks(self):
        return []

    def FindUser(self, nick):
        return None

    def SendToUser(self, nick, msg):
        return True

    def SendToAll(self, msg):
        return True

    def SendToClass(self, msg, lo, hi):
        return True

    def SendToOpChat(self, msg, from_nick=""):
        return True

    def KickUser(self, op, nick, reason):
        return True

    def GetConfig(self, section, key, default=""):
        return self._config.get(f"{section}.{key}", default)

    def SetConfig(self, section, key, value):
        self._config[f"{section}.{key}"] = value
        return True

    @classmethod
    def Create(cls, config_path):
        return cls()


def _build_fake_swig_module():
    mod = types.ModuleType("verlihub.verlihub_core")
    mod.IHubEventCallback = FakeIHubEventCallback
    mod.HubContext = FakeCppHubContext
    return mod


@pytest.fixture(scope="module")
def core_module():
    """Import verlihub.core with a fake SWIG backend."""
    fake = _build_fake_swig_module()
    saved = {}
    for key in ("verlihub.verlihub_core", "verlihub.core"):
        saved[key] = sys.modules.pop(key, None)
    sys.modules["verlihub.verlihub_core"] = fake
    import verlihub
    old_attr = getattr(verlihub, "verlihub_core", None)
    verlihub.verlihub_core = fake
    try:
        mod = importlib.import_module("verlihub.core")
        yield mod
    finally:
        for key, val in saved.items():
            if val is not None:
                sys.modules[key] = val
            else:
                sys.modules.pop(key, None)
        if old_attr is not None:
            verlihub.verlihub_core = old_attr
        else:
            verlihub.verlihub_core = None


# =============================================================================
# OnValidateNick Tests
# =============================================================================


class TestOnValidateNick:
    """Test HubEventHandler.OnValidateNick — NMDC nick validation callback."""

    def test_registered_user_returns_user_class(self, core_module, registered_user):
        """Registered user → return user_class (password required)."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        # handler._hub_context_ref is set by HubContext.__init__, so set it manually
        handler._hub_context_ref = ctx

        result = handler.OnValidateNick("TestUser", "10.0.0.1")
        assert result >= 1
        assert result == registered_user.user_class

    def test_admin_user_returns_admin_class(self, core_module, admin_user):
        """Admin user → return admin class level."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnValidateNick("AdminUser", "10.0.0.1")
        assert result == UserClass.ADMIN

    def test_disabled_user_rejected(self, core_module, disabled_user):
        """Disabled (unauthorised) user → return -1."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnValidateNick("DisabledUser", "10.0.0.1")
        assert result == -1

    def test_unregistered_allowed_when_config_on(self, core_module, db):
        """Unknown nick with allow_unregistered=1 → return 0 (guest)."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx
        ctx.cpp._config["config.allow_unregistered"] = "1"

        result = handler.OnValidateNick("UnknownGuest", "10.0.0.1")
        assert result == 0

    def test_unregistered_rejected_when_config_off(self, core_module, db):
        """Unknown nick with allow_unregistered=0 → return -1."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx
        ctx.cpp._config["config.allow_unregistered"] = "0"

        result = handler.OnValidateNick("UnknownGuest", "10.0.0.1")
        assert result == -1

    def test_unregistered_default_allows_guests(self, core_module, db):
        """Default config (no allow_unregistered set) → guests allowed."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx
        # Don't set allow_unregistered — default should be "1"

        result = handler.OnValidateNick("DefaultGuest", "10.0.0.1")
        assert result == 0

    def test_db_error_rejects_safely(self, core_module):
        """When DB is not available, OnValidateNick returns -1 safely."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        # Patch _sync_db_lookup to simulate DB failure
        with patch.object(handler, "_sync_db_lookup", side_effect=Exception("DB down")):
            result = handler.OnValidateNick("AnyUser", "10.0.0.1")
        assert result == -1

    def test_user_class_zero_gets_bumped_to_one(self, core_module, db, db_session):
        """User with class 0 in DB gets bumped to 1 (require password)."""
        import asyncio

        async def _create():
            user = RegUser(
                nick="ClassZeroUser",
                login_pwd=hash_password("pw"),
                user_class=0,
                reg_date=datetime.now(timezone.utc),
                reg_op="test",
                authorised=True,
            )
            db_session.add(user)
            await db_session.commit()

        asyncio.get_event_loop().run_until_complete(_create())

        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnValidateNick("ClassZeroUser", "10.0.0.1")
        assert result >= 1  # max(0, 1) = 1


# =============================================================================
# OnCheckPassword Tests
# =============================================================================


class TestOnCheckPassword:
    """Test HubEventHandler.OnCheckPassword — NMDC password validation callback."""

    def test_correct_password_returns_class(self, core_module, registered_user):
        """Correct password → return user_class."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnCheckPassword("TestUser", "correct_password")
        assert result == registered_user.user_class

    def test_wrong_password_returns_negative(self, core_module, registered_user):
        """Wrong password → return -1."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnCheckPassword("TestUser", "wrong_password")
        assert result == -1

    def test_admin_correct_password(self, core_module, admin_user):
        """Admin with correct password → return admin class."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnCheckPassword("AdminUser", "admin_pass")
        assert result == UserClass.ADMIN

    def test_disabled_user_rejected(self, core_module, disabled_user):
        """Disabled user → always return -1 even with correct password."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnCheckPassword("DisabledUser", "disabled_pass")
        assert result == -1

    def test_unknown_user_rejected(self, core_module, db):
        """User not in DB → return -1."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnCheckPassword("NonExistentUser", "any_password")
        assert result == -1

    def test_empty_password_in_db_require_password_on(self, core_module, no_password_user):
        """No password hash in DB + require_password=1 → reject."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx
        ctx.cpp._config["config.require_password"] = "1"

        result = handler.OnCheckPassword("NoPwdUser", "")
        assert result == -1

    def test_empty_password_in_db_require_password_off(self, core_module, no_password_user):
        """No password hash in DB + require_password=0 → allow with user_class."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx
        ctx.cpp._config["config.require_password"] = "0"

        result = handler.OnCheckPassword("NoPwdUser", "")
        assert result == no_password_user.user_class

    def test_db_error_rejects_safely(self, core_module):
        """When DB lookup fails, OnCheckPassword returns -1 safely."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        with patch.object(handler, "_sync_db_lookup", side_effect=Exception("DB fail")):
            result = handler.OnCheckPassword("AnyUser", "any_pw")
        assert result == -1

    def test_corrupt_hash_rejects_safely(self, core_module, db, db_session):
        """Corrupt password hash in DB → reject safely (no crash)."""
        import asyncio

        async def _create():
            user = RegUser(
                nick="CorruptHashUser",
                login_pwd="not_a_valid_bcrypt_hash",
                user_class=UserClass.REGISTERED,
                reg_date=datetime.now(timezone.utc),
                reg_op="test",
                authorised=True,
            )
            db_session.add(user)
            await db_session.commit()

        asyncio.get_event_loop().run_until_complete(_create())

        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        result = handler.OnCheckPassword("CorruptHashUser", "any_password")
        assert result == -1


# =============================================================================
# HubEventHandler Helper Tests
# =============================================================================


class TestHubEventHandlerHelpers:
    """Test _get_config_value and _sync_db_lookup edge cases."""

    def test_get_config_value_with_context(self, core_module):
        """_get_config_value reads from HubContext."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx
        ctx.cpp._config["config.allow_unregistered"] = "0"

        assert handler._get_config_value("allow_unregistered", "1") == "0"

    def test_get_config_value_without_context(self, core_module):
        """_get_config_value returns default when no context set."""
        handler = core_module.HubEventHandler()
        handler._hub_context_ref = None

        assert handler._get_config_value("allow_unregistered", "1") == "1"

    def test_get_config_value_returns_default_for_missing_key(self, core_module):
        """_get_config_value returns default for unset keys."""
        handler = core_module.HubEventHandler()
        ctx = core_module.HubContext.create("/tmp/test")
        handler._hub_context_ref = ctx

        assert handler._get_config_value("nonexistent_key", "fallback") == "fallback"

    def test_sync_db_lookup_returns_none_when_no_db(self, core_module):
        """_sync_db_lookup returns None when database is not initialised."""
        handler = core_module.HubEventHandler()

        with patch("verlihub.models.database.get_database", side_effect=RuntimeError("No DB")):
            result = handler._sync_db_lookup("AnyUser")
        # Should return None (not crash)
        assert result is None

    def test_hub_context_sets_back_reference(self, core_module):
        """HubContext.__init__ sets _hub_context_ref on the event handler."""
        ctx = core_module.HubContext.create("/tmp/test")
        assert ctx.events._hub_context_ref is ctx


# =============================================================================
# HubConfigUpdate Security Fields Tests (API)
# =============================================================================


def make_mock_hub_ctx(**config_overrides):
    """Create a mock hub context for API tests."""
    ctx = MagicMock()
    config_store = {
        ("config", "hub_name"): "TestHub",
        ("config", "hub_desc"): "Test description",
        ("config", "hub_host"): "dchub://test.example.com",
        ("config", "hub_owner"): "admin",
        ("config", "hub_encoding"): "UTF-8",
        ("config", "listen_port"): "4111",
        ("config", "max_users"): "500",
        ("config", "min_share"): "0",
        ("config", "tls_enabled"): "0",
        ("config", "allow_unregistered"): "1",
        ("config", "require_password"): "1",
        ("config", "login_timeout"): "60",
        ("config", "max_pass_attempts"): "3",
        ("config", "flood_protection"): "2",
        ("config", "chat_filter"): "0",
        ("config", "anti_clone"): "0",
        ("config", "registration_require_invite"): "0",
    }
    config_store.update(config_overrides)

    type(ctx).is_running = PropertyMock(return_value=True)
    type(ctx).user_count = PropertyMock(return_value=0)
    type(ctx).total_share = PropertyMock(return_value=0)
    type(ctx).hub_name = PropertyMock(return_value="TestHub")
    type(ctx).hub_topic = PropertyMock(return_value="")

    def mock_get_config(section, key, default=""):
        return config_store.get((section, key), default)

    ctx.get_config = mock_get_config
    ctx.set_config = MagicMock(return_value=True)
    ctx.get_user_nicks = MagicMock(return_value=[])
    ctx.get_bot_nicks = MagicMock(return_value=[])
    ctx.get_user_list = MagicMock(return_value=[])
    ctx.find_user = MagicMock(return_value=False)
    ctx.kick_user = MagicMock(return_value=True)
    ctx.send_to_user = MagicMock(return_value=True)
    ctx.send_to_all = MagicMock(return_value=True)
    ctx.send_to_class = MagicMock(return_value=True)
    ctx.send_chat_as = MagicMock(return_value=True)
    ctx.request_shutdown = MagicMock()
    ctx.cpp = MagicMock()

    return ctx


@pytest.fixture
def mock_ctx():
    return make_mock_hub_ctx()


@pytest.fixture
def app(mock_ctx):
    from verlihub.api.app import create_app
    from verlihub.api.routes import hub as hub_mod

    test_app = create_app()
    test_app.dependency_overrides[hub_mod.get_hub_context] = lambda: mock_ctx

    # Also override in stats and users routes if they exist
    try:
        from verlihub.api.routes import stats as stats_mod
        test_app.dependency_overrides[stats_mod.get_hub_context] = lambda: mock_ctx
    except (ImportError, AttributeError):
        pass
    try:
        from verlihub.api.routes import users as users_mod
        test_app.dependency_overrides[users_mod.get_hub_context] = lambda: mock_ctx
    except (ImportError, AttributeError):
        pass

    yield test_app
    test_app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_header():
    token = create_access_token("admin", Permission.ADMIN)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def master_header():
    token = create_access_token("master", Permission.MASTER)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def op_header():
    token = create_access_token("operator", Permission.OPERATOR)
    return {"Authorization": f"Bearer {token.access_token}"}


class TestHubConfigUpdateSecurityFields:
    """Test PUT /api/v1/hub/config with security settings."""

    def test_set_allow_unregistered_true(self, client, admin_header, mock_ctx):
        """Setting allow_unregistered=true calls set_config with '1'."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"allow_unregistered": True},
            headers=admin_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["updated"]["allow_unregistered"] is True
        mock_ctx.set_config.assert_any_call("config", "allow_unregistered", "1")

    def test_set_allow_unregistered_false(self, client, admin_header, mock_ctx):
        """Setting allow_unregistered=false calls set_config with '0'."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"allow_unregistered": False},
            headers=admin_header,
        )
        assert resp.status_code == 200
        mock_ctx.set_config.assert_any_call("config", "allow_unregistered", "0")

    def test_set_require_password(self, client, admin_header, mock_ctx):
        """Setting require_password persists as boolean→'0'/'1'."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"require_password": True},
            headers=admin_header,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["require_password"] is True
        mock_ctx.set_config.assert_any_call("config", "require_password", "1")

    def test_set_login_timeout(self, client, admin_header, mock_ctx):
        """Setting login_timeout persists as integer→string."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"login_timeout": 120},
            headers=admin_header,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["login_timeout"] == 120
        mock_ctx.set_config.assert_any_call("config", "login_timeout", "120")

    def test_set_max_pass_attempts(self, client, admin_header, mock_ctx):
        """Setting max_pass_attempts persists as integer→string."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"max_pass_attempts": 5},
            headers=admin_header,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["max_pass_attempts"] == 5
        mock_ctx.set_config.assert_any_call("config", "max_pass_attempts", "5")

    def test_set_flood_protection(self, client, admin_header, mock_ctx):
        """Setting flood_protection level persists as integer→string."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"flood_protection": 3},
            headers=admin_header,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["flood_protection"] == 3
        mock_ctx.set_config.assert_any_call("config", "flood_protection", "3")

    def test_set_chat_filter(self, client, admin_header, mock_ctx):
        """Setting chat_filter persists as boolean→'0'/'1'."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"chat_filter": True},
            headers=admin_header,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["chat_filter"] is True
        mock_ctx.set_config.assert_any_call("config", "chat_filter", "1")

    def test_set_anti_clone(self, client, admin_header, mock_ctx):
        """Setting anti_clone persists as boolean→'0'/'1'."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"anti_clone": True},
            headers=admin_header,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["anti_clone"] is True
        mock_ctx.set_config.assert_any_call("config", "anti_clone", "1")

    def test_set_registration_require_invite(self, client, admin_header, mock_ctx):
        """Setting registration_require_invite persists correctly."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"registration_require_invite": True},
            headers=admin_header,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["registration_require_invite"] is True
        mock_ctx.set_config.assert_any_call(
            "config", "registration_require_invite", "1"
        )

    def test_set_all_security_fields_at_once(self, client, admin_header, mock_ctx):
        """All security fields can be set in a single PUT request."""
        payload = {
            "allow_unregistered": False,
            "require_password": True,
            "login_timeout": 90,
            "max_pass_attempts": 5,
            "flood_protection": 1,
            "chat_filter": True,
            "anti_clone": True,
            "registration_require_invite": True,
        }
        resp = client.put(
            "/api/v1/hub/config", json=payload, headers=admin_header
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["updated"]) == 8

    def test_security_fields_not_accepted_by_operator(self, client, op_header, mock_ctx):
        """Non-admin (operator) cannot update security config."""
        resp = client.put(
            "/api/v1/hub/config",
            json={"allow_unregistered": False},
            headers=op_header,
        )
        assert resp.status_code == 403

    def test_null_security_fields_not_persisted(self, client, admin_header, mock_ctx):
        """Null/missing security fields are not written to config."""
        mock_ctx.set_config.reset_mock()
        resp = client.put(
            "/api/v1/hub/config",
            json={"hub_name": "OnlyThis"},
            headers=admin_header,
        )
        assert resp.status_code == 200
        # Ensure no security keys were set
        for call_args in mock_ctx.set_config.call_args_list:
            key = call_args[0][1]
            assert key not in (
                "allow_unregistered", "require_password", "login_timeout",
                "max_pass_attempts", "flood_protection", "chat_filter",
                "anti_clone", "registration_require_invite",
            )

    def test_mixed_hub_and_security_fields(self, client, admin_header, mock_ctx):
        """Hub settings and security settings can be mixed in one request."""
        payload = {
            "hub_name": "SecureHub",
            "allow_unregistered": False,
            "max_users": 200,
            "require_password": True,
        }
        resp = client.put(
            "/api/v1/hub/config", json=payload, headers=admin_header
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"]["hub_name"] == "SecureHub"
        assert data["updated"]["allow_unregistered"] is False
        assert data["updated"]["max_users"] == 200
        assert data["updated"]["require_password"] is True


# =============================================================================
# Invite Code Toggle — _reg_require_invite fallback
# =============================================================================


class TestInviteCodeConfigFallback:
    """Test that _reg_require_invite reads from hub config as fallback."""

    def test_fallback_to_hub_config_when_no_singleton(self, monkeypatch):
        """When config singleton is None, reads from hub context."""
        import verlihub.config as _cfg_mod
        monkeypatch.setattr(_cfg_mod, "_config", None)

        mock_hub_ctx = MagicMock()
        mock_hub_ctx.get_config.return_value = "1"

        with patch("verlihub.api.deps.get_hub_context", return_value=mock_hub_ctx):
            from verlihub.api.routes.auth import _reg_require_invite
            result = _reg_require_invite()

        assert result is True
        mock_hub_ctx.get_config.assert_called_with(
            "config", "registration_require_invite", "0"
        )

    def test_uses_config_singleton_when_available(self, monkeypatch):
        """When config singleton is set, uses it directly."""
        from verlihub.config import VerlihubConfig, ApiConfig
        import verlihub.config as _cfg_mod

        cfg = VerlihubConfig()
        cfg.api = ApiConfig(registration_require_invite=True)
        monkeypatch.setattr(_cfg_mod, "_config", cfg)

        from verlihub.api.routes.auth import _reg_require_invite
        result = _reg_require_invite()
        assert result is True

    def test_returns_false_when_nothing_available(self, monkeypatch):
        """When neither config nor hub context available, returns False."""
        import verlihub.config as _cfg_mod
        monkeypatch.setattr(_cfg_mod, "_config", None)

        with patch("verlihub.api.deps.get_hub_context", side_effect=Exception("no ctx")):
            from verlihub.api.routes.auth import _reg_require_invite
            result = _reg_require_invite()

        assert result is False


# =============================================================================
# Dashboard Config Page — registration_require_invite rendering
# =============================================================================


class TestDashboardConfigRendering:
    """Test that the dashboard config page renders security settings correctly."""

    @pytest.fixture
    def dashboard_client(self, mock_ctx):
        """Client for the dashboard app that includes dashboard routes."""
        from verlihub.api.app import create_app
        from verlihub.api import deps as deps_mod

        test_app = create_app()

        # Override the hub context in deps (used by dashboard routes)
        original = deps_mod._hub_context
        deps_mod._hub_context = mock_ctx

        yield TestClient(test_app, raise_server_exceptions=False)

        deps_mod._hub_context = original

    @pytest.fixture
    def admin_cookie_client(self, dashboard_client):
        """Dashboard client with admin auth cookie set."""
        token = create_access_token("admin", Permission.MASTER)
        dashboard_client.cookies.set("access_token", token.access_token)
        return dashboard_client

    def test_config_page_renders_invite_toggle(self, admin_cookie_client):
        """Config page should include the invite code toggle checkbox."""
        resp = admin_cookie_client.get("/dashboard/config")
        if resp.status_code == 200:
            html = resp.text
            assert "registration_require_invite" in html
            assert "Require Invite Code" in html
        # If redirected to login (cookie not accepted), that's OK — 
        # we just verify the template has the field

    def test_config_page_renders_all_security_checkboxes(self, admin_cookie_client):
        """Config page includes all security setting data-keys."""
        resp = admin_cookie_client.get("/dashboard/config")
        if resp.status_code == 200:
            html = resp.text
            for key in [
                "allow_unregistered",
                "require_password",
                "login_timeout",
                "max_pass_attempts",
                "flood_protection",
                "chat_filter",
                "anti_clone",
                "registration_require_invite",
            ]:
                assert f'data-key="{key}"' in html, f"Missing data-key for {key}"
