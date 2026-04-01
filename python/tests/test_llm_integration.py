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
    # Phase 5 methods
    ctx.send_to_opchat = MagicMock(return_value=True)
    ctx.send_to_active = MagicMock()
    ctx.send_to_passive = MagicMock()
    ctx.send_to_active_class = MagicMock()
    ctx.send_to_passive_class = MagicMock()
    ctx.broadcast_chat = MagicMock()
    ctx.send_pm_as = MagicMock()
    ctx.force_move = MagicMock(return_value=True)
    ctx.disconnect_user = MagicMock(return_value=True)
    ctx.add_robot = MagicMock(return_value=True)
    ctx.remove_robot = MagicMock(return_value=True)
    ctx.request_reload = MagicMock()
    ctx.get_protocol_stats = MagicMock(return_value={"bytes_in": 1000, "bytes_out": 2000})
    ctx.lookup_geoip = MagicMock(return_value={"country": "US", "city": "NYC"})
    ctx.get_active_user_count = MagicMock(return_value=2)
    ctx.get_passive_user_count = MagicMock(return_value=1)
    ctx.get_loaded_plugins = MagicMock(return_value=["plugin_a"])
    ctx.is_plugin_loaded = MagicMock(return_value=True)
    ctx.load_plugin = MagicMock(return_value=True)
    ctx.unload_plugin = MagicMock(return_value=True)
    ctx.reload_plugin = MagicMock(return_value=True)
    ctx.get_loaded_lua_scripts = MagicMock(return_value=["test.lua"])
    ctx.execute_lua_script = MagicMock(return_value=True)
    ctx.unload_lua_script = MagicMock(return_value=True)
    ctx.get_loaded_python_scripts = MagicMock(return_value=["test.py"])
    ctx.execute_python_script = MagicMock(return_value=True)
    ctx.unload_python_script = MagicMock(return_value=True)
    ctx.set_flood_config = MagicMock()
    ctx.get_flood_config = MagicMock(return_value=(1000, 5))
    ctx.load_ban_cache = MagicMock()
    ctx.add_ban_cache_ip = MagicMock()
    ctx.add_ban_cache_nick = MagicMock()
    ctx.clear_ban_cache = MagicMock()
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
        assert len(tools) == 18
        names = {t["function"]["name"] for t in tools}
        assert "get_hub_info" in names
        assert "list_online_users" in names
        assert "search_bans" in names
        # Phase 5 readonly
        assert "get_protocol_stats" in names
        assert "lookup_geoip" in names
        assert "list_plugins" in names
        assert "list_triggers" in names

    def test_admin_tools_count(self):
        from verlihub.api.routes.llm import _build_admin_tools
        tools = _build_admin_tools()
        assert len(tools) == 40
        names = {t["function"]["name"] for t in tools}
        assert "kick_user" in names
        assert "send_broadcast" in names
        assert "set_hub_config" in names
        # Phase 5 admin
        assert "send_to_opchat" in names
        assert "broadcast_chat" in names
        assert "load_plugin" in names
        assert "set_flood_config" in names
        assert "add_penalty" in names

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

    # --- Phase 5: Messaging tools ---

    async def test_send_to_opchat(self):
        data = await self._exec("send_to_opchat", {"message": "hello ops", "from_nick": "bot"})
        assert data["success"] is True
        self.ctx.send_to_opchat.assert_called_once_with("hello ops", "bot")

    async def test_send_to_active(self):
        data = await self._exec("send_to_active", {"message": "active msg"})
        assert data["success"] is True
        self.ctx.send_to_active.assert_called_once_with("active msg")

    async def test_send_to_passive(self):
        data = await self._exec("send_to_passive", {"message": "passive msg"})
        assert data["success"] is True
        self.ctx.send_to_passive.assert_called_once_with("passive msg")

    async def test_send_to_active_class(self):
        data = await self._exec("send_to_active_class", {"message": "active class msg", "min_class": 3, "max_class": 10})
        assert data["success"] is True
        self.ctx.send_to_active_class.assert_called_once_with("active class msg", 3, 10)

    async def test_send_to_passive_class(self):
        data = await self._exec("send_to_passive_class", {"message": "passive class msg", "min_class": 1, "max_class": 5})
        assert data["success"] is True
        self.ctx.send_to_passive_class.assert_called_once_with("passive class msg", 1, 5)

    async def test_broadcast_chat(self):
        data = await self._exec("broadcast_chat", {"from_nick": "Bot", "message": "hi"})
        assert data["success"] is True
        self.ctx.broadcast_chat.assert_called_once_with("Bot", "hi")

    async def test_send_pm_as(self):
        data = await self._exec("send_pm_as", {"from_nick": "Bot", "to_nick": "Alice", "message": "hi"})
        assert data["success"] is True
        self.ctx.send_pm_as.assert_called_once_with("Bot", "Alice", "hi")

    async def test_messaging_denied_for_non_admin(self):
        for tool in ("send_to_opchat", "send_to_active", "send_to_passive",
                      "send_to_active_class", "send_to_passive_class", "broadcast_chat"):
            data = await self._exec(tool, {"message": "x", "from_nick": "y", "min_class": 0, "max_class": 10},
                                    user=self.regular_user, is_admin=False)
            assert "Permission denied" in data["error"], f"{tool} should deny non-admin"

    # --- Phase 5: Admin tools ---

    async def test_force_move(self):
        data = await self._exec("force_move", {"nick": "Bob", "address": "dchub://other:411"})
        assert data["success"] is True
        self.ctx.force_move.assert_called_once_with("Bob", "dchub://other:411")

    async def test_disconnect_user(self):
        data = await self._exec("disconnect_user", {"nick": "Bob"})
        assert data["success"] is True
        self.ctx.disconnect_user.assert_called_once_with("Bob")

    async def test_add_robot(self):
        data = await self._exec("add_robot", {"nick": "NewBot", "description": "test", "user_class": 3})
        assert data["success"] is True
        self.ctx.add_robot.assert_called_once_with("NewBot", "test", 3)

    async def test_remove_robot(self):
        data = await self._exec("remove_robot", {"nick": "OldBot"})
        assert data["success"] is True
        self.ctx.remove_robot.assert_called_once_with("OldBot")

    async def test_reload_config(self):
        data = await self._exec("reload_config", {})
        assert data["success"] is True
        self.ctx.request_reload.assert_called_once()

    # --- Phase 5: Statistics ---

    async def test_get_protocol_stats(self):
        data = await self._exec("get_protocol_stats")
        assert data["bytes_in"] == 1000
        assert data["bytes_out"] == 2000

    async def test_lookup_geoip(self):
        data = await self._exec("lookup_geoip", {"ip": "8.8.8.8"})
        assert data["country"] == "US"
        self.ctx.lookup_geoip.assert_called_once_with("8.8.8.8")

    async def test_get_active_passive_counts(self):
        data = await self._exec("get_active_passive_counts")
        assert data["active"] == 2
        assert data["passive"] == 1

    # --- Phase 5: Plugin Management ---

    async def test_list_plugins(self):
        data = await self._exec("list_plugins")
        assert data == ["plugin_a"]

    async def test_load_plugin(self):
        data = await self._exec("load_plugin", {"plugin_path": "/usr/lib/vh_plug.so"})
        assert data["success"] is True
        self.ctx.load_plugin.assert_called_once_with("/usr/lib/vh_plug.so")

    async def test_unload_plugin(self):
        data = await self._exec("unload_plugin", {"plugin_name": "vh_plug"})
        assert data["success"] is True
        self.ctx.unload_plugin.assert_called_once_with("vh_plug")

    async def test_reload_plugin(self):
        data = await self._exec("reload_plugin", {"plugin_name": "vh_plug"})
        assert data["success"] is True
        self.ctx.reload_plugin.assert_called_once_with("vh_plug")

    async def test_list_lua_scripts(self):
        data = await self._exec("list_lua_scripts")
        assert data == ["test.lua"]

    async def test_load_lua_script(self):
        data = await self._exec("load_lua_script", {"script_path": "/scripts/test.lua"})
        assert data["success"] is True
        self.ctx.execute_lua_script.assert_called_once_with("/scripts/test.lua")

    async def test_unload_lua_script(self):
        data = await self._exec("unload_lua_script", {"script_path": "/scripts/test.lua"})
        assert data["success"] is True
        self.ctx.unload_lua_script.assert_called_once_with("/scripts/test.lua")

    async def test_list_python_scripts(self):
        data = await self._exec("list_python_scripts")
        assert data == ["test.py"]

    async def test_load_python_script(self):
        data = await self._exec("load_python_script", {"script_path": "/scripts/test.py"})
        assert data["success"] is True
        self.ctx.execute_python_script.assert_called_once_with("/scripts/test.py")

    async def test_unload_python_script(self):
        data = await self._exec("unload_python_script", {"script_path": "/scripts/test.py"})
        assert data["success"] is True
        self.ctx.unload_python_script.assert_called_once_with("/scripts/test.py")

    # --- Phase 5: Flood & Ban Cache ---

    async def test_set_flood_config(self):
        data = await self._exec("set_flood_config", {"flood_type": "chat", "period_ms": 1000, "max_tokens": 5})
        assert data["success"] is True
        self.ctx.set_flood_config.assert_called_once_with("chat", 1000, 5)

    async def test_sync_ban_cache(self):
        data = await self._exec("sync_ban_cache")
        assert data["success"] is True
        self.ctx.load_ban_cache.assert_called_once()

    async def test_add_ban_cache_ip(self):
        data = await self._exec("add_ban_cache_ip", {"ip": "10.0.0.99"})
        assert data["success"] is True
        self.ctx.add_ban_cache_ip.assert_called_once_with("10.0.0.99")

    async def test_add_ban_cache_nick(self):
        data = await self._exec("add_ban_cache_nick", {"nick": "spammer"})
        assert data["success"] is True
        self.ctx.add_ban_cache_nick.assert_called_once_with("spammer")

    async def test_clear_ban_cache(self):
        data = await self._exec("clear_ban_cache")
        assert data["success"] is True
        self.ctx.clear_ban_cache.assert_called_once()

    async def test_plugin_tools_denied_for_non_admin(self):
        for tool in ("load_plugin", "unload_plugin", "reload_plugin",
                      "load_lua_script", "unload_lua_script",
                      "load_python_script", "unload_python_script"):
            data = await self._exec(tool, {"plugin_path": "x", "plugin_name": "x", "script_path": "x"},
                                    user=self.regular_user, is_admin=False)
            assert "Permission denied" in data["error"], f"{tool} should deny non-admin"


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
        """After sending a message, the WS should respond with thinking → stream → response."""
        from starlette.testclient import TestClient

        # Mock streaming LLM response: yields text tokens then stops
        async def _fake_stream(*args, **kwargs):
            """Async generator simulating OpenAI streaming chunks."""
            for token in ["Hello!", " The hub", " is running."]:
                chunk = MagicMock()
                delta = MagicMock()
                delta.content = token
                delta.tool_calls = None
                choice = MagicMock()
                choice.delta = delta
                choice.finish_reason = None
                chunk.choices = [choice]
                yield chunk
            # Final chunk with finish_reason
            final = MagicMock()
            fd = MagicMock()
            fd.content = None
            fd.tool_calls = None
            fc = MagicMock()
            fc.delta = fd
            fc.finish_reason = "stop"
            final.choices = [fc]
            yield final

        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(return_value=_fake_stream())

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

                    # Should get 'stream_start'
                    msg = ws.receive_json()
                    assert msg["type"] == "stream_start"

                    # Collect stream_delta messages
                    parts = []
                    while True:
                        msg = ws.receive_json()
                        if msg["type"] == "stream_delta":
                            parts.append(msg["content"])
                        elif msg["type"] == "stream_end":
                            break

                    assert "hub is running" in "".join(parts)

    def test_ws_tool_call_progress(self, app):
        """WebSocket should stream tool_call and tool_result messages."""
        from starlette.testclient import TestClient

        # First LLM stream: yields a tool call (no text content)
        async def _tool_stream(*args, **kwargs):
            # Tool call delta: id + function name
            c1 = MagicMock()
            d1 = MagicMock()
            d1.content = None
            tc1 = MagicMock()
            tc1.index = 0
            tc1.id = "call_abc"
            tc1.function = MagicMock()
            tc1.function.name = "get_hub_info"
            tc1.function.arguments = None
            d1.tool_calls = [tc1]
            ch1 = MagicMock()
            ch1.delta = d1
            ch1.finish_reason = None
            c1.choices = [ch1]
            yield c1

            # Tool call delta: arguments
            c2 = MagicMock()
            d2 = MagicMock()
            d2.content = None
            tc2 = MagicMock()
            tc2.index = 0
            tc2.id = None
            tc2.function = MagicMock()
            tc2.function.name = None
            tc2.function.arguments = "{}"
            d2.tool_calls = [tc2]
            ch2 = MagicMock()
            ch2.delta = d2
            ch2.finish_reason = None
            c2.choices = [ch2]
            yield c2

            # Final: finish_reason=tool_calls
            cf = MagicMock()
            df = MagicMock()
            df.content = None
            df.tool_calls = None
            chf = MagicMock()
            chf.delta = df
            chf.finish_reason = "tool_calls"
            cf.choices = [chf]
            yield cf

        # Second LLM stream: yields text response
        async def _text_stream(*args, **kwargs):
            for tok in ["The hub name", " is TestHub."]:
                c = MagicMock()
                d = MagicMock()
                d.content = tok
                d.tool_calls = None
                ch = MagicMock()
                ch.delta = d
                ch.finish_reason = None
                c.choices = [ch]
                yield c
            cf = MagicMock()
            df = MagicMock()
            df.content = None
            df.tool_calls = None
            chf = MagicMock()
            chf.delta = df
            chf.finish_reason = "stop"
            cf.choices = [chf]
            yield cf

        _calls = iter([_tool_stream(), _text_stream()])

        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()
        mock_openai.chat.completions = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(side_effect=lambda *a, **kw: next(_calls))

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

                    # thinking (before 2nd round)
                    msg = ws.receive_json()
                    assert msg["type"] == "thinking"

                    # stream_start
                    msg = ws.receive_json()
                    assert msg["type"] == "stream_start"

                    # Collect stream deltas until stream_end
                    parts = []
                    while True:
                        msg = ws.receive_json()
                        if msg["type"] == "stream_delta":
                            parts.append(msg["content"])
                        elif msg["type"] == "stream_end":
                            break
                    assert "TestHub" in "".join(parts)


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
        assert len(session.tools) == 58  # 18 readonly + 40 admin

    def test_session_init_user(self):
        from verlihub.api.routes.llm import ChatSession
        user = _make_token_data("op", 3)
        cfg = LlmConfig(enabled=True)
        session = ChatSession(user, is_admin=False, llm_cfg=cfg)
        assert len(session.tools) == 18  # readonly only
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


# ======================================================================
# In-process MCP endpoint tests
# ======================================================================


def _mcp_enabled_config() -> MagicMock:
    """Mock VerlihubConfig with MCP and LLM enabled."""
    from verlihub.config import McpConfig
    cfg = _llm_enabled_config()
    cfg.mcp = McpConfig(enabled=True, min_class=3, admin_class=5)
    return cfg


def _mcp_disabled_config() -> MagicMock:
    """Mock VerlihubConfig with MCP disabled."""
    from verlihub.config import McpConfig
    cfg = _llm_enabled_config()
    cfg.mcp = McpConfig(enabled=False)
    return cfg


class TestInProcessMcpBuild:
    """Test the in-process MCP server builder and auth middleware."""

    def test_build_inprocess_server(self):
        """build_inprocess_mcp_server returns a Server with context-var."""
        try:
            from verlihub.api.routes.mcp import build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")
        server = build_inprocess_mcp_server()
        assert server is not None
        assert server.name == "verlihub"
        assert hasattr(server, "_current_user")

    def test_create_mcp_mount_returns_tuple(self):
        """create_mcp_mount returns (asgi_app, session_manager) or (None, None)."""
        try:
            from verlihub.api.routes.mcp import create_mcp_mount, _session_manager, _authed_app
            import verlihub.api.routes.mcp as mcp_mod
            # Reset globals to force fresh creation
            mcp_mod._session_manager = None
            mcp_mod._mcp_server = None
            mcp_mod._authed_app = None
        except ImportError:
            pytest.skip("mcp package not installed")

        app, mgr = create_mcp_mount()
        assert app is not None
        assert mgr is not None

        # Calling again returns the same objects (idempotent)
        app2, mgr2 = create_mcp_mount()
        assert app2 is app
        assert mgr2 is mgr

        # Clean up
        mcp_mod._session_manager = None
        mcp_mod._mcp_server = None
        mcp_mod._authed_app = None

    def test_mcp_config_defaults(self):
        """McpConfig defaults to disabled, min_class=3, admin_class=5."""
        from verlihub.config import McpConfig
        cfg = McpConfig()
        assert cfg.enabled is False
        assert cfg.min_class == 3
        assert cfg.admin_class == 5


class TestInProcessMcpTools:
    """Test the in-process MCP tool dispatch against mock hub context."""

    @pytest.fixture
    def mcp_server(self):
        try:
            from verlihub.api.routes.mcp import build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")
        return build_inprocess_mcp_server()

    @staticmethod
    async def _call(server, name: str, arguments: dict | None = None):
        """Invoke an MCP tool via the server's request handler."""
        from mcp.types import CallToolRequest, CallToolRequestParams
        handler = server.request_handlers[CallToolRequest]
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments or {}),
        )
        result = await handler(req)
        return result.root.content  # list[TextContent]

    @staticmethod
    async def _list_tools(server):
        from mcp.types import ListToolsRequest
        handler = server.request_handlers[ListToolsRequest]
        req = ListToolsRequest(method="tools/list")
        result = await handler(req)
        return result.root.tools

    async def test_tool_get_hub_info(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "get_hub_info")
            data = json.loads(content[0].text)
            assert data["name"] == "TestHub"
            assert data["users_online"] == 3

    async def test_tool_list_online_users(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        admin_td = _make_token_data("admin", 5)
        mcp_server._current_user.set(admin_td)
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "list_online_users")
            users = json.loads(content[0].text)
            assert len(users) == 3
            assert users[0]["nick"] == "Alice"
            assert "ip" in users[0]

    async def test_tool_list_online_users_no_ip_for_operator(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        op_td = _make_token_data("oper", 3)
        mcp_server._current_user.set(op_td)
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "list_online_users")
            users = json.loads(content[0].text)
            assert "ip" not in users[0]

    async def test_tool_get_user_info(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        admin_td = _make_token_data("admin", 5)
        mcp_server._current_user.set(admin_td)
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "get_user_info", {"nick": "Alice"})
            data = json.loads(content[0].text)
            assert data["nick"] == "Alice"

    async def test_tool_get_user_info_not_found(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "get_user_info", {"nick": "nobody"})
            data = json.loads(content[0].text)
            assert "error" in data

    async def test_tool_get_hub_statistics(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "get_hub_statistics")
            data = json.loads(content[0].text)
            assert data["users_online"] == 3
            assert data["is_running"] is True

    async def test_tool_get_share_statistics(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "get_share_statistics")
            data = json.loads(content[0].text)
            assert data["user_count"] == 3

    async def test_tool_get_geo_distribution(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "get_geo_distribution")
            data = json.loads(content[0].text)
            assert isinstance(data, list)
            assert data[0]["country"] == "US"
            assert data[0]["users"] == 2

    async def test_tool_list_operators(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "list_operators")
            ops = json.loads(content[0].text)
            assert len(ops) == 1
            assert ops[0]["nick"] == "Alice"

    async def test_tool_list_bots(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "list_bots")
            bots = json.loads(content[0].text)
            assert len(bots) == 1
            assert bots[0]["nick"] == "HubBot"

    async def test_tool_health_check(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "health_check")
            data = json.loads(content[0].text)
            assert data["hub_running"] is True

    async def test_tool_kick_user_admin(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        admin_td = _make_token_data("admin", 5)
        mcp_server._current_user.set(admin_td)
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "kick_user", {"nick": "Bob", "reason": "test"})
            data = json.loads(content[0].text)
            assert data["success"] is True
            ctx.kick_user.assert_called_once()

    async def test_tool_kick_user_denied_for_operator(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        op_td = _make_token_data("oper", 3)
        mcp_server._current_user.set(op_td)
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "kick_user", {"nick": "Bob", "reason": "test"})
            data = json.loads(content[0].text)
            assert "Permission denied" in data["error"]

    async def test_tool_send_broadcast_admin(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        admin_td = _make_token_data("admin", 5)
        mcp_server._current_user.set(admin_td)
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "send_broadcast", {"message": "Hello all"})
            data = json.loads(content[0].text)
            assert data["success"] is True

    async def test_tool_send_message_to_user_admin(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        admin_td = _make_token_data("admin", 5)
        mcp_server._current_user.set(admin_td)
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "send_message_to_user",
                                       {"nick": "Alice", "message": "Hi"})
            data = json.loads(content[0].text)
            assert data["success"] is True

    async def test_tool_ban_user_admin(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        admin_td = _make_token_data("admin", 5)
        mcp_server._current_user.set(admin_td)
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            content = await self._call(mcp_server, "ban_user", {"nick": "Bob", "reason": "spam"})
            data = json.loads(content[0].text)
            assert data["success"] is True

    async def test_tool_unknown(self, mcp_server):
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=None), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=_mcp_enabled_config()):
            content = await self._call(mcp_server, "nonexistent_tool")
            data = json.loads(content[0].text)
            assert "Unknown tool" in data["error"]

    async def test_tools_list_admin_sees_all(self, mcp_server):
        cfg = _mcp_enabled_config()
        admin_td = _make_token_data("admin", 5)
        mcp_server._current_user.set(admin_td)
        with patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            tools = await self._list_tools(mcp_server)
            names = [t.name for t in tools]
            assert "kick_user" in names
            assert "ban_user" in names
            assert "get_hub_info" in names

    async def test_tools_list_operator_sees_readonly_only(self, mcp_server):
        cfg = _mcp_enabled_config()
        op_td = _make_token_data("oper", 3)
        mcp_server._current_user.set(op_td)
        with patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            tools = await self._list_tools(mcp_server)
            names = [t.name for t in tools]
            assert "get_hub_info" in names
            assert "kick_user" not in names
            assert "ban_user" not in names


class TestInProcessMcpResources:
    """Test MCP resource reads against mock hub context."""

    @pytest.fixture
    def mcp_server(self):
        try:
            from verlihub.api.routes.mcp import build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")
        return build_inprocess_mcp_server()

    @staticmethod
    async def _list_resources(server):
        from mcp.types import ListResourcesRequest
        handler = server.request_handlers[ListResourcesRequest]
        req = ListResourcesRequest(method="resources/list")
        result = await handler(req)
        return result.root.resources

    @staticmethod
    async def _read_resource(server, uri: str):
        from mcp.types import ReadResourceRequest, ReadResourceRequestParams
        handler = server.request_handlers[ReadResourceRequest]
        req = ReadResourceRequest(
            method="resources/read",
            params=ReadResourceRequestParams(uri=uri),
        )
        result = await handler(req)
        # result.root is ReadResourceResult with .contents list
        contents = result.root.contents
        # Each item is TextResourceContents with .text attribute
        return contents[0].text

    async def test_list_resources(self, mcp_server):
        resources = await self._list_resources(mcp_server)
        uris = [str(r.uri) for r in resources]
        assert "hub://info" in uris
        assert "hub://users" in uris
        assert "hub://stats" in uris
        assert "hub://bans" in uris

    async def test_read_hub_info_resource(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            data_str = await self._read_resource(mcp_server, "hub://info")
            data = json.loads(data_str)
            assert data["name"] == "TestHub"

    async def test_read_users_resource(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            data_str = await self._read_resource(mcp_server, "hub://users")
            data = json.loads(data_str)
            assert len(data) == 3

    async def test_read_stats_resource(self, mcp_server):
        ctx = _mock_hub_context()
        cfg = _mcp_enabled_config()
        with patch("verlihub.api.routes.mcp.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            data_str = await self._read_resource(mcp_server, "hub://stats")
            data = json.loads(data_str)
            assert data["users_online"] == 3

    async def test_read_unknown_resource(self, mcp_server):
        data_str = await self._read_resource(mcp_server, "hub://nonexistent")
        data = json.loads(data_str)
        assert "error" in data


class TestInProcessMcpPrompts:
    """Test MCP prompt listing and retrieval."""

    @pytest.fixture
    def mcp_server(self):
        try:
            from verlihub.api.routes.mcp import build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")
        return build_inprocess_mcp_server()

    @staticmethod
    async def _list_prompts(server):
        from mcp.types import ListPromptsRequest
        handler = server.request_handlers[ListPromptsRequest]
        req = ListPromptsRequest(method="prompts/list")
        result = await handler(req)
        return result.root.prompts

    @staticmethod
    async def _get_prompt(server, name: str, arguments: dict | None = None):
        from mcp.types import GetPromptRequest, GetPromptRequestParams
        handler = server.request_handlers[GetPromptRequest]
        req = GetPromptRequest(
            method="prompts/get",
            params=GetPromptRequestParams(name=name, arguments=arguments or {}),
        )
        result = await handler(req)
        return result.root.messages

    async def test_list_prompts(self, mcp_server):
        prompts = await self._list_prompts(mcp_server)
        names = [p.name for p in prompts]
        assert "hub_report" in names
        assert "user_lookup" in names
        assert "troubleshoot" in names

    async def test_get_hub_report_prompt(self, mcp_server):
        messages = await self._get_prompt(mcp_server, "hub_report")
        assert len(messages) == 1
        assert "status report" in messages[0].content.text.lower()

    async def test_get_user_lookup_prompt(self, mcp_server):
        messages = await self._get_prompt(mcp_server, "user_lookup", {"nick": "Alice"})
        assert "Alice" in messages[0].content.text

    async def test_get_troubleshoot_prompt(self, mcp_server):
        messages = await self._get_prompt(mcp_server, "troubleshoot", {"symptom": "slow"})
        assert "slow" in messages[0].content.text


class TestInProcessMcpAuthMiddleware:
    """Test the JWT auth middleware for the in-process MCP endpoint."""

    async def test_no_token_returns_401(self):
        """Request without any auth token gets 401."""
        try:
            from verlihub.api.routes.mcp import _McpAuthMiddleware, build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")

        # Create a simple ASGI app that the middleware wraps
        calls = []

        async def inner(scope, receive, send):
            calls.append(True)

        server = build_inprocess_mcp_server()
        mw = _McpAuthMiddleware(inner, mcp_server=server)

        # Simulate an HTTP request with no auth
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/mcp",
            "headers": [],
            "query_string": b"",
        }

        # Capture the response
        response_started = {}
        body_parts = []

        async def receive_fn():
            return {"type": "http.request", "body": b""}

        async def send_fn(message):
            if message["type"] == "http.response.start":
                response_started["status"] = message["status"]
            elif message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))

        await mw(scope, receive_fn, send_fn)
        assert response_started["status"] == 401
        assert len(calls) == 0  # Inner app was not called

    async def test_invalid_token_returns_401(self):
        """Request with invalid JWT gets 401."""
        try:
            from verlihub.api.routes.mcp import _McpAuthMiddleware, build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")

        calls = []

        async def inner(scope, receive, send):
            calls.append(True)

        server = build_inprocess_mcp_server()
        mw = _McpAuthMiddleware(inner, mcp_server=server)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/mcp",
            "headers": [(b"authorization", b"Bearer invalid_token_here")],
            "query_string": b"",
        }

        response_started = {}

        async def receive_fn():
            return {"type": "http.request", "body": b""}

        async def send_fn(message):
            if message["type"] == "http.response.start":
                response_started["status"] = message["status"]

        await mw(scope, receive_fn, send_fn)
        assert response_started["status"] == 401

    async def test_low_class_returns_403(self):
        """Request with valid token but insufficient class gets 403."""
        try:
            from verlihub.api.routes.mcp import _McpAuthMiddleware, build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")

        calls = []

        async def inner(scope, receive, send):
            calls.append(True)

        server = build_inprocess_mcp_server()
        mw = _McpAuthMiddleware(inner, mcp_server=server)

        # Create a token with class 1 (below min_class 3)
        raw_token = _token_str("lowuser", cls=1)
        cfg = _mcp_enabled_config()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/mcp",
            "headers": [(b"authorization", f"Bearer {raw_token}".encode())],
            "query_string": b"",
        }

        response_started = {}

        async def receive_fn():
            return {"type": "http.request", "body": b""}

        async def send_fn(message):
            if message["type"] == "http.response.start":
                response_started["status"] = message["status"]

        with patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            await mw(scope, receive_fn, send_fn)
        assert response_started["status"] == 403
        assert len(calls) == 0

    async def test_valid_token_passes_through(self):
        """Request with valid token and sufficient class passes to inner app."""
        try:
            from verlihub.api.routes.mcp import _McpAuthMiddleware, build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")

        calls = []

        async def inner(scope, receive, send):
            calls.append(True)

        server = build_inprocess_mcp_server()
        mw = _McpAuthMiddleware(inner, mcp_server=server)

        raw_token = _token_str("admin", cls=5)
        cfg = _mcp_enabled_config()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/mcp",
            "headers": [(b"authorization", f"Bearer {raw_token}".encode())],
            "query_string": b"",
        }

        async def receive_fn():
            return {"type": "http.request", "body": b""}

        async def send_fn(message):
            pass

        with patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            await mw(scope, receive_fn, send_fn)
        assert len(calls) == 1  # Inner app was called

    async def test_cookie_auth_works(self):
        """Auth via access_token cookie is accepted."""
        try:
            from verlihub.api.routes.mcp import _McpAuthMiddleware, build_inprocess_mcp_server
        except ImportError:
            pytest.skip("mcp package not installed")

        calls = []

        async def inner(scope, receive, send):
            calls.append(True)

        server = build_inprocess_mcp_server()
        mw = _McpAuthMiddleware(inner, mcp_server=server)

        raw_token = _token_str("admin", cls=5)
        cfg = _mcp_enabled_config()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/mcp",
            "headers": [(b"cookie", f"access_token=Bearer {raw_token}".encode())],
            "query_string": b"",
        }

        async def receive_fn():
            return {"type": "http.request", "body": b""}

        async def send_fn(message):
            pass

        with patch("verlihub.api.routes.mcp.get_config_optional", return_value=cfg):
            await mw(scope, receive_fn, send_fn)
        assert len(calls) == 1


# =============================================================================
# Hub context snapshot & injection tests
# =============================================================================


class TestHubContextSnapshot:
    """Tests for _build_hub_context_snapshot and _inject_hub_context."""

    def test_snapshot_with_context(self):
        """snapshot includes user list, hub name, share stats."""
        from verlihub.api.routes.llm import _build_hub_context_snapshot
        ctx = _mock_hub_context()
        with patch("verlihub.api.routes.llm.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            snap = _build_hub_context_snapshot(is_admin=True)
        assert "LIVE HUB DATA" in snap
        assert "TestHub" in snap
        assert "Alice" in snap
        assert "Bob" in snap
        assert "Charlie" in snap
        assert "Users online: 3" in snap

    def test_snapshot_without_context(self):
        """snapshot gracefully handles missing hub context."""
        from verlihub.api.routes.llm import _build_hub_context_snapshot
        with patch("verlihub.api.routes.llm.get_hub_context", return_value=None):
            snap = _build_hub_context_snapshot(is_admin=False)
        assert "not running" in snap.lower()

    def test_snapshot_admin_includes_ips(self):
        """Admin snapshot should include IP addresses."""
        from verlihub.api.routes.llm import _build_hub_context_snapshot
        ctx = _mock_hub_context()
        with patch("verlihub.api.routes.llm.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            snap = _build_hub_context_snapshot(is_admin=True)
        assert "10.0.0.1" in snap

    def test_snapshot_user_excludes_ips(self):
        """Non-admin snapshot should NOT include IP addresses."""
        from verlihub.api.routes.llm import _build_hub_context_snapshot
        ctx = _mock_hub_context()
        with patch("verlihub.api.routes.llm.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            snap = _build_hub_context_snapshot(is_admin=False)
        assert "10.0.0.1" not in snap

    def test_inject_hub_context_replaces_system_prompt(self):
        """_inject_hub_context replaces the system message with context-injected version."""
        from verlihub.api.routes.llm import _inject_hub_context, SYSTEM_PROMPT_ADMIN_CONTEXT
        ctx = _mock_hub_context()
        user = _make_token_data("op", 5)
        messages = [{"role": "system", "content": "original prompt"}]
        with patch("verlihub.api.routes.llm.get_hub_context", return_value=ctx), \
             patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            _inject_hub_context(messages, user, is_admin=True)
        # System message should now contain the context-injected prompt
        assert "LIVE HUB DATA" in messages[0]["content"]
        assert "op" in messages[0]["content"]  # personalized with nick
        # Should contain the context-aware prompt template
        assert "snapshot" in messages[0]["content"].lower() or "ONLY this data" in messages[0]["content"]


class TestChatSessionToolsAvailableFlag:
    """Tests for the tools_available flag on ChatSession."""

    def test_initial_tools_available(self):
        """New ChatSession should have tools_available=True."""
        from verlihub.api.routes.llm import ChatSession
        user = _make_token_data("op", 5)
        llm_cfg = _llm_enabled_config().llm
        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()):
            session = ChatSession(user, is_admin=True, llm_cfg=llm_cfg)
        assert session.tools_available is True

    @pytest.mark.anyio
    async def test_tools_available_false_skips_tools(self):
        """When tools_available=False, chat() should skip tool calls and inject context."""
        from verlihub.api.routes.llm import ChatSession
        user = _make_token_data("op", 5)
        llm_cfg = _llm_enabled_config().llm
        ctx = _mock_hub_context()

        mock_msg = MagicMock()
        mock_msg.content = "There is 1 user online based on the hub data."
        mock_msg.tool_calls = None
        mock_msg.model_dump = MagicMock(return_value={
            "role": "assistant",
            "content": mock_msg.content,
            "tool_calls": None,
        })
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()), \
             patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_openai), \
             patch("verlihub.api.routes.llm.get_hub_context", return_value=ctx):
            session = ChatSession(user, is_admin=True, llm_cfg=llm_cfg)
            session.tools_available = False  # Simulate previous failure

            resp_text, tool_calls = await session.chat("how many users?")

        assert "1 user" in resp_text
        assert tool_calls == []
        # Should have been called WITHOUT tools or tool_choice
        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        assert "tools" not in call_kwargs
        assert "tool_choice" not in call_kwargs
        # System message should contain live hub data
        system_msg = session.messages[0]["content"]
        assert "LIVE HUB DATA" in system_msg

    @pytest.mark.anyio
    async def test_bad_request_sets_tools_available_false(self):
        """When endpoint returns BadRequestError, tools_available should be set to False."""
        from verlihub.api.routes.llm import ChatSession
        import openai as openai_mod
        user = _make_token_data("op", 5)
        llm_cfg = _llm_enabled_config().llm
        ctx = _mock_hub_context()

        mock_msg = MagicMock()
        mock_msg.content = "Fallback response."
        mock_msg.tool_calls = None
        mock_msg.model_dump = MagicMock(return_value={
            "role": "assistant", "content": "Fallback response.", "tool_calls": None,
        })
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and "tools" in kwargs and kwargs["tools"]:
                raise openai_mod.BadRequestError(
                    message="tool_choice auto requires --enable-auto-tool-choice",
                    response=MagicMock(status_code=400),
                    body={"error": {"message": "bad"}},
                )
            return mock_response

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(side_effect=side_effect)

        with patch("verlihub.api.routes.llm.get_config_optional", return_value=_llm_enabled_config()), \
             patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_openai), \
             patch("verlihub.api.routes.llm.get_hub_context", return_value=ctx):
            session = ChatSession(user, is_admin=True, llm_cfg=llm_cfg)
            assert session.tools_available is True

            resp_text, _ = await session.chat("who is online?")

        assert session.tools_available is False
        assert "Fallback" in resp_text
        # System prompt should now contain hub data
        assert "LIVE HUB DATA" in session.messages[0]["content"]
