"""
YAML Configuration loader for Verlihub Python module.

Provides a unified way to configure:
- Database connection
- API server settings
- Hub settings (when running in Python mode)
- Plugin configuration
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    """
    Database connection configuration.
    
    Supports SQLite (default), MySQL, and PostgreSQL with async drivers.
    
    Configuration priority:
    1. If `url` is provided, it's used directly (must be async-compatible)
    2. If `type` is "sqlite" and no path, uses SQLite file in config directory
    3. Otherwise constructs URL from individual parameters
    
    Examples:
        # SQLite (default) - creates verlihub.db in config directory
        database:
          type: sqlite
          
        # SQLite with specific path
        database:
          type: sqlite
          path: /var/lib/verlihub/data.db
          
        # MySQL
        database:
          type: mysql
          host: localhost
          port: 3306
          name: verlihub
          user: verlihub
          password: secret
          
        # PostgreSQL  
        database:
          type: postgresql
          host: localhost
          port: 5432
          name: verlihub
          user: verlihub
          password: secret
          
        # Direct URL (advanced)
        database:
          url: postgresql+asyncpg://user:pass@host:5432/dbname
    """
    # Database type: "sqlite", "mysql", "postgresql"
    type: str = "sqlite"
    
    # Direct URL override (must use async driver)
    url: str = ""
    
    # SQLite specific
    path: str = ""  # Empty = use config_dir/verlihub.db
    
    # Traditional connection parameters (for mysql/postgresql)
    host: str = "localhost"
    port: int = 3306  # Will be auto-adjusted for postgres
    user: str = "verlihub"
    password: str = ""
    name: str = "verlihub"
    
    def get_url(self, config_dir: str = "") -> str:
        """
        Get async database URL.
        
        Args:
            config_dir: Configuration directory (for SQLite default path)
            
        Returns:
            SQLAlchemy async connection URL
        """
        from urllib.parse import quote_plus
        
        # Direct URL override
        if self.url:
            return self.url
        
        # SQLite
        if self.type == "sqlite":
            if self.path:
                db_path = self.path
            elif config_dir:
                db_path = str(Path(config_dir) / "verlihub.db")
            else:
                # In-memory for testing
                return "sqlite+aiosqlite:///:memory:"
            return f"sqlite+aiosqlite:///{db_path}"
        
        # Build auth string
        password = quote_plus(self.password) if self.password else ""
        if self.user and password:
            auth = f"{self.user}:{password}@"
        elif self.user:
            auth = f"{self.user}@"
        else:
            auth = ""
        
        # MySQL
        if self.type == "mysql":
            port = self.port if self.port != 5432 else 3306
            return f"mysql+asyncmy://{auth}{self.host}:{port}/{self.name}"
        
        # PostgreSQL
        if self.type in ("postgresql", "postgres"):
            port = self.port if self.port != 3306 else 5432
            return f"postgresql+asyncpg://{auth}{self.host}:{port}/{self.name}"
        
        raise ValueError(f"Unknown database type: {self.type}")
    
    def to_env(self) -> dict[str, str]:
        """Export as environment variables."""
        return {
            "VH_DB_TYPE": self.type,
            "VH_DB_HOST": self.host,
            "VH_DB_PORT": str(self.port),
            "VH_DB_USER": self.user,
            "VH_DB_PASSWORD": self.password,
            "VH_DB_NAME": self.name,
            "VH_DB_PATH": self.path,
        }
    
    def display_name(self, config_dir: str = "") -> str:
        """Get human-readable database connection description."""
        if self.url:
            # Mask password in URL for display
            url = self.url
            if "@" in url:
                parts = url.split("@", 1)
                if ":" in parts[0]:
                    scheme_user = parts[0].rsplit(":", 1)[0]
                    url = f"{scheme_user}:***@{parts[1]}"
            return url
        
        if self.type == "sqlite":
            if self.path:
                return f"sqlite:{self.path}"
            elif config_dir:
                return f"sqlite:{config_dir}/verlihub.db"
            return "sqlite::memory:"
        
        return f"{self.type}://{self.host}:{self.port}/{self.name}"


@dataclass
class ApiConfig:
    """API server configuration."""
    host: str = "127.0.0.1"
    port: int = 8000
    secret: str = ""
    username: str = "admin"
    password: str = ""
    token_expire_minutes: int = 60
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    secure_cookies: bool = False
    
    def to_env(self) -> dict[str, str]:
        """Export as environment variables."""
        env = {
            "VH_API_HOST": self.host,
            "VH_API_PORT": str(self.port),
            "VH_API_USERNAME": self.username,
            "VH_API_PASSWORD": self.password,
            "VH_JWT_EXPIRE_MINUTES": str(self.token_expire_minutes),
            "VH_CORS_ORIGINS": ",".join(self.cors_origins),
            "VH_SECURE_COOKIES": "1" if self.secure_cookies else "0",
        }
        if self.secret:
            env["VH_JWT_SECRET"] = self.secret
        return env


@dataclass
class HubConfig:
    """Hub runtime configuration."""
    name: str = "My DC++ Hub"
    description: str = "Welcome to my hub!"
    host: str = ""
    port: int = 4111
    listen_host: str = "0.0.0.0"
    owner: str = ""
    topic: str = ""
    category: str = ""
    encoding: str = "CP1252"
    motd_file: str = ""
    max_users: int = 1000


@dataclass
class BotConfig:
    """Bot configuration."""
    nick: str
    description: str = ""
    email: str = ""


@dataclass
class BotsConfig:
    """Configuration for hub bots."""
    security: BotConfig = field(default_factory=lambda: BotConfig(nick="Hub-Security", description="Hub security system"))
    op_chat: BotConfig = field(default_factory=lambda: BotConfig(nick="OpChat", description="Operator chat"))


@dataclass
class PluginEntry:
    """Single plugin configuration."""
    name: str
    enabled: bool = True
    autoload: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginsConfig:
    """Plugin management configuration."""
    directory: str = ""
    plugins: list[PluginEntry] = field(default_factory=list)


@dataclass
class LuaGithubScript:
    """A Lua script to fetch from a GitHub repository."""
    repo: str  # e.g. "Verlihub/ledokol"
    files: list[str] = field(default_factory=list)  # specific files, or empty for all


@dataclass
class LuaConfig:
    """
    Lua plugin and script configuration.
    
    Controls loading of the Lua plugin (liblua_pi.so) and manages
    Lua scripts such as ledokol (https://github.com/Verlihub/ledokol).
    
    Scripts listed in ``github_scripts`` are fetched from GitHub on
    startup and placed in the hub's scripts directory. Scripts in
    ``autoload`` are loaded via ``!luaload`` after the hub starts.
    """
    enabled: bool = True
    github_scripts: list[LuaGithubScript] = field(default_factory=list)
    autoload: list[str] = field(default_factory=list)
    ledokol_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    file: str = ""
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


@dataclass
class VerlihubConfig:
    """
    Complete Verlihub configuration.
    
    Can be loaded from YAML file or constructed programmatically.
    """
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    hub: HubConfig = field(default_factory=HubConfig)
    bots: BotsConfig = field(default_factory=BotsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    lua: LuaConfig = field(default_factory=LuaConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # Runtime mode
    mode: str = "api"  # "api", "hub", "both"
    environment: str = "development"  # "development", "qa", "production"
    
    # Internal: config directory (set by load_config)
    _config_dir: str = ""
    
    @classmethod
    def from_yaml(cls, path: str | Path) -> "VerlihubConfig":
        """
        Load configuration from YAML file.
        
        Args:
            path: Path to YAML configuration file
            
        Returns:
            VerlihubConfig instance
        """
        try:
            import yaml
        except ImportError:
            raise ImportError("PyYAML is required for YAML config support. Install with: pip install pyyaml")
        
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        
        return cls.from_dict(data)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerlihubConfig":
        """
        Load configuration from dictionary.
        
        Args:
            data: Configuration dictionary
            
        Returns:
            VerlihubConfig instance
        """
        config = cls()
        
        # Database
        if "database" in data:
            db = data["database"]
            config.database = DatabaseConfig(
                type=db.get("type", config.database.type),
                url=db.get("url", config.database.url),
                path=db.get("path", config.database.path),
                host=db.get("host", config.database.host),
                port=db.get("port", config.database.port),
                user=db.get("user", config.database.user),
                password=db.get("password", config.database.password),
                name=db.get("name", config.database.name),
            )
        
        # API
        if "api" in data:
            api = data["api"]
            config.api = ApiConfig(
                host=api.get("host", config.api.host),
                port=api.get("port", config.api.port),
                secret=api.get("secret", config.api.secret),
                username=api.get("username", config.api.username),
                password=api.get("password", config.api.password),
                token_expire_minutes=api.get("token_expire_minutes", config.api.token_expire_minutes),
                cors_origins=api.get("cors_origins", config.api.cors_origins),
                secure_cookies=api.get("secure_cookies", config.api.secure_cookies),
            )
        
        # Hub
        if "hub" in data:
            hub = data["hub"]
            config.hub = HubConfig(
                name=hub.get("name", config.hub.name),
                description=hub.get("description", config.hub.description),
                host=hub.get("host", config.hub.host),
                port=hub.get("port", config.hub.port),
                listen_host=hub.get("listen_host", config.hub.listen_host),
                owner=hub.get("owner", config.hub.owner),
                topic=hub.get("topic", config.hub.topic),
                category=hub.get("category", config.hub.category),
                encoding=hub.get("encoding", config.hub.encoding),
                motd_file=hub.get("motd_file", config.hub.motd_file),
                max_users=hub.get("max_users", config.hub.max_users),
            )
        
        # Bots
        if "bots" in data:
            bots = data["bots"]
            if "security" in bots:
                sec = bots["security"]
                config.bots.security = BotConfig(
                    nick=sec.get("nick", config.bots.security.nick),
                    description=sec.get("description", config.bots.security.description),
                )
            if "op_chat" in bots:
                op = bots["op_chat"]
                config.bots.op_chat = BotConfig(
                    nick=op.get("nick", config.bots.op_chat.nick),
                    description=op.get("description", config.bots.op_chat.description),
                )
        
        # Plugins
        if "plugins" in data:
            plugins = data["plugins"]
            config.plugins.directory = plugins.get("directory", config.plugins.directory)
            if "list" in plugins:
                config.plugins.plugins = [
                    PluginEntry(
                        name=p.get("name", ""),
                        enabled=p.get("enabled", True),
                        autoload=p.get("autoload", True),
                        config=p.get("config", {}),
                    )
                    for p in plugins.get("list", [])
                ]
        
        # Lua plugin & scripts
        if "lua" in data:
            lua = data["lua"]
            github_scripts = [
                LuaGithubScript(
                    repo=gs.get("repo", ""),
                    files=gs.get("files", []),
                )
                for gs in lua.get("github_scripts", [])
            ]
            config.lua = LuaConfig(
                enabled=lua.get("enabled", True),
                github_scripts=github_scripts,
                autoload=lua.get("autoload", []),
                ledokol_config=lua.get("ledokol_config", {}),
            )
        
        # Logging
        if "logging" in data:
            log = data["logging"]
            config.logging = LoggingConfig(
                level=log.get("level", config.logging.level),
                file=log.get("file", config.logging.file),
                format=log.get("format", config.logging.format),
            )
        
        # Runtime settings
        config.mode = data.get("mode", config.mode)
        config.environment = data.get("environment", config.environment)
        
        return config
    
    @classmethod
    def from_env(cls) -> "VerlihubConfig":
        """
        Load configuration from environment variables.
        
        Environment variable mapping:
        - VH_DB_TYPE -> database.type (sqlite, mysql, postgresql)
        - VH_USE_SQLITE -> database.type = sqlite (legacy)
        - VERLIHUB_DB_* -> database.*
        - VH_API_* -> api.*
        - VH_HUB_* -> hub.*
        - VH_MODE -> mode
        - VH_ENV -> environment
        """
        config = cls()
        
        # Database type - default to SQLite for easy startup
        db_type = os.getenv("VH_DB_TYPE", "sqlite")
        if os.getenv("VH_USE_SQLITE", "").lower() in ("1", "true", "yes"):
            db_type = "sqlite"
        
        # Database from environment
        config.database = DatabaseConfig(
            type=db_type,
            host=os.getenv("VERLIHUB_DB_HOST", config.database.host),
            port=int(os.getenv("VERLIHUB_DB_PORT", str(config.database.port))),
            user=os.getenv("VERLIHUB_DB_USER", config.database.user),
            password=os.getenv("VERLIHUB_DB_PASSWORD", config.database.password),
            name=os.getenv("VERLIHUB_DB_NAME", config.database.name),
            path=os.getenv("VH_DB_PATH", ""),
            url=os.getenv("VH_DB_URL", ""),
        )
        
        # API from environment
        cors_origins = os.getenv("VH_CORS_ORIGINS", "*")
        config.api = ApiConfig(
            host=os.getenv("VH_API_HOST", config.api.host),
            port=int(os.getenv("VH_API_PORT", str(config.api.port))),
            secret=os.getenv("VH_JWT_SECRET", config.api.secret),
            username=os.getenv("VH_API_USERNAME", config.api.username),
            password=os.getenv("VH_API_PASSWORD", config.api.password),
            token_expire_minutes=int(os.getenv("VH_JWT_EXPIRE_MINUTES", str(config.api.token_expire_minutes))),
            cors_origins=cors_origins.split(",") if cors_origins else ["*"],
            secure_cookies=os.getenv("VH_SECURE_COOKIES", "0") == "1",
        )
        
        # Hub from environment
        config.hub = HubConfig(
            name=os.getenv("VH_HUB_NAME", config.hub.name),
            port=int(os.getenv("VH_HUB_PORT", str(config.hub.port))),
            listen_host=os.getenv("VH_HUB_LISTEN", config.hub.listen_host),
            max_users=int(os.getenv("VH_HUB_MAX_USERS", str(config.hub.max_users))),
        )
        
        # Runtime
        config.mode = os.getenv("VH_MODE", config.mode)
        config.environment = os.getenv("VH_ENV", config.environment)
        
        return config
    
    def apply_to_env(self) -> None:
        """
        Export configuration to environment variables.
        
        This allows components that read from environment to use
        settings from the YAML config.
        """
        env_vars = {}
        env_vars.update(self.database.to_env())
        env_vars.update(self.api.to_env())
        
        for key, value in env_vars.items():
            os.environ[key] = value
        
        logger.debug("Applied %d environment variables from config", len(env_vars))
    
    def setup_logging(self) -> None:
        """Configure logging based on config settings."""
        level = getattr(logging, self.logging.level.upper(), logging.INFO)
        
        handlers: list[logging.Handler] = [logging.StreamHandler()]
        
        if self.logging.file:
            handlers.append(logging.FileHandler(self.logging.file))
        
        logging.basicConfig(
            level=level,
            format=self.logging.format,
            handlers=handlers,
        )
    
    def validate(self) -> list[str]:
        """
        Validate configuration and return list of warnings/errors.
        
        Returns:
            List of warning/error messages (empty if valid)
        """
        issues = []
        
        # Production checks
        if self.environment == "production":
            if not self.api.secret:
                issues.append("CRITICAL: No API secret set for production")
            if not self.api.password:
                issues.append("CRITICAL: No API password set for production")
            if "*" in self.api.cors_origins:
                issues.append("WARNING: Wildcard CORS origins in production")
            if not self.api.secure_cookies:
                issues.append("WARNING: Secure cookies disabled in production")
            if self.api.host == "0.0.0.0":
                issues.append("WARNING: API bound to all interfaces in production")
        
        # Database checks
        if not self.database.password:
            issues.append("WARNING: No database password set")
        
        return issues
    
    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "mode": self.mode,
            "environment": self.environment,
            "database": {
                "host": self.database.host,
                "port": self.database.port,
                "user": self.database.user,
                "name": self.database.name,
                # Don't include password in output
            },
            "api": {
                "host": self.api.host,
                "port": self.api.port,
                "username": self.api.username,
                "token_expire_minutes": self.api.token_expire_minutes,
                "cors_origins": self.api.cors_origins,
                "secure_cookies": self.api.secure_cookies,
            },
            "hub": {
                "name": self.hub.name,
                "port": self.hub.port,
                "listen_host": self.hub.listen_host,
                "max_users": self.hub.max_users,
            },
            "logging": {
                "level": self.logging.level,
                "file": self.logging.file,
            },
            "lua": {
                "enabled": self.lua.enabled,
                "github_scripts": [
                    {"repo": gs.repo, "files": gs.files}
                    for gs in self.lua.github_scripts
                ],
                "autoload": self.lua.autoload,
                "ledokol_config": self.lua.ledokol_config,
            },
        }


def load_config(
    config_file: Optional[str | Path] = None,
    config_dir: Optional[str | Path] = None,
) -> VerlihubConfig:
    """
    Load configuration from file or environment.
    
    Priority:
    1. Explicit config_file path
    2. config.yml in config_dir (or current dir if not specified)
    3. Common config file locations
    4. Sensible defaults (SQLite in config_dir, API on localhost:8000)
    
    When no config is found, verlihub-py starts with:
    - SQLite database in config_dir/verlihub.db
    - API server on 127.0.0.1:8000
    - Development mode
    
    Args:
        config_file: Explicit path to YAML config file
        config_dir: Directory to search for config.yml (defaults to cwd)
        
    Returns:
        VerlihubConfig instance
    """
    # Default config_dir to current working directory
    if not config_dir:
        config_dir = Path.cwd()
    else:
        config_dir = Path(config_dir)
    
    # Try explicit config file
    if config_file:
        path = Path(config_file)
        if path.exists():
            logger.info("Loading configuration from %s", path)
            config = VerlihubConfig.from_yaml(path)
            config._config_dir = str(config_dir)
            return config
        else:
            raise FileNotFoundError(f"Config file not found: {path}")
    
    # Try config_dir/config.yml or verlihub.yml
    for filename in ["config.yml", "verlihub.yml"]:
        path = config_dir / filename
        if path.exists():
            logger.info("Loading configuration from %s", path)
            config = VerlihubConfig.from_yaml(path)
            config._config_dir = str(config_dir)
            return config
    
    # Check common locations (only if config_dir is cwd)
    if config_dir == Path.cwd():
        search_paths = [
            Path.home() / ".verlihub" / "config.yml",
            Path("/etc/verlihub/config.yml"),
        ]
        
        for path in search_paths:
            if path.exists():
                logger.info("Loading configuration from %s", path)
                config = VerlihubConfig.from_yaml(path)
                config._config_dir = str(path.parent)
                return config
    
    # No config file found - use sensible defaults
    logger.info("No config file found, using defaults (SQLite in %s)", config_dir)
    config = VerlihubConfig.from_env()
    config._config_dir = str(config_dir)
    
    # Ensure database uses SQLite in config_dir by default
    if not config.database.url and config.database.type == "sqlite" and not config.database.path:
        config.database.path = str(config_dir / "verlihub.db")
    
    return config
