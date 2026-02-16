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
    """Database connection configuration."""
    host: str = "localhost"
    port: int = 3306
    user: str = "verlihub"
    password: str = ""
    name: str = "verlihub"
    
    def to_env(self) -> dict[str, str]:
        """Export as environment variables."""
        return {
            "VERLIHUB_DB_HOST": self.host,
            "VERLIHUB_DB_PORT": str(self.port),
            "VERLIHUB_DB_USER": self.user,
            "VERLIHUB_DB_PASSWORD": self.password,
            "VERLIHUB_DB_NAME": self.name,
        }


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
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # Runtime mode
    mode: str = "api"  # "api", "hub", "both"
    environment: str = "development"  # "development", "qa", "production"
    
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
        - VERLIHUB_DB_* -> database.*
        - VH_API_* -> api.*
        - VH_HUB_* -> hub.*
        - VH_MODE -> mode
        - VH_ENV -> environment
        """
        config = cls()
        
        # Database from environment
        config.database = DatabaseConfig(
            host=os.getenv("VERLIHUB_DB_HOST", config.database.host),
            port=int(os.getenv("VERLIHUB_DB_PORT", str(config.database.port))),
            user=os.getenv("VERLIHUB_DB_USER", config.database.user),
            password=os.getenv("VERLIHUB_DB_PASSWORD", config.database.password),
            name=os.getenv("VERLIHUB_DB_NAME", config.database.name),
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
        }


def load_config(
    config_file: Optional[str | Path] = None,
    config_dir: Optional[str | Path] = None,
) -> VerlihubConfig:
    """
    Load configuration from file or environment.
    
    Priority:
    1. Explicit config_file path
    2. config.yml in config_dir
    3. Environment variables
    
    Args:
        config_file: Explicit path to YAML config file
        config_dir: Directory to search for config.yml
        
    Returns:
        VerlihubConfig instance
    """
    # Try explicit config file
    if config_file:
        path = Path(config_file)
        if path.exists():
            logger.info("Loading configuration from %s", path)
            return VerlihubConfig.from_yaml(path)
        else:
            raise FileNotFoundError(f"Config file not found: {path}")
    
    # Try config_dir/config.yml
    if config_dir:
        path = Path(config_dir) / "config.yml"
        if path.exists():
            logger.info("Loading configuration from %s", path)
            return VerlihubConfig.from_yaml(path)
    
    # Check common locations
    search_paths = [
        Path.cwd() / "config.yml",
        Path.cwd() / "verlihub.yml",
        Path.home() / ".verlihub" / "config.yml",
        Path("/etc/verlihub/config.yml"),
    ]
    
    for path in search_paths:
        if path.exists():
            logger.info("Loading configuration from %s", path)
            return VerlihubConfig.from_yaml(path)
    
    # Fall back to environment variables
    logger.info("No config file found, using environment variables")
    return VerlihubConfig.from_env()
