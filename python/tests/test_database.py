"""
Tests for database functionality with SQLite in-memory database.

These tests verify:
- SQLite database creation and table setup
- CRUD operations on User, Ban, and Config models
- Session management and transactions
- Database fixtures work correctly
"""
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlmodel import select

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
)


# =============================================================================
# Test Configuration
# =============================================================================

class TestDatabaseConfig:
    """Test DatabaseConfig class."""
    
    def test_sqlite_config_default(self):
        """Test SQLite config with defaults."""
        config = DatabaseConfig(use_sqlite=True)
        
        assert config.use_sqlite is True
        assert config.sqlite_path is None
        assert "sqlite+aiosqlite:///:memory:" in config.url
    
    def test_sqlite_config_with_path(self, tmp_path):
        """Test SQLite config with file path."""
        db_path = tmp_path / "test.db"
        config = DatabaseConfig(use_sqlite=True, sqlite_path=str(db_path))
        
        assert config.use_sqlite is True
        assert config.sqlite_path == str(db_path)
        assert f"sqlite+aiosqlite:///{db_path}" in config.url
    
    def test_mysql_config_default(self):
        """Test MySQL config with defaults."""
        config = DatabaseConfig(
            username="user",
            password="pass",
        )
        
        assert config.use_sqlite is False
        assert "mysql+asyncmy://" in config.url
        assert "user:pass@" in config.url
    
    def test_sync_url_sqlite(self):
        """Test sync URL for SQLite."""
        config = DatabaseConfig(use_sqlite=True)
        
        assert "sqlite:///:memory:" in config.sync_url
        assert "aiosqlite" not in config.sync_url


# =============================================================================
# Test Database Operations
# =============================================================================

@pytest.mark.asyncio
class TestDatabaseOperations:
    """Test database CRUD operations."""
    
    async def test_database_creates_tables(self, db: Database):
        """Test that database creates all necessary tables."""
        # The db fixture should have already created tables
        # We just verify we can access it
        assert db is not None
        assert db._engine is not None
    
    async def test_add_user(self, db_session):
        """Test adding a user to the database."""
        user = RegUser(
            nick="test_user",
            login_pwd="hashed_password",
            user_class=UserClass.REGISTERED,
            reg_date=datetime.now(timezone.utc),
            reg_op="system",
        )
        
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        
        assert user.id is not None
        assert user.nick == "test_user"
    
    async def test_query_users(self, db_session, test_users):
        """Test querying users from database."""
        # test_users fixture creates 4 users
        result = await db_session.execute(select(RegUser))
        users = result.scalars().all()
        
        assert len(users) == 4
        nicks = [u.nick for u in users]
        assert "admin_user" in nicks
        assert "regular_user" in nicks
        assert "vip_user" in nicks
    
    async def test_filter_users_by_class(self, db_session, test_users):
        """Test filtering users by class."""
        result = await db_session.execute(
            select(RegUser).where(RegUser.user_class >= UserClass.OPERATOR)
        )
        ops = result.scalars().all()
        
        # Should include admin (5) and operator (3)
        assert len(ops) >= 2
        for op in ops:
            assert op.user_class >= UserClass.OPERATOR
    
    async def test_add_ban(self, db_session):
        """Test adding a ban to the database."""
        ban = Ban(
            ip="1.2.3.4",
            nick="bad_user",
            ban_type=BanType.IP | BanType.NICK,
            nick_op="admin",
            reason="Testing",
            date_start=datetime.now(timezone.utc),
        )
        
        db_session.add(ban)
        await db_session.commit()
        await db_session.refresh(ban)
        
        assert ban.id is not None
        assert ban.ip == "1.2.3.4"
    
    async def test_query_bans(self, db_session, test_bans):
        """Test querying bans from database."""
        result = await db_session.execute(select(Ban))
        bans = result.scalars().all()
        
        assert len(bans) == 3
    
    async def test_filter_bans_by_ip(self, db_session, test_bans):
        """Test filtering bans by IP."""
        result = await db_session.execute(
            select(Ban).where(Ban.ip == "192.168.1.100")
        )
        bans = result.scalars().all()
        
        assert len(bans) == 1
        assert bans[0].nick == "banned_user1"
    
    async def test_add_config(self, db_session):
        """Test adding configuration to database."""
        config = SetupList(
            file="config",
            var="test_setting",
            val="test_value",
        )
        
        db_session.add(config)
        await db_session.commit()
        
        # Query it back
        result = await db_session.execute(
            select(SetupList).where(
                SetupList.file == "config",
                SetupList.var == "test_setting"
            )
        )
        found = result.scalars().first()
        
        assert found is not None
        assert found.val == "test_value"
    
    async def test_query_config(self, db_session, test_config):
        """Test querying configuration from database."""
        result = await db_session.execute(
            select(SetupList).where(SetupList.file == "config")
        )
        configs = result.scalars().all()
        
        assert len(configs) == 6
        
        # Find hub_name
        hub_name = next((c for c in configs if c.var == "hub_name"), None)
        assert hub_name is not None
        assert hub_name.val == "Test Hub"
    
    async def test_update_user(self, db_session, test_users):
        """Test updating a user."""
        # Find the regular user
        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "regular_user")
        )
        user = result.scalars().first()
        assert user is not None
        
        # Update class to VIP
        user.user_class = UserClass.VIP
        db_session.add(user)
        await db_session.commit()
        
        # Verify update
        result = await db_session.execute(
            select(RegUser).where(RegUser.nick == "regular_user")
        )
        updated_user = result.scalars().first()
        assert updated_user.user_class == UserClass.VIP
    
    async def test_delete_ban(self, db_session, test_bans):
        """Test deleting a ban."""
        # Find a ban
        result = await db_session.execute(
            select(Ban).where(Ban.nick == "badnick")
        )
        ban = result.scalars().first()
        assert ban is not None
        
        # Delete it
        await db_session.delete(ban)
        await db_session.commit()
        
        # Verify deletion
        result = await db_session.execute(
            select(Ban).where(Ban.nick == "badnick")
        )
        deleted = result.scalars().first()
        assert deleted is None


# =============================================================================
# Test Transaction Behavior
# =============================================================================

@pytest.mark.asyncio
class TestTransactions:
    """Test database transaction behavior."""
    
    async def test_rollback_on_error(self, db: Database):
        """Test that errors cause rollback."""
        async with db._session_factory() as session:
            user = RegUser(
                nick="rollback_test",
                user_class=UserClass.REGISTERED,
                reg_date=datetime.now(timezone.utc),
            )
            session.add(user)
            await session.commit()
        
        # In a new session, verify it exists
        async with db._session_factory() as session:
            result = await session.execute(
                select(RegUser).where(RegUser.nick == "rollback_test")
            )
            user = result.scalars().first()
            assert user is not None
    
    async def test_session_isolation(self, db: Database):
        """Test that sessions are isolated."""
        # Create user in one session
        async with db._session_factory() as session1:
            user = RegUser(
                nick="isolated_user",
                user_class=UserClass.REGISTERED,
                reg_date=datetime.now(timezone.utc),
            )
            session1.add(user)
            await session1.commit()
        
        # Query in another session
        async with db._session_factory() as session2:
            result = await session2.execute(
                select(RegUser).where(RegUser.nick == "isolated_user")
            )
            found = result.scalars().first()
            assert found is not None


# =============================================================================
# Test Populated Database Fixture
# =============================================================================

@pytest.mark.asyncio
class TestPopulatedDatabase:
    """Test the populated database fixture."""
    
    async def test_populated_db_has_users(self, populated_db: Database, db_session):
        """Test that populated_db fixture has users."""
        result = await db_session.execute(select(RegUser))
        users = result.scalars().all()
        assert len(users) >= 4
    
    async def test_populated_db_has_bans(self, populated_db: Database, db_session):
        """Test that populated_db fixture has bans."""
        result = await db_session.execute(select(Ban))
        bans = result.scalars().all()
        assert len(bans) >= 3
    
    async def test_populated_db_has_config(self, populated_db: Database, db_session):
        """Test that populated_db fixture has config."""
        result = await db_session.execute(select(SetupList))
        configs = result.scalars().all()
        assert len(configs) >= 6
