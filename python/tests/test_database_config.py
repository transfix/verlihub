"""
Tests for verlihub.models.database — DatabaseConfig URL construction,
properties, from_dbconfig XML parsing, Database engine init, session management.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from verlihub.models.database import (
    DatabaseConfig,
    Database,
    get_database,
    init_database,
    close_database,
    get_async_session,
)


# ======================================================================
# DatabaseConfig URL construction
# ======================================================================


class TestDatabaseConfigUrl:

    def test_direct_url_override(self):
        cfg = DatabaseConfig(url="postgresql+asyncpg://user:pw@host/db")
        assert cfg.url == "postgresql+asyncpg://user:pw@host/db"

    def test_sqlite_in_memory_default(self):
        cfg = DatabaseConfig(use_sqlite=True)
        assert cfg.url == "sqlite+aiosqlite:///:memory:"

    def test_sqlite_with_path(self):
        cfg = DatabaseConfig(use_sqlite=True, sqlite_path="/data/test.db")
        assert cfg.url == "sqlite+aiosqlite:////data/test.db"

    def test_sqlite_with_config_dir(self):
        cfg = DatabaseConfig(use_sqlite=True, config_dir="/opt/verlihub")
        assert "verlihub.db" in cfg.url
        assert cfg.url.startswith("sqlite+aiosqlite:///")

    def test_mysql_url(self):
        cfg = DatabaseConfig(
            host="dbhost", port=3306, database="vh",
            user="root", password="pw", db_type="mysql"
        )
        assert cfg.url == "mysql+asyncmy://root:pw@dbhost:3306/vh"

    def test_mysql_user_only(self):
        cfg = DatabaseConfig(
            host="h", port=3306, database="n",
            user="viewer", db_type="mysql"
        )
        assert "viewer@h:3306" in cfg.url

    def test_postgresql_url(self):
        cfg = DatabaseConfig(
            host="pghost", port=5432, database="mydb",
            user="pg", password="pass", db_type="postgresql"
        )
        assert cfg.url == "postgresql+asyncpg://pg:pass@pghost:5432/mydb"

    def test_postgresql_auto_corrects_port(self):
        cfg = DatabaseConfig(
            host="h", port=3306, database="n",
            user="u", password="p", db_type="postgresql"
        )
        assert ":5432/" in cfg.url

    def test_url_encodes_special_password(self):
        cfg = DatabaseConfig(
            host="h", port=3306, database="n",
            user="u", password="p@ss!", db_type="mysql"
        )
        assert "p%40ss%21" in cfg.url

    def test_custom_driver(self):
        cfg = DatabaseConfig(
            host="h", port=5432, database="n",
            user="u", password="p",
            driver="postgresql+asyncpg", db_type="postgresql"
        )
        assert "postgresql+asyncpg://" in cfg.url


# ======================================================================
# DatabaseConfig properties
# ======================================================================


class TestDatabaseConfigProperties:

    def test_db_type_sqlite(self):
        cfg = DatabaseConfig(use_sqlite=True)
        assert cfg.db_type == "sqlite"

    def test_db_type_mysql_default(self):
        cfg = DatabaseConfig(db_type="mysql")
        assert cfg.db_type == "mysql"

    def test_db_type_fallback(self):
        cfg = DatabaseConfig()
        assert cfg.db_type in ("sqlite", "mysql")

    def test_driver_sqlite(self):
        cfg = DatabaseConfig(use_sqlite=True)
        assert cfg.driver == "sqlite+aiosqlite"

    def test_driver_mysql(self):
        cfg = DatabaseConfig(db_type="mysql")
        assert cfg.driver == "mysql+asyncmy"

    def test_driver_postgresql(self):
        cfg = DatabaseConfig(db_type="postgresql")
        assert cfg.driver == "postgresql+asyncpg"

    def test_driver_custom_override(self):
        cfg = DatabaseConfig(driver="custom+driver")
        assert cfg.driver == "custom+driver"

    def test_sync_url_sqlite_memory(self):
        cfg = DatabaseConfig(use_sqlite=True)
        assert cfg.sync_url == "sqlite:///:memory:"

    def test_sync_url_sqlite_path(self):
        cfg = DatabaseConfig(use_sqlite=True, sqlite_path="/data/test.db")
        assert cfg.sync_url == "sqlite:////data/test.db"

    def test_sync_url_sqlite_config_dir(self):
        cfg = DatabaseConfig(use_sqlite=True, config_dir="/opt/vh")
        assert "verlihub.db" in cfg.sync_url

    def test_sync_url_mysql(self):
        cfg = DatabaseConfig(
            host="h", port=3306, database="n",
            user="u", password="p", db_type="mysql"
        )
        assert "pymysql://" in cfg.sync_url

    def test_sync_url_postgresql(self):
        cfg = DatabaseConfig(
            host="h", port=5432, database="n",
            user="u", password="p", db_type="postgresql"
        )
        assert "psycopg2://" in cfg.sync_url


# ======================================================================
# DatabaseConfig.from_dbconfig (XML parsing)
# ======================================================================


class TestFromDbconfig:

    def test_mysql_xml(self, tmp_path):
        xml = """<?xml version="1.0"?>
<dbconfig>
  <mysql>
    <host>db.local</host>
    <user>verlihub</user>
    <pass>secret</pass>
    <db>verlihub_db</db>
  </mysql>
</dbconfig>"""
        (tmp_path / "dbconfig.xml").write_text(xml)
        cfg = DatabaseConfig.from_dbconfig(tmp_path)
        assert cfg.host == "db.local"
        assert cfg.user == "verlihub"
        assert cfg.password == "secret"
        assert cfg.database == "verlihub_db"
        assert cfg.db_type == "mysql"

    def test_sqlite_xml(self, tmp_path):
        xml = """<?xml version="1.0"?>
<dbconfig>
  <sqlite>
    <path>/data/verlihub.db</path>
  </sqlite>
</dbconfig>"""
        (tmp_path / "dbconfig.xml").write_text(xml)
        cfg = DatabaseConfig.from_dbconfig(tmp_path)
        assert cfg.use_sqlite is True
        assert cfg.sqlite_path == "/data/verlihub.db"

    def test_no_dbconfig_file_uses_sqlite(self, tmp_path):
        cfg = DatabaseConfig.from_dbconfig(tmp_path)
        assert cfg.use_sqlite is True

    def test_invalid_xml_raises(self, tmp_path):
        xml = """<?xml version="1.0"?>
<dbconfig>
</dbconfig>"""
        (tmp_path / "dbconfig.xml").write_text(xml)
        with pytest.raises(ValueError, match="No <mysql> or <sqlite>"):
            DatabaseConfig.from_dbconfig(tmp_path)


# ======================================================================
# DatabaseConfig.from_config
# ======================================================================


class TestFromConfig:

    def test_from_config_url(self):
        mock_cfg = type("C", (), {"type": "sqlite", "url": "sqlite+aiosqlite:///test.db"})()
        cfg = DatabaseConfig.from_config(mock_cfg)
        assert cfg.url == "sqlite+aiosqlite:///test.db"

    def test_from_config_mysql(self):
        mock_cfg = type("C", (), {
            "type": "mysql", "url": "", "host": "h", "port": 3306,
            "name": "db", "user": "u", "password": "p", "path": ""
        })()
        cfg = DatabaseConfig.from_config(mock_cfg)
        assert cfg.db_type == "mysql"
        assert cfg.host == "h"


# ======================================================================
# DatabaseConfig auto-detect from driver
# ======================================================================


class TestDriverAutoDetect:

    def test_sqlite_driver_sets_sqlite(self):
        cfg = DatabaseConfig(driver="sqlite+aiosqlite")
        assert cfg.use_sqlite is True
        assert cfg.db_type == "sqlite"

    def test_mysql_driver_sets_mysql(self):
        cfg = DatabaseConfig(driver="mysql+asyncmy")
        assert cfg.db_type == "mysql"

    def test_asyncpg_driver_sets_postgresql(self):
        cfg = DatabaseConfig(driver="postgresql+asyncpg")
        assert cfg.db_type == "postgresql"


# ======================================================================
# Database engine init branches
# ======================================================================


class TestDatabaseInit:

    def test_in_memory_sqlite(self):
        cfg = DatabaseConfig(use_sqlite=True)
        db = Database(cfg)
        assert db._engine is not None

    def test_file_sqlite(self, tmp_path):
        cfg = DatabaseConfig(use_sqlite=True, sqlite_path=str(tmp_path / "test.db"))
        db = Database(cfg)
        assert db._engine is not None


# ======================================================================
# Database session management
# ======================================================================


class TestDatabaseSession:

    async def test_session_yields_and_commits(self):
        cfg = DatabaseConfig(use_sqlite=True)
        db = Database(cfg)
        await db.connect()
        try:
            async for session in db.session():
                assert session is not None
        finally:
            await db.disconnect()

    async def test_get_session_dependency(self):
        cfg = DatabaseConfig(use_sqlite=True)
        db = Database(cfg)
        await db.connect()
        try:
            dep = db.get_session_dependency()
            async for session in dep():
                assert session is not None
        finally:
            await db.disconnect()


# ======================================================================
# get_database / init_database / close_database
# ======================================================================


class TestGlobalDatabase:

    async def test_get_database_before_init_raises(self):
        import verlihub.models.database as db_mod
        old = db_mod._database
        db_mod._database = None
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                get_database()
        finally:
            db_mod._database = old

    async def test_init_and_close(self):
        cfg = DatabaseConfig(use_sqlite=True)
        db = await init_database(config=cfg)
        assert db is not None
        assert get_database() is db
        await close_database()

    async def test_init_requires_config_or_dir(self):
        with pytest.raises(ValueError, match="Either config_dir or config"):
            await init_database()

    async def test_init_from_config_dir(self, tmp_path):
        """When given config_dir with no dbconfig.xml, should default to SQLite."""
        db = await init_database(config_dir=str(tmp_path))
        assert db is not None
        await close_database()


# ======================================================================
# get_async_session
# ======================================================================


class TestGetAsyncSession:

    async def test_get_async_session(self):
        cfg = DatabaseConfig(use_sqlite=True)
        await init_database(config=cfg)
        try:
            async with get_async_session() as session:
                assert session is not None
        finally:
            await close_database()
