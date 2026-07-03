"""
Tests for verlihub.config — YAML configuration loader.

Covers: DatabaseConfig URL construction, display_name, to_env,
VerlihubConfig.from_dict, from_env, validate, to_dict, load_config.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from verlihub.config import (
    DatabaseConfig,
    ApiConfig,
    HubConfig,
    BotsConfig,
    BotConfig,
    PluginEntry,
    PluginsConfig,
    LuaConfig,
    LuaGithubScript,
    LoggingConfig,
    VerlihubConfig,
    load_config,
)


# ======================================================================
# DatabaseConfig.get_url
# ======================================================================


class TestDatabaseConfigGetUrl:
    """Test DatabaseConfig.get_url() branches."""

    def test_direct_url_override(self):
        db = DatabaseConfig(url="postgresql+asyncpg://user:pass@host:5432/db")
        assert db.get_url() == "postgresql+asyncpg://user:pass@host:5432/db"

    def test_sqlite_in_memory_default(self):
        db = DatabaseConfig(type="sqlite")
        assert db.get_url() == "sqlite+aiosqlite:///:memory:"

    def test_sqlite_explicit_memory_path(self):
        db = DatabaseConfig(type="sqlite", path=":memory:")
        assert db.get_url() == "sqlite+aiosqlite:///:memory:"

    def test_sqlite_with_path(self):
        db = DatabaseConfig(type="sqlite", path="/var/lib/vh/test.db")
        assert db.get_url() == "sqlite+aiosqlite:////var/lib/vh/test.db"

    def test_sqlite_with_config_dir(self):
        db = DatabaseConfig(type="sqlite")
        url = db.get_url(config_dir="/opt/verlihub")
        assert url == "sqlite+aiosqlite:////opt/verlihub/verlihub.db"

    def test_sqlite_path_takes_precedence_over_config_dir(self):
        db = DatabaseConfig(type="sqlite", path="/explicit.db")
        url = db.get_url(config_dir="/opt/verlihub")
        assert url == "sqlite+aiosqlite:////explicit.db"

    def test_mysql_basic(self):
        db = DatabaseConfig(type="mysql", user="root", password="s3cr3t",
                           host="db.local", port=3306, name="verlihub")
        url = db.get_url()
        assert url == "mysql+asyncmy://root:s3cr3t@db.local:3306/verlihub"

    def test_mysql_auto_corrects_postgres_port(self):
        """If port is 5432 (postgres default), correct to 3306 for mysql."""
        db = DatabaseConfig(type="mysql", user="u", password="p",
                           host="h", port=5432, name="n")
        assert ":3306/" in db.get_url()

    def test_postgresql_basic(self):
        db = DatabaseConfig(type="postgresql", user="pg", password="pass",
                           host="pghost", port=5432, name="mydb")
        url = db.get_url()
        assert url == "postgresql+asyncpg://pg:pass@pghost:5432/mydb"

    def test_postgres_alias(self):
        db = DatabaseConfig(type="postgres", user="u", password="p",
                           host="h", port=5432, name="n")
        assert db.get_url().startswith("postgresql+asyncpg://")

    def test_postgresql_auto_corrects_mysql_port(self):
        """If port is 3306, correct to 5432 for postgres."""
        db = DatabaseConfig(type="postgresql", user="u", password="p",
                           host="h", port=3306, name="n")
        assert ":5432/" in db.get_url()

    def test_user_only_no_password(self):
        db = DatabaseConfig(type="mysql", user="viewer", password="",
                           host="h", port=3306, name="n")
        url = db.get_url()
        assert "viewer@h:3306" in url
        assert ":@" not in url  # No trailing colon from empty password

    def test_no_user_no_password(self):
        db = DatabaseConfig(type="mysql", user="", password="",
                           host="h", port=3306, name="n")
        url = db.get_url()
        assert url == "mysql+asyncmy://h:3306/n"

    def test_password_special_chars_url_encoded(self):
        db = DatabaseConfig(type="mysql", user="u", password="p@ss/w0rd!",
                           host="h", port=3306, name="n")
        url = db.get_url()
        assert "p%40ss%2Fw0rd%21" in url

    def test_unknown_type_raises(self):
        db = DatabaseConfig(type="oracle")
        with pytest.raises(ValueError, match="Unknown database type"):
            db.get_url()


# ======================================================================
# DatabaseConfig.to_env / display_name
# ======================================================================


class TestDatabaseConfigHelpers:

    def test_to_env_returns_all_keys(self):
        db = DatabaseConfig(type="mysql", host="myhost", port=3307,
                           user="joe", password="pw", name="vh", path="/p")
        env = db.to_env()
        assert env["VH_DB_TYPE"] == "mysql"
        assert env["VH_DB_HOST"] == "myhost"
        assert env["VH_DB_PORT"] == "3307"
        assert env["VH_DB_USER"] == "joe"
        assert env["VH_DB_PASSWORD"] == "pw"
        assert env["VH_DB_NAME"] == "vh"
        assert env["VH_DB_PATH"] == "/p"

    def test_display_name_direct_url_masks_password(self):
        db = DatabaseConfig(url="postgresql+asyncpg://user:secret@host/db")
        dn = db.display_name()
        assert "secret" not in dn
        assert "***" in dn
        assert "host/db" in dn

    def test_display_name_url_without_password(self):
        db = DatabaseConfig(url="sqlite+aiosqlite:///test.db")
        assert db.display_name() == "sqlite+aiosqlite:///test.db"

    def test_display_name_sqlite_with_path(self):
        db = DatabaseConfig(type="sqlite", path="/data/vh.db")
        assert db.display_name() == "sqlite:/data/vh.db"

    def test_display_name_sqlite_with_config_dir(self):
        db = DatabaseConfig(type="sqlite")
        assert db.display_name(config_dir="/opt") == "sqlite:/opt/verlihub.db"

    def test_display_name_sqlite_memory(self):
        db = DatabaseConfig(type="sqlite")
        assert db.display_name() == "sqlite::memory:"

    def test_display_name_mysql(self):
        db = DatabaseConfig(type="mysql", host="db.local", port=3306, name="vh")
        assert db.display_name() == "mysql://db.local:3306/vh"


# ======================================================================
# ApiConfig.to_env
# ======================================================================


class TestApiConfig:

    def test_to_env_basic(self):
        api = ApiConfig(host="0.0.0.0", port=9090, secret="mysecret",
                       token_expire_minutes=120,
                       cors_origins=["http://a", "http://b"], secure_cookies=True)
        env = api.to_env()
        assert env["VH_API_HOST"] == "0.0.0.0"
        assert env["VH_API_PORT"] == "9090"
        assert env["VH_JWT_SECRET"] == "mysecret"
        assert env["VH_CORS_ORIGINS"] == "http://a,http://b"
        assert env["VH_SECURE_COOKIES"] == "1"

    def test_to_env_no_secret_omits_key(self):
        api = ApiConfig()
        env = api.to_env()
        assert "VH_JWT_SECRET" not in env

    def test_registration_defaults(self):
        api = ApiConfig()
        assert api.registration_enabled is True
        assert api.registration_require_invite is False
        assert api.registration_default_class == 1

    def test_registration_to_env(self):
        api = ApiConfig(
            registration_enabled=False,
            registration_require_invite=True,
            registration_default_class=2,
        )
        env = api.to_env()
        assert env["VH_REGISTRATION_ENABLED"] == "0"
        assert env["VH_REGISTRATION_REQUIRE_INVITE"] == "1"
        assert env["VH_REGISTRATION_DEFAULT_CLASS"] == "2"

    def test_registration_to_env_defaults(self):
        api = ApiConfig()  # All defaults
        env = api.to_env()
        assert env["VH_REGISTRATION_ENABLED"] == "1"
        assert env["VH_REGISTRATION_REQUIRE_INVITE"] == "0"
        assert env["VH_REGISTRATION_DEFAULT_CLASS"] == "1"


# ======================================================================
# VerlihubConfig.from_dict — full dictionary parsing
# ======================================================================


class TestVerlihubConfigFromDict:

    def test_empty_dict_returns_defaults(self):
        cfg = VerlihubConfig.from_dict({})
        assert cfg.database.type == "sqlite"
        assert cfg.api.port == 8000
        assert cfg.hub.name == "My DC++ Hub"
        assert cfg.mode == "both"

    def test_database_section(self):
        cfg = VerlihubConfig.from_dict({
            "database": {"type": "mysql", "host": "db1", "port": 3307,
                        "user": "root", "password": "pw", "name": "vh"}
        })
        assert cfg.database.type == "mysql"
        assert cfg.database.host == "db1"
        assert cfg.database.port == 3307

    def test_api_section(self):
        cfg = VerlihubConfig.from_dict({
            "api": {"host": "0.0.0.0", "port": 9000,
                   "secret": "s", "secure_cookies": True}
        })
        assert cfg.api.host == "0.0.0.0"
        assert cfg.api.port == 9000
        assert cfg.api.secure_cookies is True

    def test_hub_section(self):
        cfg = VerlihubConfig.from_dict({
            "hub": {"name": "TestHub", "port": 5000, "owner": "admin",
                   "topic": "Test", "max_users": 500}
        })
        assert cfg.hub.name == "TestHub"
        assert cfg.hub.port == 5000
        assert cfg.hub.max_users == 500

    def test_hub_description_and_topic(self):
        cfg = VerlihubConfig.from_dict({
            "hub": {"description": "Best hub", "topic": "Files"}
        })
        assert cfg.hub.description == "Best hub"
        assert cfg.hub.topic == "Files"

    def test_hub_logo(self):
        cfg = VerlihubConfig.from_dict({
            "hub": {"logo": "https://example.com/logo.png"}
        })
        assert cfg.hub.logo == "https://example.com/logo.png"

    def test_hub_logo_default_empty(self):
        cfg = VerlihubConfig.from_dict({})
        assert cfg.hub.logo == ""

    def test_hub_hublist_servers(self):
        cfg = VerlihubConfig.from_dict({
            "hub": {"hublist_servers": ["hl1.example.com", "hl2.example.com"]}
        })
        assert cfg.hub.hublist_servers == ["hl1.example.com", "hl2.example.com"]

    def test_hub_hublist_servers_defaults(self):
        cfg = VerlihubConfig.from_dict({})
        assert "hublist.te-home.net" in cfg.hub.hublist_servers
        assert "hublist.pwiam.com" in cfg.hub.hublist_servers

    def test_hub_host_for_hublist_registration(self):
        cfg = VerlihubConfig.from_dict({
            "hub": {"host": "hub.example.com:4111"}
        })
        assert cfg.hub.host == "hub.example.com:4111"

    def test_bots_section(self):
        cfg = VerlihubConfig.from_dict({
            "bots": {
                "security": {"nick": "Guard", "description": "Security bot"},
                "op_chat": {"nick": "OpsRoom"},
            }
        })
        assert cfg.bots.security.nick == "Guard"
        assert cfg.bots.security.description == "Security bot"
        assert cfg.bots.op_chat.nick == "OpsRoom"

    def test_plugins_section(self):
        cfg = VerlihubConfig.from_dict({
            "plugins": {
                "directory": "/opt/plugins",
                "list": [
                    {"name": "lua", "enabled": True, "autoload": True,
                     "config": {"key": "val"}},
                    {"name": "python", "enabled": False},
                ],
            }
        })
        assert cfg.plugins.directory == "/opt/plugins"
        assert len(cfg.plugins.plugins) == 2
        assert cfg.plugins.plugins[0].name == "lua"
        assert cfg.plugins.plugins[0].config == {"key": "val"}
        assert cfg.plugins.plugins[1].enabled is False

    def test_lua_section(self):
        cfg = VerlihubConfig.from_dict({
            "lua": {
                "enabled": True,
                "github_scripts": [
                    {"repo": "Verlihub/ledokol", "files": ["reg.lua"]},
                ],
                "autoload": ["reg.lua", "stats.lua"],
                "script_config": {"ledokol": {"motd": True}},
            }
        })
        assert cfg.lua.enabled is True
        assert len(cfg.lua.github_scripts) == 1
        assert cfg.lua.github_scripts[0].repo == "Verlihub/ledokol"
        assert cfg.lua.autoload == ["reg.lua", "stats.lua"]
        assert cfg.lua.script_config == {"ledokol": {"motd": True}}

    def test_logging_section(self):
        cfg = VerlihubConfig.from_dict({
            "logging": {
                "level": "DEBUG",
                "file": "/var/log/vh.log",
                "format": "%(message)s",
            }
        })
        assert cfg.logging.level == "DEBUG"
        assert cfg.logging.file == "/var/log/vh.log"

    def test_mode_and_environment(self):
        cfg = VerlihubConfig.from_dict({
            "mode": "both",
            "environment": "production",
        })
        assert cfg.mode == "both"
        assert cfg.environment == "production"

    def test_registration_settings_from_dict(self):
        cfg = VerlihubConfig.from_dict({
            "api": {
                "registration_enabled": False,
                "registration_require_invite": True,
                "registration_default_class": 2,
            }
        })
        assert cfg.api.registration_enabled is False
        assert cfg.api.registration_require_invite is True
        assert cfg.api.registration_default_class == 2

    def test_registration_settings_default_from_dict(self):
        cfg = VerlihubConfig.from_dict({})
        assert cfg.api.registration_enabled is True
        assert cfg.api.registration_require_invite is False
        assert cfg.api.registration_default_class == 1

    def test_registration_partial_override(self):
        cfg = VerlihubConfig.from_dict({
            "api": {"registration_require_invite": True}
        })
        assert cfg.api.registration_enabled is True  # default kept
        assert cfg.api.registration_require_invite is True  # overridden
        assert cfg.api.registration_default_class == 1  # default kept


# ======================================================================
# VerlihubConfig.from_env
# ======================================================================


class TestVerlihubConfigFromEnv:

    def test_from_env_defaults(self, monkeypatch):
        # Clear all VH_ env vars
        for key in list(os.environ):
            if key.startswith("VH_") or key.startswith("VERLIHUB_"):
                monkeypatch.delenv(key, raising=False)
        cfg = VerlihubConfig.from_env()
        assert cfg.database.type == "sqlite"
        assert cfg.api.host == "127.0.0.1"
        assert cfg.hub.name == "My DC++ Hub"

    def test_from_env_database(self, monkeypatch):
        monkeypatch.setenv("VH_DB_TYPE", "mysql")
        monkeypatch.setenv("VERLIHUB_DB_HOST", "dbhost")
        monkeypatch.setenv("VERLIHUB_DB_PORT", "3307")
        monkeypatch.setenv("VERLIHUB_DB_USER", "joe")
        monkeypatch.setenv("VERLIHUB_DB_PASSWORD", "secret")
        monkeypatch.setenv("VERLIHUB_DB_NAME", "mydb")
        cfg = VerlihubConfig.from_env()
        assert cfg.database.type == "mysql"
        assert cfg.database.host == "dbhost"
        assert cfg.database.port == 3307
        assert cfg.database.password == "secret"

    def test_from_env_use_sqlite_legacy(self, monkeypatch):
        monkeypatch.setenv("VH_USE_SQLITE", "true")
        monkeypatch.setenv("VH_DB_TYPE", "mysql")  # Should be overridden
        cfg = VerlihubConfig.from_env()
        assert cfg.database.type == "sqlite"

    def test_from_env_api(self, monkeypatch):
        monkeypatch.setenv("VH_API_HOST", "0.0.0.0")
        monkeypatch.setenv("VH_API_PORT", "9090")
        monkeypatch.setenv("VH_JWT_SECRET", "mysecret")
        monkeypatch.setenv("VH_SECURE_COOKIES", "1")
        monkeypatch.setenv("VH_CORS_ORIGINS", "http://a,http://b")
        cfg = VerlihubConfig.from_env()
        assert cfg.api.host == "0.0.0.0"
        assert cfg.api.port == 9090
        assert cfg.api.secret == "mysecret"
        assert cfg.api.secure_cookies is True
        assert cfg.api.cors_origins == ["http://a", "http://b"]

    def test_from_env_hub(self, monkeypatch):
        monkeypatch.setenv("VH_HUB_NAME", "EnvHub")
        monkeypatch.setenv("VH_HUB_PORT", "5000")
        monkeypatch.setenv("VH_HUB_MAX_USERS", "500")
        cfg = VerlihubConfig.from_env()
        assert cfg.hub.name == "EnvHub"
        assert cfg.hub.port == 5000
        assert cfg.hub.max_users == 500

    def test_from_env_hub_description_topic_logo(self, monkeypatch):
        monkeypatch.setenv("VH_HUB_DESCRIPTION", "Env description")
        monkeypatch.setenv("VH_HUB_TOPIC", "Env topic")
        monkeypatch.setenv("VH_HUB_LOGO", "https://env.com/logo.png")
        monkeypatch.setenv("VH_HUB_HOST", "env.example.com:411")
        cfg = VerlihubConfig.from_env()
        assert cfg.hub.description == "Env description"
        assert cfg.hub.topic == "Env topic"
        assert cfg.hub.logo == "https://env.com/logo.png"
        assert cfg.hub.host == "env.example.com:411"

    def test_from_env_mode(self, monkeypatch):
        monkeypatch.setenv("VH_MODE", "both")
        monkeypatch.setenv("VH_ENV", "production")
        cfg = VerlihubConfig.from_env()
        assert cfg.mode == "both"
        assert cfg.environment == "production"

    def test_from_env_registration_enabled_false(self, monkeypatch):
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "0")
        cfg = VerlihubConfig.from_env()
        assert cfg.api.registration_enabled is False

    def test_from_env_registration_enabled_true(self, monkeypatch):
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "true")
        cfg = VerlihubConfig.from_env()
        assert cfg.api.registration_enabled is True

    def test_from_env_registration_require_invite(self, monkeypatch):
        monkeypatch.setenv("VH_REGISTRATION_REQUIRE_INVITE", "1")
        cfg = VerlihubConfig.from_env()
        assert cfg.api.registration_require_invite is True

    def test_from_env_registration_default_class(self, monkeypatch):
        monkeypatch.setenv("VH_REGISTRATION_DEFAULT_CLASS", "2")
        cfg = VerlihubConfig.from_env()
        assert cfg.api.registration_default_class == 2

    def test_from_env_registration_defaults_when_unset(self, monkeypatch):
        # Clear registration env vars
        for key in ["VH_REGISTRATION_ENABLED", "VH_REGISTRATION_REQUIRE_INVITE",
                     "VH_REGISTRATION_DEFAULT_CLASS"]:
            monkeypatch.delenv(key, raising=False)
        cfg = VerlihubConfig.from_env()
        assert cfg.api.registration_enabled is True
        assert cfg.api.registration_require_invite is False
        assert cfg.api.registration_default_class == 1


# ======================================================================
# VerlihubConfig.validate
# ======================================================================


class TestVerlihubConfigValidate:

    def test_development_no_issues(self):
        cfg = VerlihubConfig()
        issues = cfg.validate()
        # Only the DB password warning
        assert len(issues) == 1
        assert "database password" in issues[0].lower()

    def test_production_missing_secrets(self):
        cfg = VerlihubConfig(
            environment="production",
            api=ApiConfig(host="0.0.0.0", cors_origins=["*"]),
        )
        issues = cfg.validate()
        assert any("No API secret" in i for i in issues)
        assert any("Wildcard CORS" in i for i in issues)
        assert any("all interfaces" in i for i in issues)

    def test_production_secure_cookies_warning(self):
        cfg = VerlihubConfig(
            environment="production",
            api=ApiConfig(secure_cookies=False),
        )
        issues = cfg.validate()
        assert any("Secure cookies disabled" in i for i in issues)

    def test_production_no_issues_when_configured(self):
        cfg = VerlihubConfig(
            environment="production",
            api=ApiConfig(
                secret="long-production-secret",
                cors_origins=["https://hub.example.com"],
                secure_cookies=True,
                host="127.0.0.1",
            ),
            database=DatabaseConfig(password="dbpass"),
        )
        issues = cfg.validate()
        assert len(issues) == 0


# ======================================================================
# VerlihubConfig.to_dict
# ======================================================================


class TestVerlihubConfigToDict:

    def test_round_trip_keys(self):
        cfg = VerlihubConfig()
        d = cfg.to_dict()
        assert "mode" in d
        assert "environment" in d
        assert "database" in d
        assert "api" in d
        assert "hub" in d
        assert "logging" in d
        assert "lua" in d

    def test_hub_fields_in_to_dict(self):
        cfg = VerlihubConfig(
            hub=HubConfig(
                name="RoundTrip",
                description="Desc",
                host="rt.example.com:411",
                topic="RT Topic",
                logo="https://rt.example.com/logo.png",
                max_users=200,
                hublist_servers=["hl.custom.org"],
            ),
        )
        d = cfg.to_dict()
        assert d["hub"]["name"] == "RoundTrip"
        assert d["hub"]["description"] == "Desc"
        assert d["hub"]["host"] == "rt.example.com:411"
        assert d["hub"]["topic"] == "RT Topic"
        assert d["hub"]["logo"] == "https://rt.example.com/logo.png"
        assert d["hub"]["max_users"] == 200
        assert d["hub"]["hublist_servers"] == ["hl.custom.org"]

    def test_password_not_in_output(self):
        cfg = VerlihubConfig(
            database=DatabaseConfig(password="secret123"),
        )
        d = cfg.to_dict()
        assert "password" not in d["database"]
        assert "password" not in d["api"]

    def test_registration_fields_in_to_dict(self):
        cfg = VerlihubConfig(
            api=ApiConfig(
                registration_enabled=False,
                registration_require_invite=True,
                registration_default_class=2,
            ),
        )
        d = cfg.to_dict()
        assert d["api"]["registration_enabled"] is False
        assert d["api"]["registration_require_invite"] is True
        assert d["api"]["registration_default_class"] == 2

    def test_registration_defaults_in_to_dict(self):
        cfg = VerlihubConfig()
        d = cfg.to_dict()
        assert d["api"]["registration_enabled"] is True
        assert d["api"]["registration_require_invite"] is False
        assert d["api"]["registration_default_class"] == 1

    def test_lua_scripts_serialized(self):
        cfg = VerlihubConfig(
            lua=LuaConfig(
                github_scripts=[LuaGithubScript(repo="A/B", files=["x.lua"])],
                autoload=["x.lua"],
                script_config={"ledokol": {"motd": True}},
            ),
        )
        d = cfg.to_dict()
        assert d["lua"]["github_scripts"] == [{"repo": "A/B", "files": ["x.lua"]}]
        assert d["lua"]["autoload"] == ["x.lua"]
        assert d["lua"]["script_config"] == {"ledokol": {"motd": True}}


# ======================================================================
# VerlihubConfig.apply_to_env
# ======================================================================


class TestApplyToEnv:

    def test_sets_env_vars(self, monkeypatch):
        cfg = VerlihubConfig(
            database=DatabaseConfig(type="mysql", host="h1"),
            api=ApiConfig(host="0.0.0.0", port=9090),
        )
        cfg.apply_to_env()
        assert os.environ["VH_DB_TYPE"] == "mysql"
        assert os.environ["VH_DB_HOST"] == "h1"
        assert os.environ["VH_API_HOST"] == "0.0.0.0"
        assert os.environ["VH_API_PORT"] == "9090"

    def test_sets_hub_env_vars(self, monkeypatch):
        cfg = VerlihubConfig(
            hub=HubConfig(
                name="TestHub",
                description="A test hub",
                topic="Testing",
                logo="https://example.com/logo.png",
                host="hub.example.com:4111",
                port=4111,
                owner="myowner",
                encoding="CP1251",
                listen_host="127.0.0.1",
                max_users=500,
            ),
        )
        cfg.apply_to_env()
        assert os.environ["VH_HUB_NAME"] == "TestHub"
        assert os.environ["VH_HUB_DESCRIPTION"] == "A test hub"
        assert os.environ["VH_HUB_TOPIC"] == "Testing"
        assert os.environ["VH_HUB_LOGO"] == "https://example.com/logo.png"
        assert os.environ["VH_HUB_HOST"] == "hub.example.com:4111"
        assert os.environ["VH_HUB_PORT"] == "4111"
        assert os.environ["VH_HUB_OWNER"] == "myowner"
        assert os.environ["VH_HUB_ENCODING"] == "CP1251"
        assert os.environ["VH_HUB_LISTEN"] == "127.0.0.1"
        assert os.environ["VH_HUB_MAX_USERS"] == "500"


# ======================================================================
# VerlihubConfig.setup_logging
# ======================================================================


class TestSetupLogging:

    def test_setup_logging_does_not_raise(self):
        cfg = VerlihubConfig(logging=LoggingConfig(level="WARNING"))
        cfg.setup_logging()  # Should not raise


# ======================================================================
# VerlihubConfig.from_yaml
# ======================================================================


class TestFromYaml:

    def test_from_yaml_basic(self, tmp_path):
        yaml_content = """
database:
  type: sqlite
  path: /tmp/test.db

api:
  port: 9999

hub:
  name: YamlHub
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)

        cfg = VerlihubConfig.from_yaml(str(p))
        assert cfg.database.type == "sqlite"
        assert cfg.database.path == "/tmp/test.db"
        assert cfg.api.port == 9999
        assert cfg.hub.name == "YamlHub"

    def test_from_yaml_hub_logo_and_hublist(self, tmp_path):
        yaml_content = """
hub:
  name: LogoHub
  logo: "https://cdn.example.com/logo.png"
  host: "hub.example.com:4111"
  hublist_servers:
    - "hl1.example.com"
    - "hl2.example.com"
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)

        cfg = VerlihubConfig.from_yaml(str(p))
        assert cfg.hub.name == "LogoHub"
        assert cfg.hub.logo == "https://cdn.example.com/logo.png"
        assert cfg.hub.host == "hub.example.com:4111"
        assert cfg.hub.hublist_servers == ["hl1.example.com", "hl2.example.com"]

    def test_from_yaml_sqlite_memory(self, tmp_path):
        yaml_content = """
database:
  type: sqlite
  path: ":memory:"
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)

        cfg = VerlihubConfig.from_yaml(str(p))
        assert cfg.database.path == ":memory:"
        assert cfg.database.get_url() == "sqlite+aiosqlite:///:memory:"

    def test_from_yaml_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            VerlihubConfig.from_yaml(str(tmp_path / "nope.yml"))


# ======================================================================
# load_config
# ======================================================================


class TestLoadConfig:

    def test_explicit_file(self, tmp_path):
        (tmp_path / "custom.yml").write_text("hub:\n  name: Custom\n")
        cfg = load_config(config_file=str(tmp_path / "custom.yml"),
                         config_dir=str(tmp_path))
        assert cfg.hub.name == "Custom"
        assert cfg._config_dir == str(tmp_path)

    def test_explicit_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(config_file=str(tmp_path / "nope.yml"))

    def test_config_dir_auto_discovery(self, tmp_path):
        (tmp_path / "config.yml").write_text("hub:\n  name: AutoHub\n")
        cfg = load_config(config_dir=str(tmp_path))
        assert cfg.hub.name == "AutoHub"

    def test_config_dir_verlihub_yml(self, tmp_path):
        (tmp_path / "verlihub.yml").write_text("hub:\n  name: VHHub\n")
        cfg = load_config(config_dir=str(tmp_path))
        assert cfg.hub.name == "VHHub"

    def test_fallback_to_defaults(self, tmp_path, monkeypatch):
        # Empty dir, no config files
        monkeypatch.chdir(tmp_path)
        # Clear env
        for key in list(os.environ):
            if key.startswith("VH_") or key.startswith("VERLIHUB_"):
                monkeypatch.delenv(key, raising=False)
        cfg = load_config(config_dir=str(tmp_path))
        assert cfg.database.type == "sqlite"
        assert cfg.database.path == str(tmp_path / "verlihub.db")


# ======================================================================
# YAML round-trip for registration settings
# ======================================================================


class TestRegistrationYamlRoundTrip:

    def test_yaml_registration_disabled(self, tmp_path):
        yaml_content = """
api:
  registration_enabled: false
  registration_require_invite: true
  registration_default_class: 2
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)
        cfg = VerlihubConfig.from_yaml(str(p))
        assert cfg.api.registration_enabled is False
        assert cfg.api.registration_require_invite is True
        assert cfg.api.registration_default_class == 2

    def test_yaml_registration_defaults_when_absent(self, tmp_path):
        yaml_content = """
api:
  port: 9999
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)
        cfg = VerlihubConfig.from_yaml(str(p))
        assert cfg.api.registration_enabled is True
        assert cfg.api.registration_require_invite is False
        assert cfg.api.registration_default_class == 1

    def test_env_overrides_yaml_registration(self, tmp_path, monkeypatch):
        """Env vars should override YAML registration settings."""
        yaml_content = """
api:
  registration_enabled: true
  registration_require_invite: false
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)

        # Load from YAML first
        cfg = VerlihubConfig.from_yaml(str(p))
        assert cfg.api.registration_enabled is True

        # Now override via env
        monkeypatch.setenv("VH_REGISTRATION_ENABLED", "0")
        monkeypatch.setenv("VH_REGISTRATION_REQUIRE_INVITE", "1")
        monkeypatch.setenv("VH_REGISTRATION_DEFAULT_CLASS", "3")
        cfg2 = VerlihubConfig.from_env()
        assert cfg2.api.registration_enabled is False
        assert cfg2.api.registration_require_invite is True
        assert cfg2.api.registration_default_class == 3
