"""
Pytest configuration and fixtures for Verlihub tests.

Provides:
- Multi-database backend support (SQLite, MySQL, PostgreSQL)
- Database session fixtures
- Common test data fixtures
- Hub context fixtures for plugin testing

Environment Variables:
- VH_DB_BACKEND: "sqlite" (default), "mysql", "postgresql"
- VH_MYSQL_HOST, VH_MYSQL_PORT, VH_MYSQL_USER, VH_MYSQL_PASSWORD, VH_MYSQL_DATABASE
- VH_POSTGRES_HOST, VH_POSTGRES_PORT, VH_POSTGRES_USER, VH_POSTGRES_PASSWORD, VH_POSTGRES_DATABASE
- VH_INTEGRATION_TESTS: "1" to enable integration tests
- VH_FULL_INTEGRATION: "1" to enable full integration tests (requires running hub)
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

import pytest

# Heavy dependencies (sqlalchemy, sqlmodel, pytest_asyncio) are optional.
# When running lightweight SWIG-only tests (e.g. via CTest's SwigWrapperTests)
# these packages may not be installed.  Guard them so conftest.py loads cleanly.
try:
    import pytest_asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlmodel import SQLModel

    from verlihub.models import (
        RegUser,
        Ban,
        SetupList,
        UserClass,
        BanType,
    )
    from verlihub.models.database import (
        Database,
        DatabaseConfig,
        init_database,
        close_database,
        get_database,
    )

    import verlihub.models.database as _db_module  # for restoring global _database

    _HAS_DB_DEPS = True
except ImportError:
    _HAS_DB_DEPS = False


# =============================================================================
# Path Setup for SWIG Module
# =============================================================================

_build_python_dir = Path(__file__).parent.parent.parent / "build" / "python"
if _build_python_dir.exists() and str(_build_python_dir) not in sys.path:
    sys.path.insert(0, str(_build_python_dir))


# =============================================================================
# Environment Configuration
# =============================================================================

def get_db_backend() -> str:
    """Get the configured database backend."""
    return os.environ.get("VH_DB_BACKEND", "sqlite").lower()


def get_mysql_config() -> dict:
    """Get MySQL configuration from environment."""
    return {
        "host": os.environ.get("VH_MYSQL_HOST", "localhost"),
        "port": int(os.environ.get("VH_MYSQL_PORT", "3306")),
        "user": os.environ.get("VH_MYSQL_USER", "verlihub"),
        "password": os.environ.get("VH_MYSQL_PASSWORD", "verlihub"),
        "database": os.environ.get("VH_MYSQL_DATABASE", "verlihub"),
    }


def get_postgres_config() -> dict:
    """Get PostgreSQL configuration from environment."""
    return {
        "host": os.environ.get("VH_POSTGRES_HOST", "localhost"),
        "port": int(os.environ.get("VH_POSTGRES_PORT", "5432")),
        "user": os.environ.get("VH_POSTGRES_USER", "verlihub"),
        "password": os.environ.get("VH_POSTGRES_PASSWORD", "verlihub"),
        "database": os.environ.get("VH_POSTGRES_DATABASE", "verlihub"),
    }


def is_integration_tests_enabled() -> bool:
    """Check if integration tests are enabled."""
    return os.environ.get("VH_INTEGRATION_TESTS") == "1"


def is_full_integration_enabled() -> bool:
    """Check if full integration tests are enabled."""
    return os.environ.get("VH_FULL_INTEGRATION") == "1"


# =============================================================================
# Database Configuration Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def sqlite_config() -> DatabaseConfig:
    """Create SQLite in-memory database config."""
    return DatabaseConfig(use_sqlite=True, sqlite_path=None)


@pytest.fixture(scope="session")
def mysql_config() -> DatabaseConfig:
    """Create MySQL database config from environment."""
    cfg = get_mysql_config()
    return DatabaseConfig(
        db_type="mysql",
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )


@pytest.fixture(scope="session")
def postgres_config() -> DatabaseConfig:
    """Create PostgreSQL database config from environment."""
    cfg = get_postgres_config()
    return DatabaseConfig(
        db_type="postgresql",
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )


@pytest.fixture(scope="session")
def db_config(sqlite_config, mysql_config, postgres_config) -> DatabaseConfig:
    """
    Get database config based on VH_DB_BACKEND environment variable.
    
    Defaults to SQLite in-memory for fastest testing.
    """
    backend = get_db_backend()
    
    if backend == "mysql":
        return mysql_config
    elif backend in ("postgresql", "postgres"):
        return postgres_config
    else:
        return sqlite_config


@pytest_asyncio.fixture(scope="session")
async def db(db_config: DatabaseConfig) -> AsyncGenerator[Database, None]:
    """
    Session-scoped test database.
    
    Creates the engine and tables ONCE for the entire test session.
    Per-test isolation is provided by the db_session fixture which uses
    transaction savepoints that are rolled back after each test.
    
    This avoids expensive DDL (DROP/CREATE) per test, which is the main
    bottleneck when running against MySQL/PostgreSQL over the network.
    
    Backend is controlled by VH_DB_BACKEND environment variable:
    - "sqlite" (default): SQLite in-memory database
    - "mysql": MySQL database
    - "postgresql": PostgreSQL database
    """
    database = await init_database(config=db_config)
    
    # For persistent backends (MySQL/PostgreSQL), drop and recreate tables
    # once at session start to ensure a clean schema.
    if not db_config.use_sqlite:
        async with database._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
            await conn.run_sync(SQLModel.metadata.create_all)
    
    yield database
    
    # Drop tables once at session end.
    if not db_config.use_sqlite and database._engine is not None:
        async with database._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
    
    await close_database()


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _clean_db_between_tests() -> AsyncGenerator[None, None]:
    """
    Autouse fixture: DELETE all rows from all tables between tests.
    
    Checks if a global database is initialized (via init_database()).  If so,
    deletes all row data — fast even for MySQL because no DDL is involved.
    
    On teardown, restores the global ``_database`` reference if a test cleared
    it by calling ``close_database()`` directly (e.g. test_database_config.py).
    """
    saved_db = _db_module._database
    if saved_db is not None and saved_db._engine is not None:
        try:
            async with saved_db._engine.begin() as conn:
                for table in reversed(SQLModel.metadata.sorted_tables):
                    await conn.execute(table.delete())
        except Exception:
            pass  # engine may be disposed; skip cleanup
    yield
    # Restore the global _database if a test cleared it.  The session-scoped
    # ``db`` fixture's Database instance is still alive, so just reassign.
    if saved_db is not None and _db_module._database is None:
        _db_module._database = saved_db


@pytest_asyncio.fixture(scope="function")
async def db_session(db: Database) -> AsyncGenerator[AsyncSession, None]:
    """
    Per-test database session with isolation.
    
    Uses a simple session from the factory for all backends.  Data cleanup
    is handled by the autouse ``_clean_db_between_tests`` fixture which
    DELETEs all rows between tests, so every test starts with empty tables.
    
    A previous implementation used a savepoint/rollback pattern for MySQL
    and PostgreSQL, but that caused data inserted via the test session to be
    invisible to production code that opens its own sessions (e.g.
    ``prune_stale_hubs``, ``OnValidateNick``).  The DELETE-based cleanup is
    sufficient and avoids the cross-session visibility problem.
    """
    async with db._session_factory() as session:
        yield session


# =============================================================================
# Test Data Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def test_users(db_session: AsyncSession) -> list[RegUser]:
    """Create test users in the database."""
    users = [
        RegUser(
            nick="admin_user",
            login_pwd="hashed_admin_password",
            user_class=UserClass.ADMIN,
            reg_date=datetime.now(timezone.utc),
            reg_op="setup",
        ),
        RegUser(
            nick="regular_user",
            login_pwd="hashed_user_password",
            user_class=UserClass.REGISTERED,
            reg_date=datetime.now(timezone.utc),
            reg_op="admin_user",
        ),
        RegUser(
            nick="vip_user",
            login_pwd="hashed_vip_password",
            user_class=UserClass.VIP,
            reg_date=datetime.now(timezone.utc),
            reg_op="admin_user",
        ),
        RegUser(
            nick="operator",
            login_pwd="hashed_op_password",
            user_class=UserClass.OPERATOR,
            reg_date=datetime.now(timezone.utc),
            reg_op="admin_user",
        ),
    ]
    
    for user in users:
        db_session.add(user)
    
    await db_session.commit()
    
    # Refresh to get IDs
    for user in users:
        await db_session.refresh(user)
    
    return users


@pytest_asyncio.fixture
async def test_bans(db_session: AsyncSession) -> list[Ban]:
    """Create test bans in the database."""
    bans = [
        Ban(
            ip="192.168.1.100",
            nick="banned_user1",
            ban_type=BanType.IP | BanType.NICK,
            nick_op="admin_user",
            reason="Spamming",
            date_start=datetime.now(timezone.utc),
        ),
        Ban(
            ip="10.0.0.0/8",
            nick="",
            ban_type=BanType.RANGE,
            nick_op="admin_user",
            reason="Range ban for abuse",
            date_start=datetime.now(timezone.utc),
        ),
        Ban(
            ip="",
            nick="badnick",
            ban_type=BanType.NICK,
            nick_op="operator",
            reason="Impersonation attempt",
            date_start=datetime.now(timezone.utc),
        ),
    ]
    
    for ban in bans:
        db_session.add(ban)
    
    await db_session.commit()
    
    # Refresh to get IDs
    for ban in bans:
        await db_session.refresh(ban)
    
    return bans


@pytest_asyncio.fixture
async def test_config(db_session: AsyncSession) -> list[SetupList]:
    """Create test hub configuration in the database."""
    config_items = [
        SetupList(file="config", var="hub_name", val="Test Hub"),
        SetupList(file="config", var="hub_host", val="localhost"),
        SetupList(file="config", var="hub_port", val="4111"),
        SetupList(file="config", var="max_users", val="1000"),
        SetupList(file="config", var="hub_desc", val="A test hub for unit testing"),
        SetupList(file="config", var="hub_owner", val="Test Admin"),
    ]
    
    for item in config_items:
        db_session.add(item)
    
    await db_session.commit()
    
    return config_items


# =============================================================================
# Combined Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def populated_db(
    db: Database,
    db_session: AsyncSession,
    test_users: list[RegUser],
    test_bans: list[Ban],
    test_config: list[SetupList],
) -> Database:
    """
    Database with test data already populated.
    
    Returns the database instance with users, bans, and config loaded.
    """
    return db


# =============================================================================
# Hub Context Fixtures (for Plugin Testing)
# =============================================================================

@pytest.fixture
def hub_context():
    """
    Create a HubContext for plugin testing.
    
    Returns a temporary hub context that can be used to test
    plugin loading and management.
    """
    from verlihub import verlihub_core
    
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = verlihub_core.HubContext.Create(tmpdir)
        if ctx is None:
            pytest.fail("Could not create HubContext")
        yield ctx


@pytest.fixture
def lua_plugin_path() -> Optional[str]:
    """Get the path to the Lua plugin library."""
    build_dir = Path(__file__).parent.parent.parent / "build"
    possible_paths = [
        build_dir / "plugins" / "lua" / "liblua_pi.so",
        build_dir / "plugins" / "lua" / "lua_pi.so",
        Path("/usr/lib/verlihub/liblua_pi.so"),
        Path("/usr/local/lib/verlihub/liblua_pi.so"),
    ]
    
    for path in possible_paths:
        if path.exists():
            return str(path)
    
    return None


@pytest.fixture
def python_plugin_path() -> Optional[str]:
    """Get the path to the Python plugin library."""
    build_dir = Path(__file__).parent.parent.parent / "build"
    possible_paths = [
        build_dir / "plugins" / "python" / "libpython_pi.so",
        build_dir / "plugins" / "python" / "python_pi.so",
        Path("/usr/lib/verlihub/libpython_pi.so"),
        Path("/usr/local/lib/verlihub/libpython_pi.so"),
    ]
    
    for path in possible_paths:
        if path.exists():
            return str(path)
    
    return None


# =============================================================================
# Test Markers
# =============================================================================

def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test (requires VH_INTEGRATION_TESTS=1)"
    )
    config.addinivalue_line(
        "markers",
        "full_integration: mark test as requiring a running hub (requires VH_FULL_INTEGRATION=1)"
    )
    config.addinivalue_line(
        "markers",
        "mysql: mark test as MySQL-specific"
    )
    config.addinivalue_line(
        "markers",
        "postgresql: mark test as PostgreSQL-specific"
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow-running"
    )


def pytest_collection_modifyitems(config, items):
    """Skip tests based on environment configuration."""
    skip_integration = pytest.mark.skip(reason="Requires VH_INTEGRATION_TESTS=1")
    skip_full = pytest.mark.skip(reason="Requires VH_FULL_INTEGRATION=1")
    skip_mysql = pytest.mark.skip(reason="Requires VH_DB_BACKEND=mysql")
    skip_postgres = pytest.mark.skip(reason="Requires VH_DB_BACKEND=postgresql")
    
    backend = get_db_backend()
    
    for item in items:
        # Skip integration tests unless enabled
        if "integration" in item.keywords and not is_integration_tests_enabled():
            item.add_marker(skip_integration)
        
        # Skip full integration tests unless enabled
        if "full_integration" in item.keywords and not is_full_integration_enabled():
            item.add_marker(skip_full)
        
        # Skip MySQL-specific tests unless using MySQL backend
        if "mysql" in item.keywords and backend != "mysql":
            item.add_marker(skip_mysql)
        
        # Skip PostgreSQL-specific tests unless using PostgreSQL backend
        if "postgresql" in item.keywords and backend not in ("postgresql", "postgres"):
            item.add_marker(skip_postgres)
