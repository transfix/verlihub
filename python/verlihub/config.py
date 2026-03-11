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
    token_expire_minutes: int = 60
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    secure_cookies: bool = False
    registration_enabled: bool = True
    registration_require_invite: bool = False
    registration_default_class: int = 1  # REGISTERED
    registration_require_email: bool = True
    registration_check_email_deliverability: bool = False
    registration_block_disposable_emails: bool = True
    
    def to_env(self) -> dict[str, str]:
        """Export as environment variables."""
        env = {
            "VH_API_HOST": self.host,
            "VH_API_PORT": str(self.port),
            "VH_JWT_EXPIRE_MINUTES": str(self.token_expire_minutes),
            "VH_CORS_ORIGINS": ",".join(self.cors_origins),
            "VH_SECURE_COOKIES": "1" if self.secure_cookies else "0",
            "VH_REGISTRATION_ENABLED": "1" if self.registration_enabled else "0",
            "VH_REGISTRATION_REQUIRE_INVITE": "1" if self.registration_require_invite else "0",
            "VH_REGISTRATION_DEFAULT_CLASS": str(self.registration_default_class),
            "VH_REGISTRATION_REQUIRE_EMAIL": "1" if self.registration_require_email else "0",
            "VH_REGISTRATION_CHECK_EMAIL_DELIVERABILITY": "1" if self.registration_check_email_deliverability else "0",
            "VH_REGISTRATION_BLOCK_DISPOSABLE_EMAILS": "1" if self.registration_block_disposable_emails else "0",
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
    motd: str = ""  # Inline MOTD text; written to <config_dir>/motd on startup
    motd_file: str = ""
    max_users: int = 1000
    logo: str = ""  # URL to hub logo image; empty uses default Verlihub logo
    send_user_info: bool = True  # Send user info (nick, IP, country, TLS) on connect
    user_info_as_pm: bool = False  # Deliver user info as PM instead of main chat
    hublist_servers: list[str] = field(default_factory=lambda: [
        "hublist.te-home.net",
        "hublist.pwiam.com",
    ])


@dataclass
class HubListConfig:
    """
    Hublist server configuration.

    When ``server_enabled`` is True this Verlihub-py instance acts as a
    hublist directory that other hubs can register on.

    ``registration_interval`` controls how often (in seconds) we re-register
    this hub on external hublist servers listed in ``HubConfig.hublist_servers``.

    ``stale_timeout`` is how long (in seconds) a hub entry can go without a
    registration ping before it is automatically pruned from the directory.
    """
    server_enabled: bool = False  # serve a hublist directory on /api/v1/hublist
    registration_interval: int = 600  # 10 minutes
    stale_timeout: int = 1800  # 30 minutes


@dataclass
class BotConfig:
    """Bot configuration."""
    nick: str
    description: str = ""
    email: str = ""


@dataclass
class BotBehaviorConfig:
    """Configures how the hub security bot interacts with users.

    All fields are optional — sensible defaults are applied.
    """
    # Which LLM endpoint the bot should use (name from llm.endpoints).
    # Empty string → use the default (first) endpoint.
    endpoint: str = ""

    # Personality / persona injected into every system prompt.
    personality: str = ""

    # How eagerly the bot responds to non-direct mentions in main chat.
    # "direct"  — only respond when addressed by name  (e.g. "Hub-Security: hi")
    # "mention" — also respond when the bot name appears anywhere in the message
    # "keyword" — respond to bot name OR any keyword in ``triggers``
    chat_mode: str = "direct"

    # Extra keywords (besides the bot nick) that trigger a main-chat response.
    # Only used when ``chat_mode`` is "keyword".
    triggers: list[str] = field(default_factory=list)

    # Proactive behaviours — the bot occasionally initiates conversation.
    # Base interval in seconds between proactive messages (0 = disabled).
    # The actual delay is randomised ±30 % around this value so the bot
    # doesn't feel robotic.
    proactive_interval: int = 0

    # Proactive messages: list of prompts the bot can choose from.
    proactive_prompts: list[str] = field(default_factory=list)

    # Periodic "thinking…" feedback interval in seconds for PM sessions
    # while the LLM is processing.  0 = no periodic feedback.
    thinking_interval: int = 15

    # Max response length (characters) for main-chat replies.
    # Keeps the bot from flooding the chat room.
    max_chat_length: int = 400

    # ── Dynamic mood ────────────────────────────────────────────────
    # When enabled the bot's personality shifts with hub activity:
    # fewer users / less chat → lonely/anxious; packed hub → ecstatic.
    mood_enabled: bool = False

    # Sliding window (seconds) for measuring interaction rate.
    mood_window: int = 3600

    # Interaction rate thresholds (messages per hour) that determine
    # whether activity is considered low or high.
    mood_low_interaction: float = 2.0
    mood_high_interaction: float = 10.0

    # User-count ratio thresholds.  The engine compares the current
    # user count to the 24-hour rolling average.  Ratios below
    # ``mood_low_user_ratio`` count as "few users"; above
    # ``mood_high_user_ratio`` as "many users".
    mood_low_user_ratio: float = 0.5
    mood_high_user_ratio: float = 1.5

    # How far back (seconds) to keep user-count samples for the
    # rolling average.  Default 86 400 = 24 hours.
    mood_user_history: int = 86400

    # ── Web access ──────────────────────────────────────────────────
    # Give the bot web_search / fetch_webpage / read_rss tools.
    web_enabled: bool = False

    # RSS / Atom feed URLs the bot can proactively check.
    rss_feeds: list[str] = field(default_factory=list)

    # ── Persistent memory ───────────────────────────────────────────
    # Let the bot save and recall notes across sessions.  Notes are
    # stored in the shared application database (MySQL / PostgreSQL /
    # SQLite — whichever the hub is configured to use).
    memory_enabled: bool = False


@dataclass
class BotsConfig:
    """Configuration for hub bots."""
    security: BotConfig = field(default_factory=lambda: BotConfig(nick="Hub-Security", description="Hub security system"))
    op_chat: BotConfig = field(default_factory=lambda: BotConfig(nick="OpChat", description="Operator chat"))
    behavior: BotBehaviorConfig = field(default_factory=BotBehaviorConfig)


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
class GithubScript:
    """A script to fetch from a GitHub repository (used by Lua and Python)."""
    repo: str  # e.g. "Verlihub/ledokol"
    files: list[str] = field(default_factory=list)  # specific files, or empty for all


# Keep legacy alias
LuaGithubScript = GithubScript


@dataclass
class LuaConfig:
    """
    Lua plugin and script configuration.
    
    Controls loading of the Lua plugin (liblua_pi.so) and manages
    Lua scripts such as ledokol (https://github.com/Verlihub/ledokol).
    
    Scripts listed in ``github_scripts`` are fetched from GitHub on
    startup and placed in the hub's scripts directory. Scripts in
    ``autoload`` are loaded via ``!luaload`` after the hub starts.
    
    ``script_config`` is a dict mapping script names to their settings.
    For example, ledokol settings are applied via ``!ledoset key value``.
    """
    enabled: bool = True
    github_scripts: list[GithubScript] = field(default_factory=list)
    autoload: list[str] = field(default_factory=list)
    script_config: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class PythonConfig:
    """
    Python plugin and script configuration.
    
    Controls loading of the Python plugin (libpython_pi.so) and manages
    Python scripts loaded into the hub.
    
    Scripts listed in ``github_scripts`` are fetched from GitHub on
    startup. Scripts in ``autoload`` are loaded via ``+pyload``.
    
    ``script_config`` maps script names to their settings dict.
    """
    enabled: bool = True
    github_scripts: list[GithubScript] = field(default_factory=list)
    autoload: list[str] = field(default_factory=list)
    script_config: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class UserEntry:
    """A user account to pre-register."""
    nick: str
    password: str
    note: str = ""


@dataclass
class UsersConfig:
    """
    User accounts to pre-register at startup.
    
    Accounts are inserted into the database if they don't already exist.
    Existing accounts are NOT overwritten unless ``--force`` is passed.
    
    User classes: master (10), admin (5), operator (3), vip (2), registered (1).
    """
    masters: list[UserEntry] = field(default_factory=list)
    admins: list[UserEntry] = field(default_factory=list)
    operators: list[UserEntry] = field(default_factory=list)
    vips: list[UserEntry] = field(default_factory=list)
    registered: list[UserEntry] = field(default_factory=list)


@dataclass
class LlmEndpoint:
    """
    A single OpenAI-compatible LLM endpoint.

    When multiple endpoints are defined the user can pick one from the
    dashboard AI-chat dropdown.  The first endpoint in the list is the
    default.
    """
    name: str = ""          # Human-readable label shown in the UI
    base_url: str = ""      # OpenAI-compatible base URL
    model: str = ""         # Model identifier sent to the API
    api_key: str = "none"   # API key (many local servers ignore this)


@dataclass
class LlmConfig:
    """
    LLM (Large Language Model) integration configuration.
    
    Enables the AI chat assistant in the dashboard. Connects to any
    OpenAI-compatible API (Ollama, vLLM, llama.cpp, LiteLLM, OpenRouter, etc.).
    
    The LLM can query hub state and perform admin operations via tool calling.
    Access is gated by user class: ``min_class`` controls who can use the
    chat, ``admin_class`` controls who gets admin-level tools (kick, ban, etc.).

    Multiple endpoints can be configured under ``endpoints:``.  When the list
    is non-empty each entry's ``base_url`` / ``model`` / ``api_key`` override
    the top-level values, and the user picks one from the dashboard.  If
    ``endpoints`` is empty the top-level ``base_url`` / ``model`` / ``api_key``
    are used as a single unnamed endpoint.
    """
    enabled: bool = False
    base_url: str = "http://localhost:11434/v1"  # Ollama default
    model: str = "llama3.1"
    api_key: str = "ollama"
    max_tool_rounds: int = 5
    temperature: float = 0.3
    max_tokens: int = 2048
    min_class: int = 3   # Minimum user class to access AI chat (3=Operator)
    admin_class: int = 5  # Minimum class for admin tools (5=Admin)
    endpoints: list[LlmEndpoint] = field(default_factory=list)

    # -- helpers -------------------------------------------------------------

    def get_endpoint(self, name: str | None = None) -> LlmEndpoint:
        """Return the endpoint matching *name*, or the default.

        Falls back to the top-level ``base_url`` / ``model`` / ``api_key``
        when no endpoints are configured or when *name* doesn't match.
        """
        if self.endpoints:
            if name:
                for ep in self.endpoints:
                    if ep.name == name:
                        return ep
            return self.endpoints[0]
        return LlmEndpoint(
            name=self.model,
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
        )

    def list_endpoint_names(self) -> list[str]:
        """Return the ordered list of endpoint display names."""
        if self.endpoints:
            return [ep.name for ep in self.endpoints]
        return [self.model]


@dataclass
class McpConfig:
    """
    MCP (Model Context Protocol) endpoint configuration.

    When ``enabled``, the hub API mounts an MCP Streamable HTTP endpoint at
    ``/api/v1/mcp``.  The endpoint is protected by the same JWT auth used by
    the rest of the API — ``min_class`` controls who can connect,
    ``admin_class`` who gets write tools (kick, ban, broadcast, etc.).

    The standalone ``verlihub-mcp serve`` CLI is **not** affected by this
    config.  This section only governs the in-process MCP route inside the
    FastAPI application.
    """
    enabled: bool = False
    min_class: int = 3    # Minimum user class to access MCP (3=Operator)
    admin_class: int = 5  # Minimum class for admin/write tools (5=Admin)


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
    hublist: HubListConfig = field(default_factory=HubListConfig)
    bots: BotsConfig = field(default_factory=BotsConfig)
    users: UsersConfig = field(default_factory=UsersConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    lua: LuaConfig = field(default_factory=LuaConfig)
    python: PythonConfig = field(default_factory=PythonConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # Runtime mode
    mode: str = "both"  # "api", "hub", "both"
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
                token_expire_minutes=api.get("token_expire_minutes", config.api.token_expire_minutes),
                cors_origins=api.get("cors_origins", config.api.cors_origins),
                secure_cookies=api.get("secure_cookies", config.api.secure_cookies),
                registration_enabled=api.get("registration_enabled", config.api.registration_enabled),
                registration_require_invite=api.get("registration_require_invite", config.api.registration_require_invite),
                registration_default_class=api.get("registration_default_class", config.api.registration_default_class),
                registration_require_email=api.get("registration_require_email", config.api.registration_require_email),
                registration_check_email_deliverability=api.get("registration_check_email_deliverability", config.api.registration_check_email_deliverability),
                registration_block_disposable_emails=api.get("registration_block_disposable_emails", config.api.registration_block_disposable_emails),
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
                motd=hub.get("motd", config.hub.motd),
                motd_file=hub.get("motd_file", config.hub.motd_file),
                max_users=hub.get("max_users", config.hub.max_users),
                logo=hub.get("logo", config.hub.logo),
                hublist_servers=hub.get("hublist_servers", config.hub.hublist_servers),
            )
        
        # Hublist server
        if "hublist" in data:
            hl = data["hublist"]
            config.hublist = HubListConfig(
                server_enabled=hl.get("server_enabled", config.hublist.server_enabled),
                registration_interval=hl.get("registration_interval", config.hublist.registration_interval),
                stale_timeout=hl.get("stale_timeout", config.hublist.stale_timeout),
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
            if "behavior" in bots:
                beh = bots["behavior"]
                config.bots.behavior = BotBehaviorConfig(
                    endpoint=beh.get("endpoint", config.bots.behavior.endpoint),
                    personality=beh.get("personality", config.bots.behavior.personality),
                    chat_mode=beh.get("chat_mode", config.bots.behavior.chat_mode),
                    triggers=beh.get("triggers", config.bots.behavior.triggers),
                    proactive_interval=int(beh.get("proactive_interval", config.bots.behavior.proactive_interval)),
                    proactive_prompts=beh.get("proactive_prompts", config.bots.behavior.proactive_prompts),
                    thinking_interval=int(beh.get("thinking_interval", config.bots.behavior.thinking_interval)),
                    max_chat_length=int(beh.get("max_chat_length", config.bots.behavior.max_chat_length)),
                    mood_enabled=beh.get("mood_enabled", config.bots.behavior.mood_enabled),
                    mood_window=int(beh.get("mood_window", config.bots.behavior.mood_window)),
                    mood_low_interaction=float(beh.get("mood_low_interaction", config.bots.behavior.mood_low_interaction)),
                    mood_high_interaction=float(beh.get("mood_high_interaction", config.bots.behavior.mood_high_interaction)),
                    mood_low_user_ratio=float(beh.get("mood_low_user_ratio", config.bots.behavior.mood_low_user_ratio)),
                    mood_high_user_ratio=float(beh.get("mood_high_user_ratio", config.bots.behavior.mood_high_user_ratio)),
                    mood_user_history=int(beh.get("mood_user_history", config.bots.behavior.mood_user_history)),
                    web_enabled=beh.get("web_enabled", config.bots.behavior.web_enabled),
                    rss_feeds=beh.get("rss_feeds", config.bots.behavior.rss_feeds),
                    memory_enabled=beh.get("memory_enabled", config.bots.behavior.memory_enabled),
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
                GithubScript(
                    repo=gs.get("repo", ""),
                    files=gs.get("files", []),
                )
                for gs in lua.get("github_scripts", [])
            ]
            # Support legacy "ledokol_config" key as well as new "script_config"
            script_config = lua.get("script_config", {})
            if not script_config and "ledokol_config" in lua:
                ledokol_cfg = lua["ledokol_config"]
                if ledokol_cfg:
                    script_config = {"ledokol": ledokol_cfg}
            config.lua = LuaConfig(
                enabled=lua.get("enabled", True),
                github_scripts=github_scripts,
                autoload=lua.get("autoload", []),
                script_config=script_config,
            )
        
        # Python plugin & scripts
        if "python" in data:
            py = data["python"]
            github_scripts = [
                GithubScript(
                    repo=gs.get("repo", ""),
                    files=gs.get("files", []),
                )
                for gs in py.get("github_scripts", [])
            ]
            config.python = PythonConfig(
                enabled=py.get("enabled", True),
                github_scripts=github_scripts,
                autoload=py.get("autoload", []),
                script_config=py.get("script_config", {}),
            )
        
        # Users
        if "users" in data:
            users = data["users"]
            def _parse_user_list(entries: list) -> list[UserEntry]:
                return [
                    UserEntry(
                        nick=u.get("nick", ""),
                        password=u.get("password", ""),
                        note=u.get("note", ""),
                    )
                    for u in (entries or [])
                ]
            config.users = UsersConfig(
                masters=_parse_user_list(users.get("masters", [])),
                admins=_parse_user_list(users.get("admins", [])),
                operators=_parse_user_list(users.get("operators", [])),
                vips=_parse_user_list(users.get("vips", [])),
                registered=_parse_user_list(users.get("registered", [])),
            )
        
        # LLM integration
        if "llm" in data:
            llm_data = data["llm"]
            # Parse endpoint list
            endpoints: list[LlmEndpoint] = []
            for ep in llm_data.get("endpoints", []):
                endpoints.append(LlmEndpoint(
                    name=ep.get("name", ""),
                    base_url=ep.get("base_url", ""),
                    model=ep.get("model", ""),
                    api_key=ep.get("api_key", "none"),
                ))
            config.llm = LlmConfig(
                enabled=llm_data.get("enabled", config.llm.enabled),
                base_url=llm_data.get("base_url", config.llm.base_url),
                model=llm_data.get("model", config.llm.model),
                api_key=llm_data.get("api_key", config.llm.api_key),
                max_tool_rounds=llm_data.get("max_tool_rounds", config.llm.max_tool_rounds),
                temperature=llm_data.get("temperature", config.llm.temperature),
                max_tokens=llm_data.get("max_tokens", config.llm.max_tokens),
                min_class=llm_data.get("min_class", config.llm.min_class),
                admin_class=llm_data.get("admin_class", config.llm.admin_class),
                endpoints=endpoints,
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
            token_expire_minutes=int(os.getenv("VH_JWT_EXPIRE_MINUTES", str(config.api.token_expire_minutes))),
            cors_origins=cors_origins.split(",") if cors_origins else ["*"],
            secure_cookies=os.getenv("VH_SECURE_COOKIES", "0") == "1",
            registration_enabled=os.getenv("VH_REGISTRATION_ENABLED", "1" if config.api.registration_enabled else "0").lower() in ("1", "true", "yes"),
            registration_require_invite=os.getenv("VH_REGISTRATION_REQUIRE_INVITE", "1" if config.api.registration_require_invite else "0").lower() in ("1", "true", "yes"),
            registration_default_class=int(os.getenv("VH_REGISTRATION_DEFAULT_CLASS", str(config.api.registration_default_class))),
        )
        
        # Hub from environment
        config.hub = HubConfig(
            name=os.getenv("VH_HUB_NAME", config.hub.name),
            description=os.getenv("VH_HUB_DESCRIPTION", config.hub.description),
            host=os.getenv("VH_HUB_HOST", config.hub.host),
            port=int(os.getenv("VH_HUB_PORT", str(config.hub.port))),
            listen_host=os.getenv("VH_HUB_LISTEN", config.hub.listen_host),
            topic=os.getenv("VH_HUB_TOPIC", config.hub.topic),
            logo=os.getenv("VH_HUB_LOGO", config.hub.logo),
            max_users=int(os.getenv("VH_HUB_MAX_USERS", str(config.hub.max_users))),
        )
        
        # Runtime
        config.mode = os.getenv("VH_MODE", config.mode)
        config.environment = os.getenv("VH_ENV", config.environment)
        
        # LLM from environment
        if os.getenv("VH_LLM_ENABLED"):
            config.llm = LlmConfig(
                enabled=os.getenv("VH_LLM_ENABLED", "").lower() in ("1", "true", "yes"),
                base_url=os.getenv("VH_LLM_BASE_URL", config.llm.base_url),
                model=os.getenv("VH_LLM_MODEL", config.llm.model),
                api_key=os.getenv("VH_LLM_API_KEY", config.llm.api_key),
                max_tool_rounds=int(os.getenv("VH_LLM_MAX_TOOL_ROUNDS", str(config.llm.max_tool_rounds))),
                temperature=float(os.getenv("VH_LLM_TEMPERATURE", str(config.llm.temperature))),
                max_tokens=int(os.getenv("VH_LLM_MAX_TOKENS", str(config.llm.max_tokens))),
                min_class=int(os.getenv("VH_LLM_MIN_CLASS", str(config.llm.min_class))),
                admin_class=int(os.getenv("VH_LLM_ADMIN_CLASS", str(config.llm.admin_class))),
            )
        
        return config
    
    def apply_to_env(self) -> None:
        """
        Export configuration to environment variables.
        
        This is **not** used for in-process configuration — modules should
        read from the config singleton via ``get_config()`` instead.
        
        The env vars are still exported for:
        - Subprocess / Docker child processes that may read ``VH_*`` vars
        - Legacy ``models.database.DatabaseConfig`` fallback paths
        - External CLI tools (e.g. ``verlihub-cli``)
        """
        env_vars = {}
        env_vars.update(self.database.to_env())
        env_vars.update(self.api.to_env())

        # Config directory — used by the API lifespan for SQLite path resolution
        env_vars["VH_CONFIG_DIR"] = str(self._config_dir or "")
        
        # Hub settings (needed by dashboard in API-only mode and C++ core)
        env_vars["VH_HUB_NAME"] = self.hub.name
        env_vars["VH_HUB_DESCRIPTION"] = self.hub.description
        env_vars["VH_HUB_TOPIC"] = self.hub.topic
        env_vars["VH_HUB_LOGO"] = self.hub.logo
        env_vars["VH_HUB_PORT"] = str(self.hub.port)
        env_vars["VH_HUB_HOST"] = self.hub.host
        env_vars["VH_HUB_OWNER"] = self.hub.owner
        env_vars["VH_HUB_ENCODING"] = self.hub.encoding
        env_vars["VH_HUB_LISTEN"] = self.hub.listen_host
        env_vars["VH_HUB_MAX_USERS"] = str(self.hub.max_users)

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
                "token_expire_minutes": self.api.token_expire_minutes,
                "cors_origins": self.api.cors_origins,
                "secure_cookies": self.api.secure_cookies,
                "registration_enabled": self.api.registration_enabled,
                "registration_require_invite": self.api.registration_require_invite,
                "registration_default_class": self.api.registration_default_class,
            },
            "hub": {
                "name": self.hub.name,
                "description": self.hub.description,
                "host": self.hub.host,
                "port": self.hub.port,
                "listen_host": self.hub.listen_host,
                "topic": self.hub.topic,
                "logo": self.hub.logo,
                "max_users": self.hub.max_users,
                "hublist_servers": self.hub.hublist_servers,
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
                "script_config": self.lua.script_config,
            },
            "python": {
                "enabled": self.python.enabled,
                "github_scripts": [
                    {"repo": gs.repo, "files": gs.files}
                    for gs in self.python.github_scripts
                ],
                "autoload": self.python.autoload,
                "script_config": self.python.script_config,
            },
        }


# =============================================================================
# Config Singleton — single source of truth for in-process access
# =============================================================================

_config: Optional[VerlihubConfig] = None


def set_config(config: VerlihubConfig) -> None:
    """
    Set the global configuration singleton.

    Call this once during startup (after loading YAML / CLI overrides)
    so that every module can access config via ``get_config()`` without
    an env-var round-trip.
    """
    global _config
    _config = config


def get_config() -> VerlihubConfig:
    """
    Return the global ``VerlihubConfig`` instance.

    Raises ``RuntimeError`` if ``set_config()`` has not been called yet.
    """
    if _config is None:
        raise RuntimeError(
            "get_config() called before set_config(). "
            "Ensure the config is loaded during startup."
        )
    return _config


def get_config_optional() -> Optional[VerlihubConfig]:
    """Return the global config or ``None`` if not yet initialised."""
    return _config


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


# =============================================================================
# Database-Config Synchronization
# =============================================================================

# Mapping of YAML config paths to SetupList (file, var) pairs.
# Only settings that map to the C++ hub's SetupList are included.
_HUB_SETTINGS_MAP: dict[str, tuple[str, str]] = {
    "hub.name": ("config", "hub_name"),
    "hub.description": ("config", "hub_desc"),
    "hub.host": ("config", "hub_host"),
    "hub.port": ("config", "hub_port"),
    "hub.listen_host": ("config", "listen_ip"),
    "hub.owner": ("config", "hub_owner"),
    "hub.topic": ("config", "hub_topic"),
    "hub.category": ("config", "hub_category"),
    "hub.encoding": ("config", "hub_encoding"),
    "hub.max_users": ("config", "max_users"),
    "hub.motd": ("config", "hub_motd"),
    "bots.security.nick": ("config", "hub_security"),
    "bots.op_chat.nick": ("config", "opchat_name"),
    "bots.behavior.endpoint": ("config", "bot_endpoint"),
    "bots.behavior.personality": ("config", "bot_personality"),
    "bots.behavior.chat_mode": ("config", "bot_chat_mode"),
    "bots.behavior.thinking_interval": ("config", "bot_thinking_interval"),
    "bots.behavior.max_chat_length": ("config", "bot_max_chat_length"),
    "bots.behavior.mood_enabled": ("config", "bot_mood_enabled"),
    "bots.behavior.web_enabled": ("config", "bot_web_enabled"),
    "bots.behavior.memory_enabled": ("config", "bot_memory_enabled"),
    "hub.send_user_info": ("config", "send_user_info"),
    "hub.user_info_as_pm": ("config", "user_info_as_pm"),
}

# User class mapping
_USER_CLASS_MAP: dict[str, int] = {
    "masters": 10,
    "admins": 5,
    "operators": 3,
    "vips": 2,
    "registered": 1,
}


def _get_nested(obj: Any, dotted_path: str) -> Any:
    """Get a value from a nested object using dotted path notation."""
    current = obj
    for part in dotted_path.split("."):
        if hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
    return current


async def apply_config_to_db(config: VerlihubConfig, force: bool = False) -> None:
    """
    Synchronize YAML configuration with the database.
    
    Behavior:
    - Hub settings (SetupList): If the database already has a value for a
      setting, the database value is preferred (YAML is ignored) unless
      ``force=True``.
    - User accounts: Users listed in ``config.users`` are registered in the
      database if they don't already exist. If ``force=True``, existing
      users have their passwords and classes updated.
    
    This function should be called after the database is initialized but
    before the hub starts.
    
    Args:
        config: The loaded VerlihubConfig
        force: If True, overwrite database values with YAML values
    """
    try:
        from sqlalchemy import select, text
        from verlihub.models.database import get_database, get_async_session
        from verlihub.models import SetupList, RegUser, UserClass
    except ImportError as e:
        logger.warning("Cannot apply config to DB (missing dependencies): %s", e)
        return
    
    try:
        db = get_database()
    except RuntimeError:
        logger.debug("Database not initialized, skipping config-to-DB sync")
        return
    
    async with get_async_session() as session:
        # --- Hub settings synchronization ---
        applied = 0
        skipped = 0
        
        for config_path, (file_key, var_key) in _HUB_SETTINGS_MAP.items():
            value = _get_nested(config, config_path)
            if value is None:
                continue
            
            str_value = str(value)
            
            # Check if DB already has this setting
            result = await session.execute(
                select(SetupList).where(
                    SetupList.file == file_key,
                    SetupList.var == var_key,
                )
            )
            existing = result.scalars().first()
            
            if existing is not None:
                if force:
                    existing.val = str_value
                    session.add(existing)
                    applied += 1
                    logger.debug("Forced %s.%s = %s", file_key, var_key, str_value)
                else:
                    skipped += 1
                    logger.debug(
                        "Kept DB value for %s.%s = %s (YAML had %s)",
                        file_key, var_key, existing.val, str_value,
                    )
            else:
                entry = SetupList(file=file_key, var=var_key, val=str_value)
                session.add(entry)
                applied += 1
                logger.debug("Set %s.%s = %s (new)", file_key, var_key, str_value)
        
        if applied or skipped:
            logger.info(
                "Hub settings: %d applied, %d kept from DB%s",
                applied, skipped, " (--force)" if force else "",
            )
        
        # --- User registration ---
        users_created = 0
        users_updated = 0
        users_skipped = 0
        
        for class_name, user_class in _USER_CLASS_MAP.items():
            user_list = getattr(config.users, class_name, [])
            for user_entry in user_list:
                if not user_entry.nick:
                    continue
                
                # Check if user exists
                result = await session.execute(
                    select(RegUser).where(RegUser.nick == user_entry.nick)
                )
                existing_user = result.scalars().first()
                
                if existing_user is not None:
                    if force:
                        # Update password and class
                        if user_entry.password:
                            try:
                                import bcrypt
                                hashed = bcrypt.hashpw(
                                    user_entry.password.encode("utf-8"),
                                    bcrypt.gensalt(),
                                ).decode("utf-8")
                                existing_user.login_pwd = hashed
                            except ImportError:
                                existing_user.login_pwd = user_entry.password
                        existing_user.user_class = user_class
                        if user_entry.note:
                            existing_user.note_op = user_entry.note
                        session.add(existing_user)
                        users_updated += 1
                        logger.debug("Updated user %s (class %d)", user_entry.nick, user_class)
                    else:
                        users_skipped += 1
                        logger.debug("User %s already exists, skipping", user_entry.nick)
                else:
                    # Create new user
                    password = user_entry.password
                    if password:
                        try:
                            import bcrypt
                            password = bcrypt.hashpw(
                                password.encode("utf-8"),
                                bcrypt.gensalt(),
                            ).decode("utf-8")
                        except ImportError:
                            pass  # Store plaintext if bcrypt not available
                    
                    new_user = RegUser(
                        nick=user_entry.nick,
                        login_pwd=password,
                        user_class=user_class,
                        reg_op="config",
                        note_op=user_entry.note or "",
                    )
                    session.add(new_user)
                    users_created += 1
                    logger.info("Registered user %s (class %d)", user_entry.nick, user_class)
        
        if users_created or users_updated or users_skipped:
            logger.info(
                "Users: %d created, %d updated, %d skipped%s",
                users_created, users_updated, users_skipped,
                " (--force)" if force else "",
            )
        
        await session.commit()
