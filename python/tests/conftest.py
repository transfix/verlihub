"""
Pytest configuration and fixtures for Verlihub tests.

Provides:
- SQLite in-memory database for testing
- Database session fixtures
- Common test data fixtures
"""
import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator, Generator

import pytest
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


# =============================================================================
# Event Loop Configuration
# =============================================================================

@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Database Fixtures
# =============================================================================

@pytest.fixture(scope="function")
def sqlite_config() -> DatabaseConfig:
    """Create SQLite in-memory database config."""
    return DatabaseConfig(use_sqlite=True, sqlite_path=None)


@pytest_asyncio.fixture(scope="function")
async def db(sqlite_config: DatabaseConfig) -> AsyncGenerator[Database, None]:
    """
    Create a test database with SQLite in-memory.
    
    Creates all tables and yields the database instance.
    Tables are dropped after each test.
    """
    database = await init_database(config=sqlite_config)
    
    yield database
    
    # Cleanup
    await close_database()


@pytest_asyncio.fixture(scope="function")
async def db_session(db: Database) -> AsyncGenerator[AsyncSession, None]:
    """Get a database session for testing."""
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
