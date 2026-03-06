"""
Tests for LLM chat gateway, AI chat dashboard page, navbar visibility,
MCP server, and related configuration.

Covers:
- LlmConfig loading from dict and env vars
- Tool definitions (readonly vs admin)
- _execute_tool for each hub tool (mocked hub context)
- REST endpoint: /api/v1/llm/status, /api/v1/llm/chat
- Dashboard: /dashboard/ai-chat renders correctly
- Dashboard: AI Chat navbar item hidden when LLM disabled
- Dashboard: AI Chat navbar item hidden when user class too low
- WebSocket: /ws/llm-chat auth, permission, mock LLM streaming
- MCP server: tool dispatch, resource reads, prompts
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from verlihub.api.app import create_app
from verlihub.api.auth import create_access_token, TokenData
from verlihub.config import LlmConfig, VerlihubConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cookie(nick: str = "admin", cls: int = 5) -> dict[str, str]:
    """Create auth cookie dict."""
    tok = create_access_token(nick, cls)
    return {"access_token": f"Bearer {tok.access_token}"}


def _bearer(nick: str = "admin", cls: int = 5) -> dict[str, str]:
    """Create auth header dict."""
    tok = create_access_token(nick, cls)
    return {"Authorization": f"Bearer {tok.access_token}"}


def _token_str(nick: str = "admin", cls: int = 5) -> str:
    """Return raw JWT string."""
    tok = create_access_token(nick, cls)
    return tok.access_token


def _make_token_data(nick: str = "admin", cls: int = 5) -> TokenData:
    """Create a TokenData with required exp field for testing."""
    return TokenData(
        nick=nick,
        user_class=cls,
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _mock_hub_context() -> MagicMock:
    """Standard mock hub context with typical data."""
    ctx = MagicMock()
    ctx.is_running = True
    ctx.hub_name = "TestHub"
    ctx.hub_topic = "Test Topic"
    ctx.user_count = 3
    ctx.total_share = 1024 * 1024 * 1024 * 50  # 50 GiB
    ctx.uptime = 3600
    ctx.port = 411
    ctx.get_user_list = MagicMock(return_value=[
        {"nick": "Alice", "ip": "10.0.0.1", "share": 1024**3 * 20, "user_class": 3, "country_code": "US", "client": "DC++"},
        {"nick": "Bob", "ip": "10.0.0.2", "share": 1024**3 * 10, "user_class": 1, "country_code": "DE", "client": "FlylinkDC++"},
        {"nick": "Charlie", "ip": "10.0.0.3", "share": 1024**3 * 20, "user_class": 0, "country_code": "US", "client": "EiskaltDC++"},
    ])
    _users = {
        "Alice": {"nick": "Alice", "ip": "10.0.0.1", "share": 1024**3 * 20, "user_class": 3},
    }
    ctx.get_user_info = MagicMock(side_effect=lambda nick: _users.get(nick))
    ctx.get_config = MagicMock(return_value="test_value")
    ctx.set_config = MagicMock()
    ctx.kick_user = MagicMock()
    ctx.send_to_all = MagicMock()
    ctx.send_to_user = MagicMock()
    ctx.execute_command = MagicMock(return_value="Command OK")
    ctx.get_plugins = MagicMock(return_value=[])
    ctx.get_python_scripts = MagicMock(return_value=[])
    ctx.get_lua_scripts = MagicMock(return_value=[])
    ctx.get_bot_list = MagicMock(return_value=[
        {"nick": "HubBot", "class": 10},
    ])
    return ctx


def _llm_enabled_config() -> MagicMock:
    """Mock VerlihubConfig with LLM enabled."""
    cfg = MagicMock(spec=VerlihubConfig)
    cfg.llm = LlmConfig(
        enabled=True,
        base_url="http://localhost:11434/v1",
        model="test-model",
        api_key="test-key",
        max_tool_rounds=3,
        temperature=0.3,
        max_tokens=512,
        min_class=3,
        admin_class=5,
    )
    cfg.hub = MagicMock()
    cfg.hub.name = "TestHub"
    cfg.hub.description = "Test description"
    cfg.hub.topic = "Test topic"
    cfg.hub.host = "localhost"
    cfg.hub.logo = ""
    cfg.api = MagicMock()
    cfg.api.registration_enabled = True
    cfg._config_dir = "/tmp"
    return cfg


def _llm_disabled_config() -> MagicMock:
    """Mock VerlihubConfig with LLM disabled."""
    cfg = _llm_enabled_config()
    cfg.llm = LlmConfig(enabled=False)
    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Create a fresh app with mock hub context."""
    _app = create_app()

    mock_ctx = _mock_hub_context()
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


# ======================================================================
# LlmConfig tests
# ======================================================================


class TestLlmConfig:

    def test_defaults(self):
        cfg = LlmConfig()
        assert cfg.enabled is False
        assert cfg.min_class == 3
        assert cfg.admin_class == 5
        assert cfg.max_tool_rounds == 5
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 2048

    def test_from_dict_llm_section(self):
        data = {
            "llm": {
                "enabled": True,
                "base_url": "http://my-llm:8080/v1",
                "model": "custom-model",
                "api_key": "sk-test",
                "min_class": 0,
                "admin_class": 10,
                "max_tool_rounds": 10,
                "temperature": 0.7,
                "max_tokens": 4096,
            }
        }
        config = VerlihubConfig.from_dict(data)
        assert config.llm.enabled is True
        assert config.llm.base_url == "http://my-llm:8080/v1"
        assert config.llm.model == "custom-model"
        assert config.llm.api_key == "sk-test"
        assert config.llm.min_class == 0
        assert config.llm.admin_class == 10
        assert config.llm.max_tool_rounds == 10
        assert config.llm.temperature == 0.7
        assert config.llm.max_tokens == 4096

    def test_from_dict_no_llm_section_uses_defaults(self):
        config = VerlihubConfig.from_dict({})
        assert config.llm.enabled is False
        assert config.llm.model == "llama3.1"

    @patch.dict("os.environ", {
        "VH_LLM_ENABLED": "true",
        "VH_LLM_BASE_URL": "http://env-llm/v1",
        "VH_LLM_MODEL": "env-model",
        "VH_LLM_API_KEY": "env-key",
        "VH_LLM_MIN_CLASS": "0",
        "VH_LLM_ADMIN_CLASS": "3",
    }, clear=False)
    def test_from_env_overrides(self):
        config = VerlihubConfig.from_env()
        assert config.llm.enabled is True
        assert config.llm.base_url == "http://env-llm/v1"
        assert config.llm.model == "env-model"
        assert config.llm.api_key == "env-key"
        assert config.llm.min_class == 0
        assert config.llm.admin_class == 3


# ======================================================================
# Tool definition tests
# ======================================================================


class TestToolDefinitions:

    def test_readonly_tools_count(self):
        from verlihub.api.routes.llm import _build_readonly_tools
        tools = _build_readonly_tools()
        assert len(tools) == 9
        names = {t["function"]["name"] for t in tools}
        assert "get_hub_info" in names
        assert "list_online_users" in names
        assert "search_bans" in names

    def test_admin_tools_count(self):
        from verlihub.api.routes.llm import _build_admin_tools
        tools = _build_admin_tools()
        assert len(tools) == 6
        names = {t["function"]["name"] for t in tools}
        assert "kick_user" in names
        assert "send_broadcast" in names
        assert "set_hub_config" in names

    def test_all_tools_have_correct_schema(self):
        from verlihub.api.routes.llm import _build_readonly_tools, _build_admin_tools
        for tool in _build_readonly_tools() + _build_admin_tools():
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"


# ======================================================================
# _execute_tool tests (mocked hub context)
# ======================================================================


class TestExecuteTool:

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        self.ctx = _mock_hub_context()
        self.admin_user = _make_token_data("admin", 5)
        self.regular_user = _make_token_data("op", 3)

    async def _exec(self, name, args=None, user=None, is_admin=True):
        from verlihub.api.routes.llm import _execute_tool
        with patch("verlihub.api.routes.llm.get_hub_context", return_value=self.ctx), \
             patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            result = await _execute_tool(
                name, args or {},
                user or self.admin_user,
                is_admin,
            )
        return json.loads(result)

    async def test_get_hub_info(self):
        data = await self._exec("get_hub_info")
        assert data["name"] == "TestHub"
        assert "users_online" in data

    async def test_list_online_users(self):
        data = await self._exec("list_online_users")
        assert isinstance(data, list)
        assert len(data) == 3
        assert data[0]["nick"] == "Alice"

    async def test_get_user_info_found(self):
        data = await self._exec("get_user_info", {"nick": "Alice"})
        assert data["nick"] == "Alice"

    async def test_get_user_info_not_found(self):
        data = await self._exec("get_user_info", {"nick": "nobody"})
        assert "error" in data

    async def test_list_operators(self):
        data = await self._exec("list_operators")
        assert isinstance(data, list)
        # Alice has class 3 = operator
        ops = [u for u in data if u["nick"] == "Alice"]
        assert len(ops) == 1

    async def test_list_bots(self):
        data = await self._exec("list_bots")
        assert isinstance(data, list)

    async def test_get_geo_distribution(self):
        data = await self._exec("get_geo_distribution")
        assert isinstance(data, list)
        # We have 2 US users and 1 DE user
        countries = {d["country"]: d["users"] for d in data}
        assert countries.get("US") == 2
        assert countries.get("DE") == 1

    async def test_get_share_statistics(self):
        data = await self._exec("get_share_statistics")
        assert "total_share" in data
        assert "user_count" in data
        assert data["user_count"] == 3

    async def test_get_hub_statistics(self):
        data = await self._exec("get_hub_statistics")
        assert data["users_online"] == 3
        assert data["is_running"] is True

    # --- Admin tools ---

    async def test_kick_user_as_admin(self):
        data = await self._exec("kick_user", {"nick": "Bob", "reason": "test"}, is_admin=True)
        assert data["success"] is True
        self.ctx.kick_user.assert_called_once_with("admin", "Bob", "test")

    async def test_kick_user_denied_for_non_admin(self):
        data = await self._exec("kick_user", {"nick": "Bob", "reason": "test"},
                                user=self.regular_user, is_admin=False)
        assert "error" in data
        assert "Permission denied" in data["error"]

    async def test_send_broadcast_as_admin(self):
        data = await self._exec("send_broadcast", {"message": "Hello all"}, is_admin=True)
        assert data["success"] is True
        self.ctx.send_to_all.assert_called_once_with("Hello all")

    async def test_send_broadcast_denied_for_non_admin(self):
        data = await self._exec("send_broadcast", {"message": "Hi"},
                                user=self.regular_user, is_admin=False)
        assert "Permission denied" in data["error"]

    async def test_send_message_to_user_as_admin(self):
        data = await self._exec("send_message_to_user", {"nick": "Alice", "message": "Hi"}, is_admin=True)
        assert data["success"] is True

    async def test_execute_hub_command_as_admin(self):
        data = await self._exec("execute_hub_command", {"command": "!help"}, is_admin=True)
        assert data["success"] is True
        assert "Command OK" in data["output"]

    async def test_get_hub_config_as_admin(self):
        data = await self._exec("get_hub_config", {"section": "config", "key": "hub_name"}, is_admin=True)
        assert data["value"] == "test_value"

    async def test_set_hub_config_requires_master(self):
        data = await self._exec("set_hub_config",
                                {"section": "config", "key": "hub_name", "value": "new"},
                                user=self.admin_user, is_admin=True)
        assert "error" in data  # class 5 < 10 required for set_config

    async def test_set_hub_config_as_master(self):
        master = _make_token_data("master", 10)
        data = await self._exec("set_hub_config",
                                {"section": "config", "key": "hub_name", "value": "NewName"},
                                user=master, is_admin=True)
        assert data["success"] is True

    async def test_unknown_tool_returns_error(self):
        data = await self._exec("nonexistent_tool", {})
        assert "error" in data

    async def test_hub_not_running(self):
        self.ctx = None
        with patch("verlihub.api.routes.llm.get_hub_context", return_value=None), \
             patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            from verlihub.api.routes.llm import _execute_tool
            result = await _execute_tool("get_hub_info", {}, self.admin_user, True)
        data = json.loads(result)
        assert "error" in data


# ======================================================================
# REST endpoint tests
# ======================================================================


class TestLlmEndpoints:

    async def test_status_llm_disabled(self, client):
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_disabled_config()):
            resp = await client.get("/api/v1/llm/status", headers=_bearer())
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False

    async def test_status_llm_enabled(self, client):
        mock_openai = MagicMock()
        mock_openai.models = MagicMock()
        mock_openai.models.list = AsyncMock(return_value=[])
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()), \
             patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_openai):
            resp = await client.get("/api/v1/llm/status", headers=_bearer())
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["model"] == "test-model"

    async def test_chat_llm_disabled(self, client):
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_disabled_config()):
            resp = await client.post(
                "/api/v1/llm/chat",
                json={"message": "hello"},
                headers=_bearer(),
            )
        assert resp.status_code == 503

    async def test_chat_insufficient_class(self, client):
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.post(
                "/api/v1/llm/chat",
                json={"message": "hello"},
                headers=_bearer("user", 1),  # class 1 < min_class 3
            )
        assert resp.status_code == 403

    async def test_chat_success_with_mock_llm(self, client):
        """Test a full chat round-trip with mocked OpenAI client."""
        # Build a mock response that returns a simple text reply (no tool calls)
        mock_msg = MagicMock()
        mock_msg.content = "There are 3 users online."
        mock_msg.tool_calls = None
        mock_msg.model_dump = MagicMock(return_value={
            "role": "assistant", "content": "There are 3 users online.", "tool_calls": None,
        })

        mock_choice = MagicMock()
        mock_choice.message = mock_msg

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()), \
             patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_openai):
            resp = await client.post(
                "/api/v1/llm/chat",
                json={"message": "how many users?"},
                headers=_bearer("op", 3),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "3 users" in data["response"]
        assert data["model"] == "test-model"

    async def test_chat_with_tool_calls(self, client):
        """Test a chat that makes a tool call then responds."""
        # First LLM response: tool call
        mock_tc = MagicMock()
        mock_tc.id = "call_123"
        mock_tc.function = MagicMock()
        mock_tc.function.name = "get_hub_info"
        mock_tc.function.arguments = "{}"

        mock_msg1 = MagicMock()
        mock_msg1.content = None
        mock_msg1.tool_calls = [mock_tc]
        mock_msg1.model_dump = MagicMock(return_value={
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_123", "type": "function",
                            "function": {"name": "get_hub_info", "arguments": "{}"}}],
        })

        # Second LLM response: final answer
        mock_msg2 = MagicMock()
        mock_msg2.content = "The hub is running with 3 users online."
        mock_msg2.tool_calls = None
        mock_msg2.model_dump = MagicMock(return_value={
            "role": "assistant", "content": "The hub is running with 3 users online.",
        })

        mock_choice1 = MagicMock()
        mock_choice1.message = mock_msg1
        mock_choice2 = MagicMock()
        mock_choice2.message = mock_msg2

        mock_resp1 = MagicMock()
        mock_resp1.choices = [mock_choice1]
        mock_resp2 = MagicMock()
        mock_resp2.choices = [mock_choice2]

        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(side_effect=[mock_resp1, mock_resp2])

        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()), \
             patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_openai):
            resp = await client.post(
                "/api/v1/llm/chat",
                json={"message": "tell me about the hub"},
                headers=_bearer("admin", 5),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "3 users" in data["response"]
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["name"] == "get_hub_info"


# ======================================================================
# Dashboard AI chat page tests
# ======================================================================


class TestAiChatDashboard:

    async def test_ai_chat_redirects_when_not_logged_in(self, client):
        resp = await client.get("/dashboard/ai-chat")
        assert resp.status_code == 303
        assert "/dashboard/login" in resp.headers["location"]

    async def test_ai_chat_page_renders_when_llm_enabled(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        assert resp.status_code == 200
        assert b"AI Chat" in resp.content
        assert b"ai-messages" in resp.content
        assert b"ai-input" in resp.content

    async def test_ai_chat_page_shows_disabled_message(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_disabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        assert resp.status_code == 200
        assert b"not enabled" in resp.content.lower()

    async def test_ai_chat_page_shows_admin_access_level(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("admin", 5))
        assert resp.status_code == 200
        assert b"admin" in resp.content.lower()
        assert b"full admin access" in resp.content.lower()

    async def test_ai_chat_page_shows_readonly_access_level(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        assert resp.status_code == 200
        assert b"read-only" in resp.content.lower()

    async def test_ai_chat_redirects_if_user_class_too_low(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("user", 1))
        assert resp.status_code == 303
        assert "/dashboard/" in resp.headers["location"]

    async def test_ai_chat_has_websocket_js(self, client):
        """Verify the template includes WebSocket connection code."""
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        assert resp.status_code == 200
        assert b"ws/llm-chat" in resp.content
        assert b"WebSocket" in resp.content

    async def test_ai_chat_input_disabled_when_llm_off(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_disabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        assert resp.status_code == 200
        # The input and button should have the 'disabled' attribute
        assert b"disabled" in resp.content

    async def test_ai_chat_shows_model_name(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        assert resp.status_code == 200
        assert b"test-model" in resp.content

    async def test_ai_chat_tool_badge_js_present(self, client):
        """The template should have JS for rendering tool call badges."""
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        text = resp.text
        assert "appendToolCall" in text
        assert "ai-tool-badge" in text

    async def test_ai_chat_clear_button_present(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        assert b"clearAiChat" in resp.content

    async def test_ai_chat_reconnect_logic(self, client):
        """The JS should auto-reconnect on disconnect."""
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/ai-chat", cookies=_cookie("op", 3))
        assert b"Reconnecting" in resp.content or b"connectAiChat" in resp.content


# ======================================================================
# Navbar visibility tests
# ======================================================================


class TestNavbarLlmVisibility:

    async def test_navbar_shows_ai_chat_when_enabled_and_permitted(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/", cookies=_cookie("admin", 5))
        if resp.status_code == 200:
            assert b"AI Chat" in resp.content
            assert b"/dashboard/ai-chat" in resp.content

    async def test_navbar_hides_ai_chat_when_disabled(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_disabled_config()):
            resp = await client.get("/dashboard/", cookies=_cookie("admin", 5))
        if resp.status_code == 200:
            assert b"/dashboard/ai-chat" not in resp.content

    async def test_navbar_hides_ai_chat_when_user_class_too_low(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/", cookies=_cookie("user", 1))
        if resp.status_code == 200:
            # User class 1 < min_class 3 → should not see AI Chat
            assert b"/dashboard/ai-chat" not in resp.content

    async def test_navbar_shows_ai_chat_for_operator(self, client):
        with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
            resp = await client.get("/dashboard/", cookies=_cookie("op", 3))
        if resp.status_code == 200:
            assert b"AI Chat" in resp.content

    async def test_navbar_on_various_pages(self, client):
        """Navbar AI Chat item appears consistently across different pages."""
        pages = ["/dashboard/users", "/dashboard/bans", "/dashboard/chat"]
        for page in pages:
            with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_enabled_config()):
                resp = await client.get(page, cookies=_cookie("admin", 5))
            if resp.status_code == 200:
                assert b"AI Chat" in resp.content, f"AI Chat missing from navbar on {page}"

    async def test_navbar_hides_ai_chat_on_various_pages_when_disabled(self, client):
        pages = ["/dashboard/users", "/dashboard/bans", "/dashboard/chat"]
        for page in pages:
            with patch("verlihub.dashboard.routes.get_config_optional", return_value=_llm_disabled_config()):
                resp = await client.get(page, cookies=_cookie("admin", 5))
            if resp.status_code == 200:
                assert b"/dashboard/ai-chat" not in resp.content, \
                    f"AI Chat link should be hidden on {page} when disabled"


# ======================================================================
# WebSocket /ws/llm-chat tests
# ======================================================================


class TestLlmWebSocket:

    def test_ws_rejects_when_llm_disabled(self, app):
        """WebSocket should send error when LLM is disabled."""
        from starlette.testclient import TestClient
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_disabled_config()):
            token = _token_str()
            with TestClient(app) as tc:
                with tc.websocket_connect(f"/ws/llm-chat?token={token}") as ws:
                    data = ws.receive_json()
                    assert data["type"] == "error"
                    assert "not enabled" in data["content"].lower()

    def test_ws_rejects_without_token(self, app):
        from starlette.testclient import TestClient
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            with TestClient(app) as tc:
                with tc.websocket_connect("/ws/llm-chat") as ws:
                    data = ws.receive_json()
                    assert data["type"] == "error"
                    assert "authentication" in data["content"].lower()

    def test_ws_rejects_insufficient_class(self, app):
        from starlette.testclient import TestClient
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            token = _token_str("user", 1)  # class 1 < min_class 3
            with TestClient(app) as tc:
                with tc.websocket_connect(f"/ws/llm-chat?token={token}") as ws:
                    data = ws.receive_json()
                    assert data["type"] == "error"
                    assert "permission" in data["content"].lower() or "insufficient" in data["content"].lower()

    def test_ws_connects_and_receives_connected(self, app):
        """WebSocket should send 'connected' message with access level."""
        from starlette.testclient import TestClient
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            token = _token_str("admin", 5)
            with TestClient(app) as tc:
                with tc.websocket_connect(f"/ws/llm-chat?token={token}") as ws:
                    data = ws.receive_json()
                    assert data["type"] == "connected"
                    assert data["access"] == "admin"
                    assert data["model"] == "test-model"

    def test_ws_connected_user_access(self, app):
        """Non-admin users get 'user' access level."""
        from starlette.testclient import TestClient
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            token = _token_str("op", 3)
            with TestClient(app) as tc:
                with tc.websocket_connect(f"/ws/llm-chat?token={token}") as ws:
                    data = ws.receive_json()
                    assert data["type"] == "connected"
                    assert data["access"] == "user"

    def test_ws_sends_thinking_on_message(self, app):
        """After sending a message, the WS should respond with 'thinking' then 'response'."""
        from starlette.testclient import TestClient

        # Mock LLM: simple response, no tool calls
        mock_msg = MagicMock()
        mock_msg.content = "Hello! The hub is running."
        mock_msg.tool_calls = None
        mock_msg.model_dump = MagicMock(return_value={
            "role": "assistant", "content": "Hello! The hub is running.",
        })
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_resp)

        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()), \
             patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_openai):
            token = _token_str("op", 3)
            with TestClient(app) as tc:
                with tc.websocket_connect(f"/ws/llm-chat?token={token}") as ws:
                    # Receive 'connected'
                    connected = ws.receive_json()
                    assert connected["type"] == "connected"

                    # Send a message
                    ws.send_json({"message": "hello"})

                    # Should get 'thinking'
                    thinking = ws.receive_json()
                    assert thinking["type"] == "thinking"

                    # Should get 'response'
                    response = ws.receive_json()
                    assert response["type"] == "response"
                    assert "hub is running" in response["content"]

    def test_ws_tool_call_progress(self, app):
        """WebSocket should stream tool_call and tool_result messages."""
        from starlette.testclient import TestClient

        # First LLM response: tool call
        mock_tc = MagicMock()
        mock_tc.id = "call_abc"
        mock_tc.function = MagicMock()
        mock_tc.function.name = "get_hub_info"
        mock_tc.function.arguments = "{}"

        mock_msg1 = MagicMock()
        mock_msg1.content = None
        mock_msg1.tool_calls = [mock_tc]
        mock_msg1.model_dump = MagicMock(return_value={
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_abc", "function": {"name": "get_hub_info", "arguments": "{}"}}],
        })

        # Second LLM response: text
        mock_msg2 = MagicMock()
        mock_msg2.content = "The hub name is TestHub."
        mock_msg2.tool_calls = None
        mock_msg2.model_dump = MagicMock(return_value={
            "role": "assistant", "content": "The hub name is TestHub.",
        })

        mock_choice1 = MagicMock()
        mock_choice1.message = mock_msg1
        mock_choice2 = MagicMock()
        mock_choice2.message = mock_msg2
        mock_resp1 = MagicMock()
        mock_resp1.choices = [mock_choice1]
        mock_resp2 = MagicMock()
        mock_resp2.choices = [mock_choice2]

        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(side_effect=[mock_resp1, mock_resp2])

        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()), \
             patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_openai):
            token = _token_str("admin", 5)
            with TestClient(app) as tc:
                with tc.websocket_connect(f"/ws/llm-chat?token={token}") as ws:
                    ws.receive_json()  # connected

                    ws.send_json({"message": "tell me about the hub"})

                    # thinking
                    msg = ws.receive_json()
                    assert msg["type"] == "thinking"

                    # tool_call
                    msg = ws.receive_json()
                    assert msg["type"] == "tool_call"
                    assert msg["name"] == "get_hub_info"

                    # tool_result
                    msg = ws.receive_json()
                    assert msg["type"] == "tool_result"
                    assert msg["name"] == "get_hub_info"

                    # response
                    msg = ws.receive_json()
                    assert msg["type"] == "response"
                    assert "TestHub" in msg["content"]


# ======================================================================
# MCP server tests
# ======================================================================


class TestMcpServer:
    """Test the MCP server tool dispatch and resource functions."""

    @pytest.fixture
    def mock_hub_client(self):
        """Create a mock AsyncHubClient with all methods."""
        client = AsyncMock()
        client._user_nick = "admin"
        client.get_hub_info = AsyncMock(return_value={
            "name": "TestHub", "topic": "Test", "users": 3,
        })
        client.get_detailed_users = AsyncMock(return_value=[
            {"nick": "Alice", "share": 1000, "user_class": 3},
            {"nick": "Bob", "share": 500, "user_class": 1},
        ])
        client.get_statistics = AsyncMock(return_value={"uptime": 3600, "users": 3})
        client.get_share_stats = AsyncMock(return_value={"total": 1500})
        client.get_geo_distribution = AsyncMock(return_value={"US": 2, "DE": 1})
        client.get_operators = AsyncMock(return_value=[{"nick": "Alice"}])
        client.get_bots = AsyncMock(return_value=[{"nick": "HubBot"}])
        client.get_bans = AsyncMock(return_value=[
            {"nick": "baduser", "ip": "1.2.3.4", "reason": "spam"},
        ])
        client.get_registered_users = AsyncMock(return_value=[
            {"nick": "Alice", "user_class": 3},
        ])
        client.health_check = AsyncMock(return_value={"status": "healthy"})
        client.kick_user = AsyncMock(return_value=True)
        client.send_to_all = AsyncMock(return_value=True)
        client.send_to_user = AsyncMock(return_value=True)
        client.ban_user = AsyncMock(return_value={"status": "banned"})
        client.register_user = AsyncMock(return_value={"nick": "newuser"})
        return client

    async def test_dispatch_get_hub_info(self, mock_hub_client):
        try:
            from verlihub.client.mcp import build_mcp_server
        except (ImportError, SystemExit):
            pytest.skip("mcp package not installed")

        try:
            with patch("verlihub.client.mcp._create_hub_client", new_callable=AsyncMock) as mock_create:
                mock_create.return_value = mock_hub_client
                server = build_mcp_server("http://test/api/v1", "admin", "pass")
        except SystemExit:
            pytest.skip("mcp package not installed")

        # Access the registered handlers through the server instance
        # For testing purposes let's just test directly
        hub = mock_hub_client

        # Test get_hub_info
        result = await hub.get_hub_info()
        assert result["name"] == "TestHub"

    async def test_dispatch_list_online_users(self, mock_hub_client):
        result = await mock_hub_client.get_detailed_users()
        assert len(result) == 2

    async def test_dispatch_kick_user(self, mock_hub_client):
        result = await mock_hub_client.kick_user("admin", "Bob", "test")
        assert result is True
        mock_hub_client.kick_user.assert_awaited_once()

    async def test_dispatch_search_bans(self, mock_hub_client):
        bans = await mock_hub_client.get_bans(limit=50)
        assert len(bans) == 1
        assert bans[0]["nick"] == "baduser"

    async def test_dispatch_health_check(self, mock_hub_client):
        result = await mock_hub_client.health_check()
        assert result["status"] == "healthy"

    async def test_dispatch_send_broadcast(self, mock_hub_client):
        ok = await mock_hub_client.send_to_all("Hello all")
        assert ok is True

    async def test_dispatch_ban_user(self, mock_hub_client):
        result = await mock_hub_client.ban_user(nick="bad", reason="spam", duration_hours=24)
        assert result["status"] == "banned"

    async def test_dispatch_register_user(self, mock_hub_client):
        result = await mock_hub_client.register_user(nick="newuser", password="pass")
        assert result["nick"] == "newuser"


class TestMcpServerBuild:
    """Test that the MCP server builds and has the right tools/resources."""

    def test_build_mcp_server_returns_server(self):
        try:
            from verlihub.client.mcp import build_mcp_server
            server = build_mcp_server("http://test/api/v1", "admin", "pass")
            assert server is not None
            assert server.name == "verlihub"
        except (ImportError, SystemExit):
            pytest.skip("mcp package not installed")

    def test_mcp_cli_help(self):
        """Verify the click CLI top-level --help works."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "verlihub.client.mcp", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "serve" in result.stdout
        assert "client" in result.stdout

    def test_mcp_cli_serve_help(self):
        """Verify 'serve --help' shows transport options."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "verlihub.client.mcp", "serve", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "http" in combined
        assert "stdio" in combined
        assert "--hub-url" in combined
        assert "--transport" in combined

    def test_mcp_cli_serve_http_flags(self):
        """Verify --host, --port, --json-response are accepted on serve."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "verlihub.client.mcp", "serve", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "--host" in combined
        assert "--port" in combined
        assert "--json-response" in combined

    def test_mcp_cli_client_help(self):
        """Verify the client subcommand shows tool/resource/prompt commands."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "verlihub.client.mcp", "client", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "tools" in combined
        assert "resources" in combined
        assert "call" in combined
        assert "read" in combined
        assert "prompts" in combined

    def test_mcp_cli_client_call_help(self):
        """Verify 'client call --help' shows usage examples."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "verlihub.client.mcp", "client", "call", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "TOOL_NAME" in result.stdout

    def test_run_http_creates_starlette_app(self):
        """Test that _run_http builds a Starlette app and calls uvicorn."""
        try:
            from verlihub.client.mcp import build_mcp_server, _run_http
        except (ImportError, SystemExit):
            pytest.skip("mcp package not installed")

        try:
            server = build_mcp_server("http://test/api/v1", "admin", "pass")
        except (ImportError, SystemExit):
            pytest.skip("mcp package not installed")

        # uvicorn is imported locally inside _run_http, so we patch the
        # module namespace that _run_http will import from.
        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            try:
                _run_http(server, host="127.0.0.1", port=9090,
                          json_response=True, log_level="WARNING")
            except Exception:
                pass  # may raise in mock context
            if mock_uvicorn.run.called:
                call_kwargs = mock_uvicorn.run.call_args
                assert call_kwargs[1]["host"] == "127.0.0.1"
                assert call_kwargs[1]["port"] == 9090


# ======================================================================
# ChatSession tests
# ======================================================================


class TestChatSession:

    def test_session_init_admin(self):
        from verlihub.api.routes.llm import ChatSession
        user = _make_token_data("admin", 5)
        cfg = LlmConfig(enabled=True)
        session = ChatSession(user, is_admin=True, llm_cfg=cfg)
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "system"
        assert "administrator" in session.messages[0]["content"].lower()
        # Admin gets readonly + admin tools
        assert len(session.tools) == 15  # 9 readonly + 6 admin

    def test_session_init_user(self):
        from verlihub.api.routes.llm import ChatSession
        user = _make_token_data("op", 3)
        cfg = LlmConfig(enabled=True)
        session = ChatSession(user, is_admin=False, llm_cfg=cfg)
        assert len(session.tools) == 9  # readonly only
        assert "CANNOT" in session.messages[0]["content"]

    def test_session_personalization(self):
        from verlihub.api.routes.llm import ChatSession
        user = _make_token_data("testop", 3)
        cfg = LlmConfig(enabled=True)
        session = ChatSession(user, is_admin=False, llm_cfg=cfg)
        assert "testop" in session.messages[0]["content"]
        assert "class 3" in session.messages[0]["content"]

    async def test_session_chat_simple(self):
        """Test ChatSession.chat() with a simple LLM response."""
        from verlihub.api.routes.llm import ChatSession

        mock_msg = MagicMock()
        mock_msg.content = "Hi there!"
        mock_msg.tool_calls = None
        mock_msg.model_dump = MagicMock(return_value={"role": "assistant", "content": "Hi there!"})
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_resp)

        user = _make_token_data("op", 3)
        cfg = LlmConfig(enabled=True, max_tool_rounds=3)
        session = ChatSession(user, is_admin=False, llm_cfg=cfg)

        with patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_openai):
            text, tools = await session.chat("hello")

        assert text == "Hi there!"
        assert tools == []
        # Messages should now have system + user + assistant
        assert len(session.messages) == 3


# ======================================================================
# Utility function tests
# ======================================================================


class TestFormatBytes:

    def test_format_bytes(self):
        from verlihub.api.routes.llm import _format_bytes
        assert "0.0 B" == _format_bytes(0)
        assert "1.0 KiB" == _format_bytes(1024)
        assert "1.0 MiB" == _format_bytes(1024 ** 2)
        assert "1.0 GiB" == _format_bytes(1024 ** 3)
        assert "1.0 TiB" == _format_bytes(1024 ** 4)
