"""
Database connection and session management for Verlihub.

Uses SQLModel with async support for FastAPI integration.
Supports both MySQL (production) and SQLite (testing).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, AsyncEngine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Default database settings (can be overridden via environment)
DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = 3306
DEFAULT_DB_NAME = "verlihub"


class DatabaseConfig:
    """Database configuration loader."""
    
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        *,
        use_sqlite: bool = False,
        sqlite_path: Optional[str] = None,  # None = in-memory
    ) -> None:
        self.host = host or os.getenv("VH_DB_HOST", DEFAULT_DB_HOST)
        self.port = port or int(os.getenv("VH_DB_PORT", str(DEFAULT_DB_PORT)))
        self.database = database or os.getenv("VH_DB_NAME", DEFAULT_DB_NAME)
        self.username = username or os.getenv("VH_DB_USER", "")
        self.password = password or os.getenv("VH_DB_PASS", "")
        
        # SQLite options
        self.use_sqlite = use_sqlite or os.getenv("VH_USE_SQLITE", "").lower() in ("1", "true", "yes")
        self.sqlite_path = sqlite_path  # None means in-memory ":memory:"
    
    @classmethod
    def from_dbconfig(cls, config_dir: str | Path) -> "DatabaseConfig":
        """
        Load configuration from Verlihub's dbconfig.xml file.
        
        Args:
            config_dir: Path to verlihub configuration directory
            
        Returns:
            DatabaseConfig instance
        """
        import xml.etree.ElementTree as ET
        
        dbconfig_path = Path(config_dir) / "dbconfig.xml"
        if not dbconfig_path.exists():
            raise FileNotFoundError(f"dbconfig.xml not found in {config_dir}")
        
        tree = ET.parse(dbconfig_path)
        root = tree.getroot()
        
        # Parse XML structure
        # Expected format:
        # <mysql>
        #   <host>localhost</host>
        #   <user>username</user>
        #   <pass>password</pass>
        #   <db>verlihub</db>
        # </mysql>
        
        mysql = root.find("mysql")
        if mysql is None:
            raise ValueError("No <mysql> section in dbconfig.xml")
        
        host_elem = mysql.find("host")
        user_elem = mysql.find("user")
        pass_elem = mysql.find("pass")
        db_elem = mysql.find("db")
        
        return cls(
            host=host_elem.text if host_elem is not None else None,
            username=user_elem.text if user_elem is not None else None,
            password=pass_elem.text if pass_elem is not None else None,
            database=db_elem.text if db_elem is not None else None,
        )
    
    @property 
    def url(self) -> str:
        """Generate SQLAlchemy async connection URL."""
        if self.use_sqlite:
            # Use aiosqlite for async SQLite
            if self.sqlite_path:
                return f"sqlite+aiosqlite:///{self.sqlite_path}"
            else:
                # In-memory SQLite
                return "sqlite+aiosqlite:///:memory:"
        
        # URL-encode password to handle special characters
        password = quote_plus(self.password) if self.password else ""
        
        if self.username and password:
            auth = f"{self.username}:{password}@"
        elif self.username:
            auth = f"{self.username}@"
        else:
            auth = ""
        
        # Use asyncmy driver for async MySQL
        return f"mysql+asyncmy://{auth}{self.host}:{self.port}/{self.database}"
    
    @property
    def sync_url(self) -> str:
        """Generate SQLAlchemy sync connection URL (for migrations)."""
        if self.use_sqlite:
            if self.sqlite_path:
                return f"sqlite:///{self.sqlite_path}"
            else:
                return "sqlite:///:memory:"
        
        password = quote_plus(self.password) if self.password else ""
        
        if self.username and password:
            auth = f"{self.username}:{password}@"
        elif self.username:
            auth = f"{self.username}@"
        else:
            auth = ""
        
        return f"mysql+pymysql://{auth}{self.host}:{self.port}/{self.database}"


class Database:
    """
    Database connection manager.
    
    Example:
        db = Database(config)
        await db.connect()
        
        async with db.session() as session:
            users = await session.exec(select(RegUser))
        
        await db.disconnect()
    """
    
    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        
        # Configure engine based on database type
        if config.use_sqlite and not config.sqlite_path:
            # In-memory SQLite needs special handling
            # StaticPool ensures the same connection is reused (required for in-memory)
            self._engine: AsyncEngine = create_async_engine(
                config.url,
                echo=False,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        elif config.use_sqlite:
            # File-based SQLite
            self._engine = create_async_engine(
                config.url,
                echo=False,
                connect_args={"check_same_thread": False},
            )
        else:
            # MySQL/other databases
            self._engine = create_async_engine(
                config.url,
                echo=False,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
            )
        
        self._session_factory = sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    
    async def connect(self) -> None:
        """Initialize database connection and create tables."""
        async with self._engine.begin() as conn:
            # Create tables if they don't exist
            await conn.run_sync(SQLModel.metadata.create_all)
    
    async def disconnect(self) -> None:
        """Close database connection."""
        await self._engine.dispose()
    
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get an async database session."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    
    def get_session_dependency(self):
        """Get a FastAPI dependency for database sessions."""
        async def get_session() -> AsyncGenerator[AsyncSession, None]:
            async with self._session_factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
        
        return get_session


# Global database instance (set during app startup)
_database: Optional[Database] = None


def get_database() -> Database:
    """Get the global database instance."""
    if _database is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _database


async def init_database(
    config_dir: Optional[str | Path] = None,
    config: Optional[DatabaseConfig] = None,
) -> Database:
    """
    Initialize the global database instance.
    
    Args:
        config_dir: Path to verlihub config dir (uses dbconfig.xml)
        config: Direct DatabaseConfig (overrides config_dir)
        
    Returns:
        Database instance
    """
    global _database
    
    if config is None:
        if config_dir is None:
            raise ValueError("Either config_dir or config must be provided")
        config = DatabaseConfig.from_dbconfig(config_dir)
    
    _database = Database(config)
    await _database.connect()
    return _database


async def close_database() -> None:
    """Close the global database instance."""
    global _database
    
    if _database is not None:
        await _database.disconnect()
        _database = None


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get an async database session as a context manager.
    
    Usage:
        async with get_async_session() as session:
            result = await session.exec(select(User))
    """
    db = get_database()
    async with db._session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
