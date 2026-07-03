"""
SQL Semantics Comparison Tests

This test suite verifies that SQL operations behave consistently
across different database backends (SQLite, MySQL, PostgreSQL).

These tests are designed to catch semantic differences that could
cause issues when moving between database systems.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

# Add package to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from verlihub.models import RegUser, Ban, SetupList, UserClass, BanType
from verlihub.models.database import Database, DatabaseConfig, init_database, close_database


# =============================================================================
# Database Configuration Helpers
# =============================================================================

def get_sqlite_config() -> DatabaseConfig:
    """Get SQLite in-memory database config."""
    return DatabaseConfig(db_type="sqlite")


def get_mysql_config() -> Optional[DatabaseConfig]:
    """Get MySQL config from environment, or None if not configured."""
    host = os.environ.get("VH_MYSQL_HOST")
    if not host:
        return None
    return DatabaseConfig(
        db_type="mysql",
        host=host,
        port=int(os.environ.get("VH_MYSQL_PORT", "3306")),
        user=os.environ.get("VH_MYSQL_USER", "verlihub"),
        password=os.environ.get("VH_MYSQL_PASSWORD", "verlihub"),
        database=os.environ.get("VH_MYSQL_DATABASE", "verlihub_test"),
    )


def get_postgres_config() -> Optional[DatabaseConfig]:
    """Get PostgreSQL config from environment, or None if not configured."""
    host = os.environ.get("VH_POSTGRES_HOST")
    if not host:
        return None
    return DatabaseConfig(
        db_type="postgresql",
        host=host,
        port=int(os.environ.get("VH_POSTGRES_PORT", "5432")),
        user=os.environ.get("VH_POSTGRES_USER", "verlihub"),
        password=os.environ.get("VH_POSTGRES_PASSWORD", "verlihub"),
        database=os.environ.get("VH_POSTGRES_DATABASE", "verlihub_test"),
    )


# =============================================================================
# Helpers
# =============================================================================

async def _clean_tables(db: Database, config: DatabaseConfig) -> None:
    """Delete all rows from every table for persistent backends."""
    if config.db_type == "sqlite":
        return  # in-memory DB is always fresh
    async with db._engine.begin() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            await conn.execute(table.delete())


# =============================================================================
# Test Cases
# =============================================================================

class TestCaseInsensitiveSearch:
    """Test case sensitivity behavior across databases."""
    
    async def run_test(self, config: DatabaseConfig) -> dict:
        """Run case sensitivity test on given database."""
        db = await init_database(config=config)
        
        results = {"backend": config.db_type}
        
        try:
            await _clean_tables(db, config)

            async with db._session_factory() as session:
                # Create users with different case
                users = [
                    RegUser(nick="TestUser", login_pwd="pwd", user_class=UserClass.REGISTERED,
                           reg_date=datetime.now(timezone.utc), reg_op="test"),
                    RegUser(nick="testuser", login_pwd="pwd", user_class=UserClass.REGISTERED,
                           reg_date=datetime.now(timezone.utc), reg_op="test"),
                    RegUser(nick="TESTUSER", login_pwd="pwd", user_class=UserClass.REGISTERED,
                           reg_date=datetime.now(timezone.utc), reg_op="test"),
                ]
                
                for user in users:
                    session.add(user)
                await session.commit()
                
                # Test case-insensitive search (LIKE with %)
                from sqlalchemy import select, func
                
                # Count users matching 'testuser' (case insensitive)
                stmt = select(func.count()).select_from(RegUser).where(
                    func.lower(RegUser.nick) == "testuser"
                )
                result = await session.execute(stmt)
                count = result.scalar()
                
                results["lower_match_count"] = count
                results["expected"] = 3
                results["passed"] = count == 3
                
        finally:
            await close_database()
        
        return results


class TestNullHandling:
    """Test NULL handling differences across databases."""
    
    async def run_test(self, config: DatabaseConfig) -> dict:
        """Run NULL handling test on given database."""
        db = await init_database(config=config)
        
        results = {"backend": config.db_type}
        
        try:
            await _clean_tables(db, config)

            async with db._session_factory() as session:
                # Create user with NULL login_last (a real nullable datetime field)
                user_null = RegUser(
                    nick="nulltest",
                    login_pwd="pwd",
                    user_class=UserClass.REGISTERED,
                    reg_date=datetime.now(timezone.utc),
                    reg_op="test",
                    login_last=None,  # Explicit NULL
                )
                # Create user with non-NULL login_last
                user_set = RegUser(
                    nick="settest",
                    login_pwd="pwd",
                    user_class=UserClass.REGISTERED,
                    reg_date=datetime.now(timezone.utc),
                    reg_op="test",
                    login_last=datetime.now(timezone.utc),
                )
                session.add(user_null)
                session.add(user_set)
                await session.commit()
                
                # Query for NULL login_last
                from sqlalchemy import select
                
                stmt = select(RegUser).where(RegUser.login_last.is_(None))
                result = await session.execute(stmt)
                null_users = result.scalars().all()
                
                # Query for non-NULL login_last
                stmt = select(RegUser).where(RegUser.login_last.isnot(None))
                result = await session.execute(stmt)
                non_null_users = result.scalars().all()
                
                results["null_count"] = len(null_users)
                results["non_null_count"] = len(non_null_users)
                results["passed"] = len(null_users) == 1 and len(non_null_users) == 1
                
        finally:
            await close_database()
        
        return results


class TestTimestampHandling:
    """Test timestamp/datetime handling across databases."""
    
    async def run_test(self, config: DatabaseConfig) -> dict:
        """Run timestamp handling test on given database."""
        db = await init_database(config=config)
        
        results = {"backend": config.db_type}
        
        try:
            await _clean_tables(db, config)

            async with db._session_factory() as session:
                # Create user with specific timestamp
                test_time = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
                
                user = RegUser(
                    nick="timetest",
                    login_pwd="pwd",
                    user_class=UserClass.REGISTERED,
                    reg_date=test_time,
                    reg_op="test",
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                
                # Check if timestamp was preserved
                retrieved_time = user.reg_date
                
                # Compare (allowing for timezone differences)
                if retrieved_time.tzinfo is None:
                    retrieved_time = retrieved_time.replace(tzinfo=timezone.utc)
                
                results["stored"] = str(test_time)
                results["retrieved"] = str(retrieved_time)
                
                # Check year, month, day, hour, minute, second match
                results["passed"] = (
                    retrieved_time.year == test_time.year and
                    retrieved_time.month == test_time.month and
                    retrieved_time.day == test_time.day and
                    retrieved_time.hour == test_time.hour and
                    retrieved_time.minute == test_time.minute and
                    retrieved_time.second == test_time.second
                )
                
        finally:
            await close_database()
        
        return results


class TestBooleanHandling:
    """Test boolean column handling across databases."""
    
    async def run_test(self, config: DatabaseConfig) -> dict:
        """Run boolean handling test on given database."""
        db = await init_database(config=config)
        
        results = {"backend": config.db_type}
        
        try:
            await _clean_tables(db, config)

            async with db._session_factory() as session:
                # Create ban with boolean-like flags
                ban = Ban(
                    ip="192.168.1.1",
                    nick="booltest",
                    ban_type=BanType.IP,
                    nick_op="test",
                    reason="Testing boolean handling",
                    date_start=datetime.now(timezone.utc),
                )
                session.add(ban)
                await session.commit()
                await session.refresh(ban)
                
                # Verify ban type flags
                results["ban_type"] = int(ban.ban_type)
                results["has_ip_flag"] = bool(ban.ban_type & BanType.IP)
                results["has_nick_flag"] = bool(ban.ban_type & BanType.NICK)
                results["passed"] = (
                    results["has_ip_flag"] == True and
                    results["has_nick_flag"] == False
                )
                
        finally:
            await close_database()
        
        return results


class TestStringLength:
    """Test string length handling across databases."""
    
    async def run_test(self, config: DatabaseConfig) -> dict:
        """Run string length test on given database."""
        db = await init_database(config=config)
        
        results = {"backend": config.db_type}
        
        try:
            await _clean_tables(db, config)

            async with db._session_factory() as session:
                # Test with various string lengths (all within the 64-char
                # nick column limit so MySQL doesn't reject them)
                short_nick = "a" * 10
                medium_nick = "b" * 40
                long_nick = "c" * 64
                
                users = []
                for nick in [short_nick, medium_nick, long_nick]:
                    user = RegUser(
                        nick=nick,
                        login_pwd="pwd",
                        user_class=UserClass.REGISTERED,
                        reg_date=datetime.now(timezone.utc),
                        reg_op="test",
                    )
                    session.add(user)
                    users.append(user)
                
                await session.commit()
                
                # Refresh and check lengths preserved
                for user in users:
                    await session.refresh(user)
                
                results["short_length"] = len(users[0].nick)
                results["medium_length"] = len(users[1].nick)  
                results["long_length"] = len(users[2].nick)
                results["passed"] = (
                    results["short_length"] == 10 and
                    results["medium_length"] == 40 and
                    results["long_length"] == 64
                )
                
        finally:
            await close_database()
        
        return results


class TestUnicodeHandling:
    """Test Unicode string handling across databases."""
    
    async def run_test(self, config: DatabaseConfig) -> dict:
        """Run Unicode handling test on given database."""
        db = await init_database(config=config)
        
        results = {"backend": config.db_type}
        
        try:
            await _clean_tables(db, config)

            async with db._session_factory() as session:
                # Test various Unicode strings
                test_strings = [
                    ("ascii", "plain_ascii"),
                    ("emoji", "user_🎉_emoji"),
                    ("chinese", "用户_chinese"),
                    ("arabic", "مستخدم_arabic"),
                    ("mixed", "Œßü_mixed_特殊"),
                ]
                
                users = []
                for label, nick in test_strings:
                    user = RegUser(
                        nick=nick,
                        login_pwd="pwd",
                        user_class=UserClass.REGISTERED,
                        reg_date=datetime.now(timezone.utc),
                        reg_op=label,
                    )
                    session.add(user)
                    users.append((label, nick, user))
                
                await session.commit()
                
                # Verify strings were preserved
                results["tests"] = {}
                all_passed = True
                
                for label, original, user in users:
                    await session.refresh(user)
                    passed = user.nick == original
                    results["tests"][label] = {
                        "original": original,
                        "retrieved": user.nick,
                        "passed": passed,
                    }
                    if not passed:
                        all_passed = False
                
                results["passed"] = all_passed
                
        finally:
            await close_database()
        
        return results


# =============================================================================
# Test Runner
# =============================================================================

async def run_all_tests():
    """Run all SQL semantics tests across available databases."""
    
    configs = [
        ("SQLite", get_sqlite_config()),
    ]
    
    mysql_config = get_mysql_config()
    if mysql_config:
        configs.append(("MySQL", mysql_config))
    else:
        print("⚠️  MySQL not configured (set VH_MYSQL_HOST)")
    
    postgres_config = get_postgres_config()
    if postgres_config:
        configs.append(("PostgreSQL", postgres_config))
    else:
        print("⚠️  PostgreSQL not configured (set VH_POSTGRES_HOST)")
    
    test_classes = [
        ("Case Sensitivity", TestCaseInsensitiveSearch()),
        ("NULL Handling", TestNullHandling()),
        ("Timestamp Handling", TestTimestampHandling()),
        ("Boolean Handling", TestBooleanHandling()),
        ("String Length", TestStringLength()),
        ("Unicode Handling", TestUnicodeHandling()),
    ]
    
    print("\n" + "=" * 60)
    print("SQL Semantics Comparison Test Results")
    print("=" * 60)
    
    all_passed = True
    
    for test_name, test_instance in test_classes:
        print(f"\n--- {test_name} ---")
        
        for db_name, config in configs:
            try:
                result = await test_instance.run_test(config)
                status = "✓" if result.get("passed") else "✗"
                print(f"  {db_name}: {status}")
                
                if not result.get("passed"):
                    all_passed = False
                    for key, value in result.items():
                        if key not in ("backend", "passed"):
                            print(f"    {key}: {value}")
                            
            except Exception as e:
                print(f"  {db_name}: ERROR - {e}")
                all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed - see details above")
    print("=" * 60 + "\n")
    
    return all_passed


def main():
    """Main entry point."""
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
