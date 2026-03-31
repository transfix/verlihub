"""
Core bridge module for Verlihub C++ core.

This module provides a Pythonic interface to the C++ HubContext via SWIG bindings.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import bcrypt
from sqlmodel import select

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# Import SWIG module (built by CMake)
# Build outputs should be symlinked for development:
#   ln -sf ../../build/python/verlihub/verlihub_core.py python/verlihub/
#   ln -sf ../../build/python/verlihub/_verlihub_core.so python/verlihub/
try:
    from verlihub import verlihub_core
except ImportError as e:
    raise ImportError(
        "verlihub_core module not found. "
        "Build with CMake and symlink outputs: "
        "ln -sf ../../build/python/verlihub/verlihub_core.py python/verlihub/"
    ) from e

if verlihub_core is None:
    raise ImportError(
        "verlihub_core SWIG module not available. "
        "Build with CMake first or run in standalone API mode."
    )

logger = logging.getLogger(__name__)


class HubEventHandler(verlihub_core.IHubEventCallback):
    """
    Python callback handler for hub events.
    
    Extends the SWIG-generated IHubEventCallback to dispatch events
    to registered Python handlers.  Also implements ``OnGetConfig``
    so the C++ core can pull configuration values straight from
    the Python YAML config instead of relying on environment variables.
    
    Example:
        handler = HubEventHandler()
        handler.on_user_connect = lambda nick, ip: print(f"{nick} connected")
        ctx.SetEventCallback(handler)
    """
    
    def __init__(self) -> None:
        super().__init__()
        self._handlers: dict[str, list[Callable[..., Any]]] = {
            'user_connect': [],
            'user_disconnect': [],
            'user_login': [],
            'user_logout': [],
            'chat_message': [],
            'private_message': [],
            'search': [],
            'timer': [],
            'hub_started': [],
            'hub_stopping': [],
            'ext_json': [],
            'my_hub_url': [],
            'user_in_update': [],
        }
        self._lock = threading.Lock()
        # Config dict for OnGetConfig callback — populated before Initialize().
        # Structure: {"hub": {"hub_name": "...", "hub_topic": "..."}, ...}
        self._config: dict[str, dict[str, str]] = {}
        # Back-reference to HubContext (set by HubContext.__init__)
        # Used by OnValidateNick/OnCheckPassword to read config values.
        self._hub_context_ref: Optional["HubContext"] = None
        # Explicit event loop reference for cross-thread DB access.
        # Set by set_event_loop() after the async engine is created.
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
    
    # ------------------------------------------------------------------
    # Config bridge
    # ------------------------------------------------------------------

    def set_config(self, config: dict[str, dict[str, str]]) -> None:
        """Load a ``{section: {key: value, ...}}`` dict for C++ to query."""
        with self._lock:
            self._config = config

    def OnGetConfig(self, section: str, key: str, default_val: str) -> str:
        """Called from C++ ``LoadConfiguration()`` — returns config values."""
        with self._lock:
            sec = self._config.get(section)
            if sec is not None:
                val = sec.get(key)
                if val is not None:
                    return str(val)
        return default_val
    
    def register(self, event: str, handler: Callable[..., Any]) -> None:
        """Register a handler for an event type."""
        with self._lock:
            if event in self._handlers:
                self._handlers[event].append(handler)
            else:
                raise ValueError(f"Unknown event type: {event}")
    
    def unregister(self, event: str, handler: Callable[..., Any]) -> None:
        """Unregister a handler."""
        with self._lock:
            if event in self._handlers:
                try:
                    self._handlers[event].remove(handler)
                except ValueError:
                    pass
    
    def _dispatch(self, event: str, *args: Any, **kwargs: Any) -> bool:
        """Dispatch event to registered handlers."""
        with self._lock:
            handlers = self._handlers.get(event, [])[:]
        
        result = True
        for handler in handlers:
            try:
                ret = handler(*args, **kwargs)
                # For pre-action callbacks, False blocks the action
                if ret is False:
                    result = False
            except Exception:
                logger.exception("Error in event handler for %s", event)
        
        return result
    
    # C++ callback overrides (called from C++)
    
    def OnUserConnect(self, nick: str, ip: str) -> bool:
        return self._dispatch('user_connect', nick, ip)
    
    def OnUserDisconnect(self, nick: str) -> None:
        self._dispatch('user_disconnect', nick)
    
    def OnUserLogin(self, nick: str, user_class: int) -> bool:
        return self._dispatch('user_login', nick, user_class)
    
    def OnUserLogout(self, nick: str) -> None:
        self._dispatch('user_logout', nick)
    
    def OnChatMessage(self, nick: str, message: str) -> bool:
        return self._dispatch('chat_message', nick, message)
    
    def OnPrivateMessage(self, from_nick: str, to_nick: str, message: str) -> bool:
        return self._dispatch('private_message', from_nick, to_nick, message)
    
    def OnSearch(self, nick: str, query: str) -> bool:
        return self._dispatch('search', nick, query)
    
    def OnTimer(self, timestamp: int) -> None:
        self._dispatch('timer', timestamp)
    
    def OnHubStarted(self) -> None:
        self._dispatch('hub_started')
    
    def OnHubStopping(self) -> None:
        self._dispatch('hub_stopping')

    def OnExtJSON(self, nick: str, json: str) -> bool:
        return self._dispatch('ext_json', nick, json)

    def OnMyHubURL(self, nick: str, url: str) -> bool:
        return self._dispatch('my_hub_url', nick, url)

    def OnUserINUpdate(self, nick: str, data: str) -> bool:
        return self._dispatch('user_in_update', nick, data)

    # ------------------------------------------------------------------
    # C++ log callback (called from C++ while m_log_mutex is held)
    # ------------------------------------------------------------------

    def OnLog(self, level: int, message: str) -> None:
        """Receive a formatted log line from the C++ core.

        Called from a C++ thread while ``m_log_mutex`` is held.
        We store it in the ring buffer and push it to WebSocket clients.
        Must NOT call back into C++ logging to avoid deadlock.
        """
        try:
            from verlihub.log_buffer import _level_str
            from verlihub.dashboard.websocket import emit_log
            emit_log(
                level=_level_str(level),
                message=message,
                log_type="core",
            )
        except Exception:
            # Swallow — we MUST NOT raise here or the C++ caller will abort.
            pass

    # ------------------------------------------------------------------
    # NMDC Authentication callbacks (called from C++ I/O thread)
    # ------------------------------------------------------------------
    # These must be synchronous (C++ blocks until they return).
    # We bridge to the async DB via run_coroutine_threadsafe().
    # ------------------------------------------------------------------

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the running event loop for cross-thread DB calls.

        Must be called from the async context that owns the DB engine
        (e.g. during API lifespan startup) so that ``_sync_db_lookup``
        can safely schedule coroutines on the correct loop.
        """
        self._event_loop = loop

    def _get_event_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Return the stored event loop if still running, else probe."""
        # Prefer the explicitly stored loop (set by API lifespan)
        if self._event_loop is not None and self._event_loop.is_running():
            return self._event_loop
        # Fallback: try the current thread's loop (works when called from
        # within the same async context, e.g. hub-only mode)
        try:
            loop = asyncio.get_running_loop()
            return loop
        except RuntimeError:
            pass
        return None

    def _sync_db_lookup(self, nick: str):
        """
        Synchronously look up a registered user by nick.
        Returns (user_class, hashed_password, authorised) or None.
        Called from the C++ I/O thread.
        """
        try:
            from verlihub.models import RegUser
            from verlihub.models.database import get_database

            db = get_database()

            async def _query():
                async with db._session_factory() as session:
                    result = await session.execute(
                        select(RegUser).where(RegUser.nick == nick)
                    )
                    user = result.scalar_one_or_none()
                    if user is None:
                        return None
                    return (user.user_class, user.login_pwd, user.authorised)

            loop = self._get_event_loop()
            if loop is not None:
                future = asyncio.run_coroutine_threadsafe(_query(), loop)
                return future.result(timeout=5)
            else:
                logger.warning(
                    "No running event loop for DB lookup (nick=%s) — "
                    "call set_event_loop() during startup",
                    nick,
                )
                return None
        except Exception:
            logger.exception("DB lookup failed for nick=%s", nick)
            return None

    def _db_available(self) -> bool:
        """Check if the database and event loop are ready for auth queries."""
        try:
            from verlihub.models.database import get_database
            get_database()
        except (RuntimeError, ImportError):
            return False
        return self._get_event_loop() is not None

    def _get_config_value(self, key: str, default: str = "") -> str:
        """Read a config value from the HubContext (thread-safe C++ call)."""
        try:
            if self._hub_context_ref is not None:
                return self._hub_context_ref.get_config("config", key, default)
        except Exception:
            pass
        return default

    def OnValidateNick(self, nick: str, ip: str) -> int:
        """
        Called by C++ when a client sends $ValidateNick.
        
        Returns:
            -1  → reject (nick denied)
             0  → allow as guest (no password required)
            >0  → registered user class (password required via OnCheckPassword)
        """
        try:
            db_result = self._sync_db_lookup(nick)

            if db_result is not None:
                user_class, _pwd, authorised = db_result
                if not authorised:
                    logger.info("Rejecting disabled user: %s", nick)
                    return -1
                # Registered user → require password
                return max(user_class, 1)

            if db_result is None and not self._db_available():
                # DB lookup failed (no event loop / DB not ready).
                # Reject the connection rather than allowing a
                # potentially-registered user in as a guest.
                logger.warning(
                    "Rejecting nick %s — database unavailable for auth", nick
                )
                return -1

            # Nick not in DB → check allow_unregistered
            allow_unreg = self._get_config_value("allow_unregistered", "1")
            if allow_unreg != "1":
                logger.info("Rejecting unregistered nick: %s (allow_unregistered=0)", nick)
                return -1

            # Allow as guest
            return 0
        except Exception:
            logger.exception("OnValidateNick error for nick=%s", nick)
            return -1

    def OnCheckPassword(self, nick: str, password: str) -> int:
        """
        Called by C++ when a client sends $MyPass (registered users only).
        
        Returns:
            -1  → wrong password
            >=0 → user class on success
        """
        try:
            db_result = self._sync_db_lookup(nick)
            if db_result is None:
                return -1

            user_class, hashed_pwd, authorised = db_result
            if not authorised:
                return -1

            # Verify password using bcrypt
            if not hashed_pwd:
                # No password set — accept if require_password is off
                require_pw = self._get_config_value("require_password", "1")
                if require_pw != "1":
                    return user_class
                return -1

            try:
                if bcrypt.checkpw(
                    password.encode("utf-8"),
                    hashed_pwd.encode("utf-8") if isinstance(hashed_pwd, str) else hashed_pwd,
                ):
                    return user_class
            except (ValueError, TypeError):
                logger.warning("Password hash format error for nick=%s", nick)

            return -1
        except Exception:
            logger.exception("OnCheckPassword error for nick=%s", nick)
            return -1


class HubContext:
    """
    Python wrapper for C++ HubContext.
    
    Provides a Pythonic interface with async support and context manager.
    
    Example:
        async with HubContext.create("/etc/verlihub") as ctx:
            await ctx.start(port=411)
            # Hub runs...
    """
    
    def __init__(self, cpp_context: verlihub_core.HubContext) -> None:
        self._cpp = cpp_context
        self._event_handler = HubEventHandler()
        self._event_handler._hub_context_ref = self
        self._cpp.SetEventCallback(self._event_handler)
        self._shutdown_event = asyncio.Event()
        self._start_time: float | None = None
    
    @classmethod
    def create(cls, config_dir: str | Path) -> Optional["HubContext"]:
        """
        Create a new HubContext.
        
        Args:
            config_dir: Path to verlihub configuration directory
            
        Returns:
            HubContext instance or None on failure
        """
        config_path = str(Path(config_dir).resolve())
        cpp_ctx = verlihub_core.HubContext.Create(config_path)
        if cpp_ctx is None:
            return None
        return cls(cpp_ctx)
    
    @property
    def cpp(self) -> verlihub_core.HubContext:
        """Access the underlying C++ context."""
        return self._cpp
    
    @property
    def events(self) -> HubEventHandler:
        """Access the event handler for registering callbacks."""
        return self._event_handler
    
    @property
    def is_running(self) -> bool:
        """Check if hub is running."""
        return self._cpp.IsRunning()
    
    @property
    def user_count(self) -> int:
        """Get current online user count."""
        return self._cpp.GetUserCount()
    
    @property
    def total_share(self) -> int:
        """Get total share size in bytes."""
        return self._cpp.GetTotalShare()
    
    @property
    def uptime(self) -> int:
        """Get hub uptime in seconds (0 if not running)."""
        if self._start_time is None or not self.is_running:
            return 0
        return int(time.monotonic() - self._start_time)

    @property
    def port(self) -> int:
        """Get the hub listen port from config."""
        try:
            cfg = self._cpp.GetHubConfig()
            return cfg.listen_port
        except Exception:
            return 411

    @property
    def hub_name(self) -> str:
        """Get hub name."""
        return self._cpp.GetHubName()
    
    @property
    def hub_topic(self) -> str:
        """Get hub topic."""
        return self._cpp.GetHubTopic()
    
    @hub_topic.setter
    def hub_topic(self, value: str) -> None:
        """Set hub topic."""
        self._cpp.SetHubTopic(value)
    
    def initialize(self) -> bool:
        """
        Initialize the hub (load config, connect to database).
        
        Must be called before start().
        """
        return self._cpp.Initialize()
    
    def start(self, port: int = 0, listen_ip: str = "") -> bool:
        """
        Start the hub server.
        
        Args:
            port: Port to listen on (0 = use config)
            listen_ip: IP to bind to (empty = use config)
        """
        result = self._cpp.Start(port, listen_ip)
        if result:
            self._start_time = time.monotonic()
        return result
    
    def stop(self) -> None:
        """Stop the hub server."""
        self._cpp.Stop()
        self._shutdown_event.set()
    
    def request_shutdown(self, signal_code: int = 0) -> None:
        """Request hub shutdown (can be called from signal handler)."""
        self._cpp.RequestShutdown(signal_code)
        self._shutdown_event.set()
    
    async def wait_for_shutdown(self) -> None:
        """Wait asynchronously for hub shutdown."""
        await self._shutdown_event.wait()
    
    # User operations
    
    def get_user_nicks(self) -> list[str]:
        """Get list of all online user nicknames."""
        return list(self._cpp.GetUserNicks())

    def get_user_list(self) -> list[dict]:
        """
        Get full info for all online users.
        
        Uses GetUserInfoSnapshots() for a single-lock, race-free snapshot
        of all user data from the NMDCHubServer.
        
        Returns a list of dicts with keys:
        - nick, user_class, share, ip, country, client, status
        - description, tag, speed, email
        """
        try:
            # Prefer the efficient single-lock C++ method
            snapshots = self._cpp.GetUserInfoSnapshots()
        except AttributeError:
            # Fallback: SWIG module too old or method unavailable
            logger.warning("GetUserInfoSnapshots not available, falling back to nick list")
            return [{"nick": n, "user_class": 0, "share": 0, "ip": "",
                      "country": "", "client": "", "status": ""}
                     for n in self.get_user_nicks()]

        user_list = []
        for snap in snapshots:
            # SWIG maps C++ char → Python str; normalise mode to a
            # single-character string (or empty when unset).
            raw_mode = getattr(snap, 'mode', '')
            if isinstance(raw_mode, int):
                mode = chr(raw_mode) if raw_mode else ''
            else:
                mode = raw_mode if raw_mode and raw_mode != '\x00' else ''

            user_list.append({
                "nick": snap.nick,
                "user_class": snap.user_class,
                "share": snap.share,
                "ip": snap.ip,
                "country": snap.country,
                "country_name": getattr(snap, 'country_name', ''),
                "city": getattr(snap, 'city', ''),
                "client": snap.client_name,
                "client_version": getattr(snap, 'client_version', ''),
                "description": snap.description,
                "tag": snap.tag,
                "speed": snap.speed,
                "email": snap.email,
                "mode": mode,
                "slots": getattr(snap, 'slots', 0),
                "hubs_normal": getattr(snap, 'hubs_normal', 0),
                "hubs_registered": getattr(snap, 'hubs_registered', 0),
                "hubs_operator": getattr(snap, 'hubs_operator', 0),
                "status_flag": getattr(snap, 'status_flag', 0),
                "supports": getattr(snap, 'supports', ''),
                "login_time": getattr(snap, 'login_time', 0),
                "status": "",
            })
        return user_list

    def get_user_info(self, nick: str) -> dict | None:
        """
        Get info for a single online user.
        
        Returns a dict with user info, or None if not found.
        """
        try:
            from verlihub import verlihub_core
            snap = verlihub_core.UserInfoSnapshot()
            if self._cpp.GetUserInfo(nick, snap):
                raw_mode = getattr(snap, 'mode', '')
                if isinstance(raw_mode, int):
                    mode = chr(raw_mode) if raw_mode else ''
                else:
                    mode = raw_mode if raw_mode and raw_mode != '\x00' else ''

                return {
                    "nick": snap.nick,
                    "user_class": snap.user_class,
                    "share": snap.share,
                    "ip": snap.ip,
                    "country": snap.country,
                    "country_name": getattr(snap, 'country_name', ''),
                    "city": getattr(snap, 'city', ''),
                    "client": snap.client_name,
                    "client_version": getattr(snap, 'client_version', ''),
                    "description": snap.description,
                    "tag": snap.tag,
                    "speed": snap.speed,
                    "email": snap.email,
                    "mode": mode,
                    "slots": getattr(snap, 'slots', 0),
                    "hubs_normal": getattr(snap, 'hubs_normal', 0),
                    "hubs_registered": getattr(snap, 'hubs_registered', 0),
                    "hubs_operator": getattr(snap, 'hubs_operator', 0),
                    "status_flag": getattr(snap, 'status_flag', 0),
                    "supports": getattr(snap, 'supports', ''),
                    "login_time": getattr(snap, 'login_time', 0),
                    "status": "",
                }
        except (AttributeError, ImportError):
            pass
        return None
    
    def find_user(self, nick: str) -> bool:
        """Check if a user is online."""
        return nick in self.get_user_nicks()
    
    def send_to_user(self, nick: str, message: str) -> bool:
        """Send a message to a specific user."""
        return self._cpp.SendToUser(nick, message)

    def send_pm_as(self, from_nick: str, to_nick: str, message: str) -> bool:
        """
        Send a private message from *from_nick* to *to_nick*.

        Constructs the raw NMDC PM protocol frame and delivers it via
        ``SendToUser`` (→ ``SendToNick`` → ``SendToConn``).  The C++ layer
        appends the trailing ``|`` terminator automatically.
        """
        raw = f"$To: {to_nick} From: {from_nick} $<{from_nick}> {message}"
        return self._cpp.SendToUser(to_nick, raw)

    def send_to_all(self, message: str) -> bool:
        """Broadcast message to all users."""
        return self._cpp.SendToAll(message)
    
    def send_to_class(self, message: str, min_class: int, max_class: int) -> bool:
        """Send message to users in class range."""
        return self._cpp.SendToClass(message, min_class, max_class)

    def send_chat_as(self, nick: str, message: str) -> bool:
        """Send a chat message to all users, formatted as <nick> message."""
        return self._cpp.SendToOpChat(message, nick)

    def kick_user(self, op_nick: str, nick: str, reason: str) -> bool:
        """Kick a user from the hub."""
        return self._cpp.KickUser(op_nick, nick, reason)

    def force_move(self, nick: str, address: str) -> bool:
        """Force-move (redirect) a user to another hub address."""
        return self._cpp.ForceMove(nick, address)

    def disconnect_user(self, nick: str) -> bool:
        """Disconnect a user without redirect."""
        return self._cpp.DisconnectUser(nick)

    def send_to_opchat(self, message: str, from_nick: str = "") -> bool:
        """Send a message to OpChat."""
        return self._cpp.SendToOpChat(message, from_nick)

    def get_protocol_stats(self) -> dict:
        """Get protocol-level message counters."""
        snap = self._cpp.GetProtocolStats()
        return {
            "messages_in": snap.messages_in,
            "messages_out": snap.messages_out,
            "chat_count": snap.chat_count,
            "pm_count": snap.pm_count,
            "search_count": snap.search_count,
            "myinfo_count": snap.myinfo_count,
            "ctm_count": snap.ctm_count,
            "sr_count": snap.sr_count,
            "mcto_count": snap.mcto_count,
            "flood_blocked": snap.flood_blocked,
            "ban_blocked": snap.ban_blocked,
        }

    def lookup_geoip(self, ip: str) -> dict:
        """Look up GeoIP data for an IP address."""
        info = self._cpp.LookupGeoIP(ip)
        return {
            "country_code": info.country_code,
            "country_name": info.country_name,
            "city": info.city,
            "available": info.available,
        }

    def set_flood_config(self, flood_type: int, period_ms: int, max_tokens: int) -> None:
        """Set flood protection config for a message type."""
        self._cpp.SetFloodConfig(flood_type, period_ms, max_tokens)

    def get_flood_config(self, flood_type: int) -> tuple[int, int]:
        """Get flood protection config (period_ms, max_tokens) for a type."""
        return self._cpp.GetFloodConfig(flood_type)

    # Targeted messaging

    def send_to_active(self, message: str) -> bool:
        """Send a message to all active-mode users."""
        return self._cpp.SendToActive(message)

    def send_to_passive(self, message: str) -> bool:
        """Send a message to all passive-mode users."""
        return self._cpp.SendToPassive(message)

    def broadcast_chat(self, from_nick: str, message: str) -> bool:
        """Broadcast a chat message as a specific nick."""
        return self._cpp.BroadcastChat(from_nick, message)

    # Bot management

    def add_robot(self, nick: str, description: str, user_class: int) -> bool:
        """Register a bot nick on the hub."""
        return self._cpp.AddRobot(nick, description, user_class)

    def remove_robot(self, nick: str) -> bool:
        """Remove a bot from the hub."""
        return self._cpp.RemoveRobot(nick)

    # Active/passive counts

    def get_active_user_count(self) -> int:
        """Return the number of users in active mode."""
        return self._cpp.GetActiveUserCount()

    def get_passive_user_count(self) -> int:
        """Return the number of users in passive mode."""
        return self._cpp.GetPassiveUserCount()

    # Plugin management

    def load_plugin(self, path: str) -> bool:
        """Load a native plugin from the given path."""
        return self._cpp.LoadPlugin(path)

    def unload_plugin(self, name: str) -> bool:
        """Unload a native plugin by name."""
        return self._cpp.UnloadPlugin(name)

    def reload_plugin(self, name: str) -> bool:
        """Reload a native plugin by name."""
        return self._cpp.ReloadPlugin(name)

    def get_loaded_plugins(self) -> list[dict]:
        """Return info about loaded native plugins."""
        infos = self._cpp.GetLoadedPlugins()
        return [{"name": p.name, "path": p.path, "version": p.version} for p in infos]

    def is_plugin_loaded(self, name: str) -> bool:
        """Check if a plugin is currently loaded."""
        return self._cpp.IsPluginLoaded(name)

    # Lua script management

    def execute_lua_script(self, path: str) -> bool:
        """Load and execute a Lua script."""
        return self._cpp.ExecuteLuaScript(path)

    def unload_lua_script(self, path: str) -> bool:
        """Unload a Lua script."""
        return self._cpp.UnloadLuaScript(path)

    def get_loaded_lua_scripts(self) -> list[str]:
        """Return paths of loaded Lua scripts."""
        return list(self._cpp.GetLoadedLuaScripts())

    # Python script management

    def execute_python_script(self, path: str) -> bool:
        """Load and execute a Python script."""
        return self._cpp.ExecutePythonScript(path)

    def unload_python_script(self, path: str) -> bool:
        """Unload a Python script."""
        return self._cpp.UnloadPythonScript(path)

    def get_loaded_python_scripts(self) -> list[str]:
        """Return paths of loaded Python scripts."""
        return list(self._cpp.GetLoadedPythonScripts())

    # Ban cache management

    def load_ban_cache(self) -> None:
        """Reload ban cache from DB."""
        self._cpp.LoadBanCache([], [])

    def add_ban_cache_ip(self, ip: str) -> None:
        """Add an IP to the ban cache."""
        self._cpp.AddBanCacheIP(ip)

    def add_ban_cache_nick(self, nick: str) -> None:
        """Add a nick to the ban cache."""
        self._cpp.AddBanCacheNick(nick)

    def clear_ban_cache(self) -> None:
        """Clear all ban cache entries."""
        self._cpp.ClearBanCache()

    # Reload

    def request_reload(self) -> None:
        """Request a configuration reload."""
        self._cpp.RequestReload()
    
    # Configuration
    
    def get_config(self, section: str, key: str, default: str = "") -> str:
        """Get a configuration value."""
        return self._cpp.GetConfig(section, key, default)
    
    def set_config(self, section: str, key: str, value: str) -> bool:
        """Set a configuration value."""
        return self._cpp.SetConfig(section, key, value)
    
    # Context manager support
    
    def __enter__(self) -> "HubContext":
        return self
    
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.is_running:
            self.stop()


@asynccontextmanager
async def create_hub(config_dir: str | Path) -> AsyncGenerator[HubContext, None]:
    """
    Async context manager for creating and managing a hub.

    Publishes the context to the global singleton (``set_hub_context``) so
    that the FastAPI lifespan and other modules can discover it without
    creating a second instance.
    
    Example:
        async with create_hub("/etc/verlihub") as ctx:
            if ctx.initialize() and ctx.start():
                await ctx.wait_for_shutdown()
    """
    from verlihub.api.deps import set_hub_context

    ctx = HubContext.create(config_dir)
    if ctx is None:
        raise RuntimeError(f"Failed to create HubContext for {config_dir}")

    set_hub_context(ctx)
    try:
        yield ctx
    finally:
        if ctx.is_running:
            ctx.stop()
        set_hub_context(None)


def setup_signal_handlers(ctx: HubContext) -> None:
    """
    Set up signal handlers to gracefully shutdown the hub.

    Uses :meth:`asyncio.loop.add_signal_handler` when an event loop is
    running so the callback is invoked safely inside the loop (important
    because :class:`asyncio.Event.set` is *not* signal-safe).  Falls back
    to the classic :func:`signal.signal` API otherwise.

    Handles SIGTERM, SIGINT and SIGHUP.
    """
    loop: asyncio.AbstractEventLoop | None = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    if loop is not None:
        # asyncio-safe: the callback runs inside the event loop.
        loop.add_signal_handler(signal.SIGINT, ctx.request_shutdown, signal.SIGINT)
        loop.add_signal_handler(signal.SIGTERM, ctx.request_shutdown, signal.SIGTERM)
        loop.add_signal_handler(
            signal.SIGHUP, lambda: ctx.cpp.RequestReload()
        )
    else:
        # Fallback for non-asyncio callers.
        def _shutdown(signum: int, _frame: Any) -> None:
            logger.info("Received signal %d, shutting down...", signum)
            ctx.request_shutdown(signum)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGHUP, lambda _s, _f: ctx.cpp.RequestReload())


async def run_hub_server(
    config_dir: str,
    port: int = 0,
    listen_ip: str = "",
    *,
    hub_name: str = "",
    hub_topic: str = "",
    hub_desc: str = "",
    hub_owner: str = "",
    hub_encoding: str = "",
) -> None:
    """
    High-level coroutine that initialises, starts, and runs the NMDC hub
    until a shutdown signal is received.

    This is the entry-point used by ``verlihub-server`` in *hub* and *both*
    modes.

    Configuration values are passed to the C++ core through the
    ``IHubEventCallback.OnGetConfig`` director callback — no environment
    variables are involved.

    Args:
        config_dir:   Path to the verlihub configuration directory.
        port:         Port to listen on (0 = use value from config).
        listen_ip:    IP address to bind to (empty = use value from config).
        hub_name:     Hub name shown to DC clients.
        hub_topic:    Hub topic shown to DC clients.
        hub_desc:     Hub description for hub lists.
        hub_owner:    Hub owner nick.
        hub_encoding: Character encoding for legacy clients.

    Raises:
        RuntimeError: If context creation, initialisation, or start fails.
    """
    async with create_hub(config_dir) as ctx:
        # Build a config dict that the C++ side will pull via OnGetConfig
        # during Initialize() → LoadConfiguration().
        hub_section: dict[str, str] = {}
        if hub_name:
            hub_section["hub_name"] = hub_name
        if hub_topic:
            hub_section["hub_topic"] = hub_topic
        if hub_desc:
            hub_section["hub_desc"] = hub_desc
        if hub_owner:
            hub_section["hub_owner"] = hub_owner
        if hub_encoding:
            hub_section["hub_encoding"] = hub_encoding
        if port:
            hub_section["listen_port"] = str(port)
        if listen_ip:
            hub_section["listen_ip"] = listen_ip

        ctx.events.set_config({"hub": hub_section})

        if not ctx.initialize():
            raise RuntimeError("HubContext.initialize() failed")

        setup_signal_handlers(ctx)

        if not ctx.start(port=port, listen_ip=listen_ip):
            raise RuntimeError(
                f"HubContext.start(port={port}, listen_ip={listen_ip!r}) failed"
            )

        logger.info(
            "Hub running on %s:%d — press Ctrl-C to stop",
            listen_ip or "0.0.0.0",
            port,
        )
        await ctx.wait_for_shutdown()
        logger.info("Hub stopped")
