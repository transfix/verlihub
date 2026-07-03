"""
Database connection and session management for Verlihub.

Uses SQLModel with async support for FastAPI integration.
Supports SQLite (default), MySQL, and PostgreSQL with async drivers.
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

# Default database settings
DEFAULT_DB_TYPE = "sqlite"
DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = 3306
DEFAULT_DB_NAME = "verlihub"


class DatabaseConfig:
    """
    Database configuration loader.
    
    Supports:
    - SQLite (default): Uses aiosqlite driver
    - MySQL: Uses asyncmy driver
    - PostgreSQL: Uses asyncpg driver
    
    Can be configured via:
    - Direct URL (must be async-compatible)
    - Individual parameters
    - Environment variables
    """
    
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        *,
        driver: Optional[str] = None,
        db_type: Optional[str] = None,
        url: Optional[str] = None,
        use_sqlite: bool = False,
        sqlite_path: Optional[str] = None,  # None = in-memory
        config_dir: Optional[str] = None,  # For SQLite default path
    ) -> None:
        # Direct URL override
        self._url = url or os.getenv("VH_DB_URL", "")
        
        # Database type detection
        self._db_type = db_type or os.getenv("VH_DB_TYPE", "")
        if use_sqlite or os.getenv("VH_USE_SQLITE", "").lower() in ("1", "true", "yes"):
            self._db_type = "sqlite"
        
        # Connection parameters
        self.host = host or os.getenv("VH_DB_HOST", DEFAULT_DB_HOST)
        self.port = port or int(os.getenv("VH_DB_PORT", str(DEFAULT_DB_PORT)))
        self.database = database or os.getenv("VH_DB_NAME", DEFAULT_DB_NAME)
        self.user = user or os.getenv("VH_DB_USER", "")
        self.password = password or os.getenv("VH_DB_PASSWORD", os.getenv("VH_DB_PASS", ""))
        
        # Driver override
        self._driver = driver
        
        # SQLite options
        self.use_sqlite = self._db_type == "sqlite"
        self.sqlite_path = sqlite_path or os.getenv("VH_DB_PATH", "")
        self.config_dir = config_dir
        
        # Auto-detect type from driver if specified
        if self._driver:
            if "sqlite" in self._driver:
                self.use_sqlite = True
                self._db_type = "sqlite"
            elif "mysql" in self._driver or "asyncmy" in self._driver:
                self._db_type = "mysql"
            elif "postgres" in self._driver or "asyncpg" in self._driver:
                self._db_type = "postgresql"
    
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
            # Default to SQLite if no dbconfig.xml
            return cls(use_sqlite=True, config_dir=str(config_dir))
        
        tree = ET.parse(dbconfig_path)
        root = tree.getroot()
        
        # Parse XML structure
        mysql = root.find("mysql")
        if mysql is None:
            # Try SQLite config
            sqlite = root.find("sqlite")
            if sqlite is not None:
                path_elem = sqlite.find("path")
                return cls(
                    use_sqlite=True,
                    sqlite_path=path_elem.text if path_elem is not None else None,
                    config_dir=str(config_dir),
                )
            raise ValueError("No <mysql> or <sqlite> section in dbconfig.xml")
        
        host_elem = mysql.find("host")
        user_elem = mysql.find("user")
        pass_elem = mysql.find("pass")
        db_elem = mysql.find("db")
        
        return cls(
            host=host_elem.text if host_elem is not None else None,
            user=user_elem.text if user_elem is not None else None,
            password=pass_elem.text if pass_elem is not None else None,
            database=db_elem.text if db_elem is not None else None,
            db_type="mysql",
        )
    
    @classmethod
    def from_config(cls, config: "DatabaseConfig") -> "DatabaseConfig":
        """
        Create from verlihub.config.DatabaseConfig.
        
        Args:
            config: Config from YAML loader
            
        Returns:
            DatabaseConfig instance for database connection
        """
        # Handle the config module's DatabaseConfig
        db_type = getattr(config, "type", "sqlite")
        url = getattr(config, "url", "")
        
        if url:
            return cls(url=url)
        
        return cls(
            db_type=db_type,
            host=getattr(config, "host", DEFAULT_DB_HOST),
            port=getattr(config, "port", DEFAULT_DB_PORT),
            database=getattr(config, "name", DEFAULT_DB_NAME),
            user=getattr(config, "user", ""),
            password=getattr(config, "password", ""),
            sqlite_path=getattr(config, "path", ""),
        )
    
    @property 
    def url(self) -> str:
        """Generate SQLAlchemy async connection URL."""
        # Direct URL override
        if self._url:
            return self._url
        
        if self.use_sqlite:
            # Use aiosqlite for async SQLite
            if self.sqlite_path:
                return f"sqlite+aiosqlite:///{self.sqlite_path}"
            elif self.config_dir:
                # Default to verlihub.db in config directory
                db_path = Path(self.config_dir) / "verlihub.db"
                return f"sqlite+aiosqlite:///{db_path}"
            else:
                # In-memory SQLite
                return "sqlite+aiosqlite:///:memory:"
        
        # URL-encode password to handle special characters
        password = quote_plus(self.password) if self.password else ""
        
        if self.user and password:
            auth = f"{self.user}:{password}@"
        elif self.user:
            auth = f"{self.user}@"
        else:
            auth = ""
        
        # PostgreSQL
        if self._db_type in ("postgresql", "postgres"):
            driver = self._driver or "postgresql+asyncpg"
            port = self.port if self.port != 3306 else 5432
            return f"{driver}://{auth}{self.host}:{port}/{self.database}"
        
        # MySQL (default for non-sqlite)
        driver = self._driver or "mysql+asyncmy"
        return f"{driver}://{auth}{self.host}:{self.port}/{self.database}"
    
    @property
    def db_type(self) -> str:
        """Database type (sqlite, mysql, postgresql)."""
        return self._db_type or ("sqlite" if self.use_sqlite else "mysql")
    
    @property
    def driver(self) -> str:
        """Database driver name."""
        if self._driver:
            return self._driver
        if self.use_sqlite:
            return "sqlite+aiosqlite"
        if self._db_type in ("postgresql", "postgres"):
            return "postgresql+asyncpg"
        return "mysql+asyncmy"

    @property
    def sync_url(self) -> str:
        """Generate SQLAlchemy sync connection URL (for migrations)."""
        if self.use_sqlite:
            if self.sqlite_path:
                return f"sqlite:///{self.sqlite_path}"
            elif self.config_dir:
                db_path = Path(self.config_dir) / "verlihub.db"
                return f"sqlite:///{db_path}"
            else:
                return "sqlite:///:memory:"
        
        password = quote_plus(self.password) if self.password else ""
        
        if self.user and password:
            auth = f"{self.user}:{password}@"
        elif self.user:
            auth = f"{self.user}@"
        else:
            auth = ""
        
        # PostgreSQL
        if self._db_type in ("postgresql", "postgres"):
            port = self.port if self.port != 3306 else 5432
            return f"postgresql+psycopg2://{auth}{self.host}:{port}/{self.database}"
        
        # MySQL
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

        # Run lightweight migrations in a SEPARATE transaction so that a
        # migration failure (e.g. column already exists) doesn't roll back
        # the table creation above.
        try:
            async with self._engine.begin() as conn:
                await self._run_migrations(conn)
        except Exception:
            pass  # Migrations are best-effort
    
    async def disconnect(self) -> None:
        """Close database connection."""
        await self._engine.dispose()

    async def _run_migrations(self, conn) -> None:
        """Apply lightweight schema migrations (add missing columns)."""
        import logging
        from sqlalchemy import text
        _log = logging.getLogger(__name__)

        # Each entry: (table, column, SQL type default)
        _COLUMN_MIGRATIONS = [
            ("reglist", "email", "VARCHAR(256) DEFAULT ''"),
        ]

        dialect = self.config.db_type  # "sqlite", "mysql", "postgresql"

        for table, column, col_def in _COLUMN_MIGRATIONS:
            has_column = False
            try:
                if dialect == "sqlite":
                    # SQLite: use PRAGMA table_info
                    result = await conn.execute(text(f"PRAGMA table_info({table})"))
                    rows = result.fetchall()
                    has_column = any(row[1] == column for row in rows)
                else:
                    # PostgreSQL / MySQL: use information_schema
                    result = await conn.execute(
                        text("SELECT column_name FROM information_schema.columns "
                             "WHERE table_name = :tbl AND column_name = :col"),
                        {"tbl": table, "col": column},
                    )
                    has_column = result.first() is not None
            except Exception:
                pass  # Table may not exist yet — skip

            if not has_column:
                try:
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                    )
                    _log.info("Migration: added column %s.%s", table, column)
                except Exception:
                    pass  # Column likely already exists
    
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
