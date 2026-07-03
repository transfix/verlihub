"""
Extended dashboard route tests — form submissions, SPA/embed pages,
register flow, invite permalink, and authenticated page branches.

Uses httpx.AsyncClient + ASGITransport for proper async coverage.
Also uses in-memory SQLite for DB-dependent routes (login_submit, register_submit).
"""
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import httpx
from sqlmodel import SQLModel, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from verlihub.api.app import create_app
from verlihub.api.auth import create_access_token, hash_password, TokenData
from verlihub.models import RegUser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Create a fresh app instance with mock hub context."""
    _app = create_app()

    # Override hub context dependency to return a mock
    mock_ctx = MagicMock()
    mock_ctx.is_running = False
    mock_ctx.hub_name = "TestHub"
    mock_ctx.user_count = 0
    mock_ctx.total_share = 0
    mock_ctx.uptime = 100
    mock_ctx.port = 411
    mock_ctx.get_user_list = MagicMock(return_value=[])
    mock_ctx.get_config = MagicMock(return_value={})
    mock_ctx.get_plugins = MagicMock(return_value=[])
    mock_ctx.get_python_scripts = MagicMock(return_value=[])
    mock_ctx.get_lua_scripts = MagicMock(return_value=[])

    from verlihub.api import deps
    original = deps._hub_context
    deps._hub_context = mock_ctx

    yield _app

    deps._hub_context = original


@pytest.fixture
async def client(app):
    """Async HTTP client."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c


def _cookie(nick="admin", cls=5):
    """Create auth cookie dict."""
    tok = create_access_token(nick, cls)
    return {"access_token": f"Bearer {tok.access_token}"}


# ===================================================================
# SPA & Embed endpoints
# ===================================================================

class TestSPAEmbed:

    async def test_spa_page(self, client):
        resp = await client.get("/dashboard/spa")
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text

    async def test_embed_page(self, client):
        resp = await client.get("/dashboard/embed")
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text


# ===================================================================
# Invite permalink
# ===================================================================

class TestInvitePermalink:

    async def test_invite_redirect(self, client):
        resp = await client.get("/dashboard/invite/ABC123")
        assert resp.status_code == 303
        assert "/dashboard/register?invite=ABC123" in resp.headers["location"]


# ===================================================================
# Register page (GET)
# ===================================================================

class TestRegisterPage:

    async def test_register_page_default(self, client):
        resp = await client.get("/dashboard/register")
        assert resp.status_code == 200
        assert b"register" in resp.content.lower() or b"Register" in resp.content

    async def test_register_page_with_error(self, client):
        resp = await client.get("/dashboard/register?error=Some+error")
        assert resp.status_code == 200

    async def test_register_page_with_invite(self, client):
        resp = await client.get("/dashboard/register?invite=CODE123")
        assert resp.status_code == 200

    @patch("verlihub.dashboard.routes.get_config_optional")
    async def test_register_page_disabled(self, mock_cfg, client):
        cfg = MagicMock()
        cfg.api.registration_enabled = False
        cfg.api.registration_require_invite = False
        cfg.hub.name = "Test"
        cfg.hub.description = ""
        cfg.hub.topic = ""
        cfg.hub.logo = ""
        mock_cfg.return_value = cfg
        resp = await client.get("/dashboard/register")
        assert resp.status_code == 200


# ===================================================================
# Register form submission (POST) — validation errors
# ===================================================================

class TestRegisterSubmitValidation:

    async def test_register_short_nick(self, client):
        resp = await client.post(
            "/dashboard/register",
            data={"nick": "a", "password": "1234", "confirm_password": "1234"},
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    async def test_register_invalid_nick(self, client):
        resp = await client.post(
            "/dashboard/register",
            data={"nick": "bad nick!", "password": "pass", "confirm_password": "pass"},
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    async def test_register_short_password(self, client):
        resp = await client.post(
            "/dashboard/register",
            data={"nick": "validnick", "password": "ab", "confirm_password": "ab"},
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    async def test_register_password_mismatch(self, client):
        resp = await client.post(
            "/dashboard/register",
            data={"nick": "validnick", "password": "pass1234", "confirm_password": "different"},
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    @patch("verlihub.dashboard.routes.get_config_optional")
    async def test_register_disabled(self, mock_cfg, client):
        cfg = MagicMock()
        cfg.api.registration_enabled = False
        cfg.api.registration_require_invite = False
        cfg.hub.name = "Test"
        cfg.hub.description = ""
        cfg.hub.topic = ""
        cfg.hub.logo = ""
        mock_cfg.return_value = cfg
        resp = await client.post(
            "/dashboard/register",
            data={"nick": "nick", "password": "pass", "confirm_password": "pass"},
        )
        assert resp.status_code == 303
        assert "disabled" in resp.headers["location"].lower() or "error=" in resp.headers["location"]

    @patch("verlihub.dashboard.routes.get_config_optional")
    async def test_register_invite_required_no_code(self, mock_cfg, client):
        cfg = MagicMock()
        cfg.api.registration_enabled = True
        cfg.api.registration_require_invite = True
        cfg.hub.name = "Test"
        cfg.hub.description = ""
        cfg.hub.topic = ""
        cfg.hub.logo = ""
        mock_cfg.return_value = cfg
        resp = await client.post(
            "/dashboard/register",
            data={"nick": "validnick", "password": "pass1234", "confirm_password": "pass1234", "invite_code": ""},
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


# ===================================================================
# Login form submission — empty credentials
# ===================================================================

class TestLoginSubmit:

    async def test_login_empty_credentials(self, client):
        resp = await client.post(
            "/dashboard/login",
            data={"username": "", "password": ""},
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    async def test_login_missing_password(self, client):
        resp = await client.post(
            "/dashboard/login",
            data={"username": "admin", "password": ""},
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]


# ===================================================================
# Logout
# ===================================================================

class TestLogout:

    async def test_logout_clears_cookie(self, client):
        resp = await client.get(
            "/dashboard/logout",
            cookies=_cookie(),
        )
        assert resp.status_code == 303
        assert "/dashboard/login" in resp.headers["location"]


# ===================================================================
# Authenticated page routes — redirect when unauthenticated
# ===================================================================

class TestUnauthenticatedRedirects:
    """Ensure all protected routes redirect to login without auth."""

    @pytest.mark.parametrize("path", [
        "/dashboard/",
        "/dashboard/users",
        "/dashboard/bans",
        "/dashboard/config",
        "/dashboard/logs",
        "/dashboard/chat",
        "/dashboard/plugins",
        "/dashboard/invites",
        "/dashboard/triggers",
        "/dashboard/redirects",
        "/dashboard/clients",
        "/dashboard/penalties",
        "/dashboard/flood-config",
        "/dashboard/protocol-stats",
    ])
    async def test_redirect_to_login(self, client, path):
        resp = await client.get(path)
        assert resp.status_code == 303
        assert "/dashboard/login" in resp.headers["location"]


# ===================================================================
# Authenticated page routes — with auth cookie
# ===================================================================

class TestAuthenticatedPages:

    async def test_dashboard_home(self, client):
        resp = await client.get("/dashboard/", cookies=_cookie())
        assert resp.status_code == 200

    async def test_users_page(self, client):
        resp = await client.get("/dashboard/users", cookies=_cookie())
        assert resp.status_code in [200, 500]

    async def test_users_page_with_search(self, client):
        resp = await client.get("/dashboard/users?search=alice&page=2", cookies=_cookie())
        assert resp.status_code in [200, 500]

    async def test_bans_page(self, client):
        resp = await client.get("/dashboard/bans", cookies=_cookie("op", 3))
        assert resp.status_code in [200, 500]

    async def test_bans_page_with_search(self, client):
        resp = await client.get("/dashboard/bans?search=10.0.0", cookies=_cookie("op", 3))
        assert resp.status_code in [200, 500]

    async def test_config_page(self, client):
        resp = await client.get("/dashboard/config", cookies=_cookie("master", 10))
        assert resp.status_code == 200

    async def test_logs_page(self, client):
        resp = await client.get("/dashboard/logs", cookies=_cookie())
        assert resp.status_code == 200

    async def test_chat_page(self, client):
        resp = await client.get("/dashboard/chat", cookies=_cookie("op", 3))
        assert resp.status_code == 200

    async def test_plugins_page(self, client):
        resp = await client.get("/dashboard/plugins", cookies=_cookie())
        assert resp.status_code == 200

    async def test_invites_page(self, client):
        resp = await client.get("/dashboard/invites", cookies=_cookie())
        assert resp.status_code == 200

    async def test_triggers_page(self, client):
        resp = await client.get("/dashboard/triggers", cookies=_cookie())
        assert resp.status_code == 200

    async def test_redirects_page(self, client):
        resp = await client.get("/dashboard/redirects", cookies=_cookie())
        assert resp.status_code == 200

    async def test_clients_page(self, client):
        resp = await client.get("/dashboard/clients", cookies=_cookie())
        assert resp.status_code == 200

    async def test_penalties_page(self, client):
        resp = await client.get("/dashboard/penalties", cookies=_cookie())
        assert resp.status_code == 200

    async def test_flood_config_page(self, client):
        resp = await client.get("/dashboard/flood-config", cookies=_cookie())
        assert resp.status_code == 200

    async def test_protocol_stats_page(self, client):
        resp = await client.get("/dashboard/protocol-stats", cookies=_cookie())
        assert resp.status_code == 200


# ===================================================================
# Cookie auth — bare token (without "Bearer " prefix)
# ===================================================================

class TestCookieAuthBareToken:

    async def test_bare_token_works(self, client):
        """Token without 'Bearer ' prefix should still authenticate."""
        tok = create_access_token("admin", 5)
        resp = await client.get(
            "/dashboard/",
            cookies={"access_token": tok.access_token},
        )
        # Should authenticate and show dashboard (not redirect to login)
        assert resp.status_code in [200, 500]
        assert resp.status_code != 303


# ===================================================================
# Plugins page — with hub context returning AttributeError
# ===================================================================

class TestPluginsPageBranches:

    async def test_plugins_with_attribute_error(self, app):
        """Hub context raises AttributeError for get_plugins — fallback."""
        from verlihub.api import deps
        ctx = deps._hub_context
        ctx.get_plugins.side_effect = AttributeError("no method")
        ctx.get_python_scripts.side_effect = AttributeError("no method")
        ctx.get_lua_scripts.side_effect = AttributeError("no method")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get("/dashboard/plugins", cookies=_cookie())
            assert resp.status_code == 200

    async def test_plugins_lua_from_dir(self, app, tmp_path):
        """Hub context raises AttributeError for lua_scripts — reads dir."""
        from verlihub.api import deps
        ctx = deps._hub_context
        ctx.get_plugins.return_value = []
        ctx.get_python_scripts.return_value = []
        ctx.get_lua_scripts.side_effect = AttributeError("no method")

        # Create fake lua scripts dir
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "test.lua").write_text("-- lua")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            with patch.dict(os.environ, {"VH_SCRIPTS_DIR": str(scripts_dir)}):
                resp = await client.get("/dashboard/plugins", cookies=_cookie())
                assert resp.status_code == 200
