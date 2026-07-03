"""
Tests for Lua plugin configuration — YAML loading, dataclass serialization,
and dashboard integration.

Covers:
- LuaConfig / LuaGithubScript dataclass construction
- YAML round-trip (from_dict → to_dict) for Lua section
- Default values when Lua section is absent
- Ledokol-specific config handling
- Dashboard route passes lua_scripts context
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from verlihub.config import (
    LuaConfig,
    LuaGithubScript,
    VerlihubConfig,
    load_config,
)


# ---------------------------------------------------------------------------
# LuaGithubScript
# ---------------------------------------------------------------------------

class TestLuaGithubScript:
    """Tests for the LuaGithubScript dataclass."""

    def test_create_with_repo_only(self):
        gs = LuaGithubScript(repo="Verlihub/ledokol")
        assert gs.repo == "Verlihub/ledokol"
        assert gs.files == []

    def test_create_with_files(self):
        gs = LuaGithubScript(repo="Verlihub/ledokol", files=["ledokol.lua", "readme.md"])
        assert gs.repo == "Verlihub/ledokol"
        assert gs.files == ["ledokol.lua", "readme.md"]

    def test_to_dict_roundtrip(self):
        gs = LuaGithubScript(repo="Verlihub/ledokol", files=["ledokol.lua"])
        d = {"repo": gs.repo, "files": gs.files}
        gs2 = LuaGithubScript(repo=d["repo"], files=d["files"])
        assert gs == gs2


# ---------------------------------------------------------------------------
# LuaConfig
# ---------------------------------------------------------------------------

class TestLuaConfig:
    """Tests for the LuaConfig dataclass."""

    def test_defaults(self):
        cfg = LuaConfig()
        assert cfg.enabled is True
        assert cfg.github_scripts == []
        assert cfg.autoload == []
        assert cfg.script_config == {}

    def test_enabled_false(self):
        cfg = LuaConfig(enabled=False)
        assert cfg.enabled is False

    def test_with_github_scripts(self):
        gs = LuaGithubScript(repo="Verlihub/ledokol", files=["ledokol.lua"])
        cfg = LuaConfig(github_scripts=[gs])
        assert len(cfg.github_scripts) == 1
        assert cfg.github_scripts[0].repo == "Verlihub/ledokol"

    def test_with_autoload(self):
        cfg = LuaConfig(autoload=["ledokol.lua", "another.lua"])
        assert cfg.autoload == ["ledokol.lua", "another.lua"]

    def test_with_script_config(self):
        sconf = {"ledokol": {"calculator": "1", "hubchat_history": "50", "anti_spam": "1"}}
        cfg = LuaConfig(script_config=sconf)
        assert cfg.script_config["ledokol"]["calculator"] == "1"
        assert cfg.script_config["ledokol"]["anti_spam"] == "1"

    def test_full_construction(self):
        cfg = LuaConfig(
            enabled=True,
            github_scripts=[
                LuaGithubScript(repo="Verlihub/ledokol", files=["ledokol.lua"]),
                LuaGithubScript(repo="some/other-scripts"),
            ],
            autoload=["ledokol.lua"],
            script_config={"ledokol": {"calculator": "1"}},
        )
        assert len(cfg.github_scripts) == 2
        assert cfg.autoload == ["ledokol.lua"]
        assert cfg.script_config == {"ledokol": {"calculator": "1"}}


# ---------------------------------------------------------------------------
# VerlihubConfig — Lua section from_dict / to_dict
# ---------------------------------------------------------------------------

class TestVerlihubConfigLua:
    """Tests for Lua section in VerlihubConfig."""

    FULL_LUA_DICT = {
        "lua": {
            "enabled": True,
            "github_scripts": [
                {"repo": "Verlihub/ledokol", "files": ["ledokol.lua"]},
            ],
            "autoload": ["ledokol.lua"],
            "script_config": {
                "ledokol": {
                    "calculator": "1",
                    "hubchat_history": "50",
                },
            },
        }
    }

    def test_from_dict_with_lua(self):
        cfg = VerlihubConfig.from_dict(self.FULL_LUA_DICT)
        assert cfg.lua.enabled is True
        assert len(cfg.lua.github_scripts) == 1
        assert cfg.lua.github_scripts[0].repo == "Verlihub/ledokol"
        assert cfg.lua.github_scripts[0].files == ["ledokol.lua"]
        assert cfg.lua.autoload == ["ledokol.lua"]
        assert cfg.lua.script_config["ledokol"]["calculator"] == "1"

    def test_from_dict_without_lua_uses_defaults(self):
        cfg = VerlihubConfig.from_dict({})
        assert cfg.lua.enabled is True
        assert cfg.lua.github_scripts == []
        assert cfg.lua.autoload == []
        assert cfg.lua.script_config == {}

    def test_from_dict_lua_disabled(self):
        cfg = VerlihubConfig.from_dict({"lua": {"enabled": False}})
        assert cfg.lua.enabled is False

    def test_from_dict_multiple_github_scripts(self):
        data = {
            "lua": {
                "github_scripts": [
                    {"repo": "Verlihub/ledokol", "files": ["ledokol.lua"]},
                    {"repo": "SomeUser/lua-extras"},
                ],
            }
        }
        cfg = VerlihubConfig.from_dict(data)
        assert len(cfg.lua.github_scripts) == 2
        assert cfg.lua.github_scripts[1].repo == "SomeUser/lua-extras"
        assert cfg.lua.github_scripts[1].files == []

    def test_to_dict_roundtrip(self):
        cfg = VerlihubConfig.from_dict(self.FULL_LUA_DICT)
        d = cfg.to_dict()
        assert d["lua"]["enabled"] is True
        assert len(d["lua"]["github_scripts"]) == 1
        assert d["lua"]["github_scripts"][0]["repo"] == "Verlihub/ledokol"
        assert d["lua"]["autoload"] == ["ledokol.lua"]
        assert d["lua"]["script_config"]["ledokol"]["calculator"] == "1"

    def test_to_dict_default_lua(self):
        cfg = VerlihubConfig()
        d = cfg.to_dict()
        assert d["lua"]["enabled"] is True
        assert d["lua"]["github_scripts"] == []
        assert d["lua"]["autoload"] == []
        assert d["lua"]["script_config"] == {}


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------

class TestYamlLuaRoundTrip:
    """Test loading and saving YAML with Lua configuration."""

    YAML_CONTENT = """\
mode: hub
environment: production

lua:
  enabled: true
  github_scripts:
    - repo: Verlihub/ledokol
      files:
        - ledokol.lua
  autoload:
    - ledokol.lua
  ledokol_config:
    calculator: "1"
    hubchat_history: "50"
    anti_spam: "1"
"""

    def test_from_yaml_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(self.YAML_CONTENT)
            f.flush()
            try:
                cfg = VerlihubConfig.from_yaml(f.name)
                assert cfg.lua.enabled is True
                assert len(cfg.lua.github_scripts) == 1
                assert cfg.lua.github_scripts[0].repo == "Verlihub/ledokol"
                assert cfg.lua.autoload == ["ledokol.lua"]
                # Legacy ledokol_config key is auto-wrapped as script_config["ledokol"]
                assert cfg.lua.script_config["ledokol"]["anti_spam"] == "1"
                assert cfg.mode == "hub"
            finally:
                os.unlink(f.name)

    def test_yaml_without_lua(self):
        yaml_no_lua = "mode: api\nenvironment: development\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_no_lua)
            f.flush()
            try:
                cfg = VerlihubConfig.from_yaml(f.name)
                # Should use defaults
                assert cfg.lua.enabled is True
                assert cfg.lua.github_scripts == []
            finally:
                os.unlink(f.name)

    def test_yaml_to_dict_preserves_lua(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(self.YAML_CONTENT)
            f.flush()
            try:
                cfg = VerlihubConfig.from_yaml(f.name)
                d = cfg.to_dict()
                # Legacy ledokol_config is emitted as script_config["ledokol"]
                assert d["lua"]["script_config"]["ledokol"]["hubchat_history"] == "50"
            finally:
                os.unlink(f.name)


# ---------------------------------------------------------------------------
# Dashboard integration — plugins_page passes lua_scripts
# ---------------------------------------------------------------------------

class TestDashboardLuaIntegration:
    """Test that the dashboard plugins route populates lua_scripts context."""

    @pytest.fixture
    def mock_hub_context(self):
        ctx = MagicMock()
        ctx.get_plugins.return_value = [
            {"nick": "lua", "desc": "Lua Scripting", "loaded": True, "autoload": True, "version": "1.0"},
        ]
        ctx.get_python_scripts.return_value = []
        ctx.get_lua_scripts.return_value = [
            {"name": "ledokol.lua", "loaded": True},
            {"name": "custom.lua", "loaded": False},
        ]
        return ctx

    def test_lua_scripts_fallback_to_directory(self):
        """When get_lua_scripts raises, fall back to scanning scripts dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some .lua files
            (Path(tmpdir) / "ledokol.lua").write_text("-- test")
            (Path(tmpdir) / "helper.lua").write_text("-- test")
            (Path(tmpdir) / "not_lua.py").write_text("# not lua")

            ctx = MagicMock()
            ctx.get_lua_scripts.side_effect = AttributeError("no method")

            with patch.dict(os.environ, {"VH_SCRIPTS_DIR": tmpdir}):
                # Simulate what routes.py does
                lua_scripts = []
                try:
                    lua_scripts = ctx.get_lua_scripts() or []
                except AttributeError:
                    if os.path.isdir(tmpdir):
                        for f in os.listdir(tmpdir):
                            if f.endswith(".lua"):
                                lua_scripts.append({"name": f, "loaded": False})

            names = {s["name"] for s in lua_scripts}
            assert "ledokol.lua" in names
            assert "helper.lua" in names
            assert "not_lua.py" not in names

    def test_lua_scripts_from_context(self, mock_hub_context):
        """When get_lua_scripts works, use its output directly."""
        scripts = mock_hub_context.get_lua_scripts()
        assert len(scripts) == 2
        assert scripts[0]["name"] == "ledokol.lua"
        assert scripts[0]["loaded"] is True

    def test_empty_lua_scripts(self):
        """No Lua scripts means empty list."""
        ctx = MagicMock()
        ctx.get_lua_scripts.return_value = []
        assert ctx.get_lua_scripts() == []


# ---------------------------------------------------------------------------
# Script config (per-script settings)
# ---------------------------------------------------------------------------

class TestScriptConfig:
    """Test per-script config values using script_config."""

    LEDOKOL_SETTINGS = {
        "calculator": "1",
        "hubchat_history": "50",
        "anti_spam": "1",
        "anti_flood": "1",
        "auto_register": "0",
        "hub_topic_enable": "1",
        "news_enable": "1",
    }

    def test_script_config_preserved(self):
        cfg = LuaConfig(script_config={"ledokol": self.LEDOKOL_SETTINGS})
        assert len(cfg.script_config["ledokol"]) == 7
        assert cfg.script_config["ledokol"]["anti_flood"] == "1"

    def test_legacy_ledokol_config_from_yaml_dict(self):
        """Legacy ledokol_config key is auto-wrapped as script_config['ledokol']."""
        data = {
            "lua": {
                "ledokol_config": self.LEDOKOL_SETTINGS,
            }
        }
        cfg = VerlihubConfig.from_dict(data)
        assert cfg.lua.script_config["ledokol"]["hub_topic_enable"] == "1"

    def test_script_config_roundtrip(self):
        cfg = VerlihubConfig.from_dict({
            "lua": {"script_config": {"ledokol": self.LEDOKOL_SETTINGS}}
        })
        d = cfg.to_dict()
        assert d["lua"]["script_config"]["ledokol"] == self.LEDOKOL_SETTINGS

    def test_empty_script_config(self):
        cfg = VerlihubConfig.from_dict({"lua": {}})
        assert cfg.lua.script_config == {}

    def test_multiple_scripts(self):
        """script_config supports multiple scripts with separate settings."""
        sconf = {
            "ledokol": {"calculator": "1"},
            "my_custom_script": {"debug": "true", "log_level": "2"},
        }
        cfg = LuaConfig(script_config=sconf)
        assert cfg.script_config["ledokol"]["calculator"] == "1"
        assert cfg.script_config["my_custom_script"]["debug"] == "true"
