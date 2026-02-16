"""
Core bridge module for Verlihub C++ core.

This module provides a Pythonic interface to the C++ HubContext via SWIG bindings.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# Import SWIG module (built by CMake)
try:
    from verlihub import verlihub_core
except ImportError as e:
    raise ImportError(
        "verlihub_core module not found. "
        "Build with -DBUILD_PYTHON_BINDINGS=ON"
    ) from e

logger = logging.getLogger(__name__)


class HubEventHandler(verlihub_core.IHubEventCallback):
    """
    Python callback handler for hub events.
    
    Extends the SWIG-generated IHubEventCallback to dispatch events
    to registered Python handlers.
    
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
        }
        self._lock = threading.Lock()
    
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
        self._cpp.SetEventCallback(self._event_handler)
        self._shutdown_event = asyncio.Event()
    
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
        return self._cpp.Start(port, listen_ip)
    
    def stop(self) -> None:
        """Stop the hub server."""
        self._cpp.Stop()
        self._shutdown_event.set()
    
    def request_shutdown(self, signal_code: int = 0) -> None:
        """Request hub shutdown (can be called from signal handler)."""
        self._cpp.RequestShutdown(signal_code)
    
    async def wait_for_shutdown(self) -> None:
        """Wait asynchronously for hub shutdown."""
        await self._shutdown_event.wait()
    
    # User operations
    
    def get_user_nicks(self) -> list[str]:
        """Get list of all online user nicknames."""
        return list(self._cpp.GetUserNicks())
    
    def find_user(self, nick: str) -> bool:
        """Check if a user is online."""
        return self._cpp.FindUser(nick) is not None
    
    def send_to_user(self, nick: str, message: str) -> bool:
        """Send a message to a specific user."""
        return self._cpp.SendToUser(nick, message)
    
    def send_to_all(self, message: str) -> bool:
        """Broadcast message to all users."""
        return self._cpp.SendToAll(message)
    
    def send_to_class(self, message: str, min_class: int, max_class: int) -> bool:
        """Send message to users in class range."""
        return self._cpp.SendToClass(message, min_class, max_class)
    
    def kick_user(self, op_nick: str, nick: str, reason: str) -> bool:
        """Kick a user from the hub."""
        return self._cpp.KickUser(op_nick, nick, reason)
    
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
    
    Example:
        async with create_hub("/etc/verlihub") as ctx:
            if ctx.initialize() and ctx.start():
                await ctx.wait_for_shutdown()
    """
    ctx = HubContext.create(config_dir)
    if ctx is None:
        raise RuntimeError(f"Failed to create HubContext for {config_dir}")
    
    try:
        yield ctx
    finally:
        if ctx.is_running:
            ctx.stop()


def setup_signal_handlers(ctx: HubContext) -> None:
    """
    Set up Unix signal handlers to gracefully shutdown the hub.
    
    Handles SIGTERM, SIGINT, and SIGHUP.
    """
    def shutdown_handler(signum: int, frame: Any) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        ctx.request_shutdown(signum)
    
    def reload_handler(signum: int, frame: Any) -> None:
        logger.info("Received SIGHUP, reloading configuration...")
        ctx.cpp.RequestReload()
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGHUP, reload_handler)
