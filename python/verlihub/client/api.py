"""
REST API Client for Verlihub

Provides synchronous and asynchronous clients for remote hub management
via the Verlihub REST API. These clients mirror the local HubBridge
interface for seamless local/remote switching.

Example - Synchronous:
    from verlihub.client import HubClient
    
    with HubClient("https://myhub.example.com/api/v1") as hub:
        hub.login("admin", "password")
        print(f"Users: {hub.get_user_count()}")
        hub.send_to_all("Announcement")
        hub.kick_user("admin", "spammer", "Flooding")

Example - Asynchronous:
    from verlihub.client import AsyncHubClient
    
    async with AsyncHubClient("https://myhub.example.com/api/v1") as hub:
        await hub.login("admin", "password")
        users = await hub.get_user_list()
        await hub.send_to_all("Hello!")
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class HubClientError(Exception):
    """Base exception for HubClient errors."""
    pass


class AuthenticationError(HubClientError):
    """Authentication failed."""
    pass


class PermissionError(HubClientError):
    """Insufficient permissions."""
    pass


class APIError(HubClientError):
    """API request failed."""
    def __init__(self, message: str, status_code: int = 0, response: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


@dataclass
class HubClientConfig:
    """Configuration for HubClient connection."""
    base_url: str
    timeout: float = 30.0
    verify_ssl: bool = True
    max_retries: int = 3
    retry_delay: float = 1.0


class HubClient:
    """
    Synchronous REST API client for remote Verlihub hub management.
    
    Provides the same interface as HubBridge but communicates via REST API.
    All methods are thread-safe.
    
    Args:
        base_url: API base URL (e.g., "https://myhub.com/api/v1")
        timeout: Request timeout in seconds
        verify_ssl: Whether to verify SSL certificates
    
    Example:
        with HubClient("https://myhub.example.com/api/v1") as hub:
            hub.login("admin", "password")
            
            # Hub operations
            print(f"Users: {hub.get_user_count()}")
            hub.kick_user("admin", "spammer", "Flooding")
            hub.send_to_all("Server maintenance in 5 minutes")
    """
    
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ):
        if not HTTPX_AVAILABLE:
            raise ImportError(
                "httpx is required for HubClient. "
                "Install with: pip install httpx"
            )
        
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._user_class: int = 0
        self._user_nick: str = ""
        self._lock = threading.Lock()
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            verify=verify_ssl,
        )
    
    def __enter__(self) -> "HubClient":
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
    
    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
    
    # =========================================================================
    # Authentication
    # =========================================================================
    
    def login(self, username: str, password: str) -> bool:
        """
        Authenticate with the hub API.
        
        Args:
            username: Hub username (nick)
            password: User password
            
        Returns:
            True if login succeeded
            
        Raises:
            AuthenticationError: If credentials are invalid
        """
        try:
            response = self._client.post(
                "/auth/login",
                json={"nick": username, "password": password},
            )
            response.raise_for_status()
            data = response.json()
            
            with self._lock:
                self._token = data["access_token"]
                expires_in = data.get("expires_in", 3600)
                self._token_expires = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                self._user_class = data.get("user_class", 0)
                self._user_nick = username
            
            return True
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise AuthenticationError("Invalid credentials")
            raise HubClientError(f"Login failed: {e}")
    
    def logout(self) -> None:
        """Clear authentication state."""
        with self._lock:
            self._token = None
            self._token_expires = None
            self._user_class = 0
            self._user_nick = ""
    
    @property
    def is_authenticated(self) -> bool:
        """Check if client is authenticated with valid token."""
        with self._lock:
            if not self._token:
                return False
            if self._token_expires and datetime.now(timezone.utc) > self._token_expires:
                return False
            return True
    
    @property
    def user_class(self) -> int:
        """Get current user's class level."""
        with self._lock:
            return self._user_class
    
    def _headers(self) -> dict[str, str]:
        """Get authorization headers."""
        with self._lock:
            if self._token:
                return {"Authorization": f"Bearer {self._token}"}
            return {}
    
    def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> Any:
        """Make authenticated API request."""
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        
        try:
            response = self._client.request(
                method,
                endpoint,
                headers=headers,
                **kwargs,
            )
            response.raise_for_status()
            
            if response.headers.get("content-type", "").startswith("application/json"):
                return response.json()
            return response.text
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise AuthenticationError("Not authenticated or token expired")
            if e.response.status_code == 403:
                raise PermissionError("Insufficient permissions")
            raise APIError(
                f"API error: {e}",
                status_code=e.response.status_code,
                response=e.response.text,
            )
    
    # =========================================================================
    # Hub Lifecycle
    # =========================================================================
    
    def start(self, port: int = 4111, listen_ip: str = "0.0.0.0") -> bool:
        """Start the hub server (requires Master class)."""
        result = self._request("POST", "/hub/start", json={
            "port": port,
            "listen_ip": listen_ip,
        })
        return result.get("status") == "started"
    
    def stop(self) -> bool:
        """Stop the hub server (requires Master class)."""
        result = self._request("POST", "/hub/stop")
        return result.get("status") == "stopped"
    
    def restart(self, port: int = 4111, listen_ip: str = "0.0.0.0") -> bool:
        """Restart the hub server."""
        self.stop()
        return self.start(port, listen_ip)
    
    @property
    def is_running(self) -> bool:
        """Check if hub is running."""
        try:
            result = self._request("GET", "/hub/status")
            return result.get("is_running", False)
        except HubClientError:
            return False
    
    def reload_config(self) -> bool:
        """Reload hub configuration (requires Admin class)."""
        result = self._request("POST", "/hub/reload")
        return result.get("status") == "ok"
    
    # =========================================================================
    # Hub Information
    # =========================================================================
    
    def get_hub_info(self) -> dict[str, Any]:
        """Get hub information."""
        return self._request("GET", "/hub/info")
    
    def get_hub_name(self) -> str:
        """Get hub name."""
        result = self.get_hub_info()
        return result.get("hub_name", "")
    
    def get_hub_topic(self) -> str:
        """Get hub topic."""
        result = self.get_hub_info()
        return result.get("topic", "")
    
    def set_hub_topic(self, topic: str) -> bool:
        """Set hub topic (requires Operator class)."""
        result = self._request("PUT", "/hub/topic", json={"topic": topic})
        return result.get("status") == "ok"
    
    def get_total_share(self) -> int:
        """Get total share size in bytes."""
        result = self._request("GET", "/hub/stats")
        return result.get("total_share", 0)
    
    def get_hub_stats(self) -> dict[str, Any]:
        """Get hub statistics."""
        return self._request("GET", "/hub/stats")
    
    # =========================================================================
    # User Operations
    # =========================================================================
    
    def get_user_count(self) -> int:
        """Get number of online users."""
        result = self._request("GET", "/hub/stats")
        return result.get("user_count", 0)
    
    def get_user_list(self) -> list[str]:
        """Get list of online user nicknames."""
        result = self._request("GET", "/users/online")
        return result.get("users", [])
    
    def get_user_info(self, nick: str) -> dict[str, Any]:
        """Get detailed info for an online user."""
        return self._request("GET", f"/users/online/{nick}")
    
    def kick_user(self, op: str, nick: str, reason: str) -> bool:
        """Kick a user from the hub (requires Operator class)."""
        result = self._request("POST", f"/users/{nick}/kick", json={
            "operator": op,
            "reason": reason,
        })
        return result.get("status") == "kicked"
    
    def drop_user(self, nick: str) -> bool:
        """Drop a user's connection."""
        result = self._request("POST", f"/users/{nick}/drop")
        return result.get("status") == "dropped"
    
    def redirect_user(self, nick: str, target_hub: str, reason: str = "") -> bool:
        """Redirect user to another hub."""
        result = self._request("POST", f"/users/{nick}/redirect", json={
            "target": target_hub,
            "reason": reason,
        })
        return result.get("status") == "redirected"
    
    # =========================================================================
    # Messaging
    # =========================================================================
    
    def send_to_user(self, nick: str, message: str) -> bool:
        """Send a private message to a user."""
        result = self._request("POST", f"/users/{nick}/message", json={
            "message": message,
        })
        return result.get("status") == "sent"
    
    def send_to_all(self, message: str) -> bool:
        """Broadcast a message to all users."""
        result = self._request("POST", "/hub/broadcast", json={
            "message": message,
        })
        return result.get("status") == "ok"
    
    def send_to_class(self, message: str, min_class: int, max_class: int) -> bool:
        """Send a message to users in a class range."""
        result = self._request("POST", "/messages/class", json={
            "message": message,
            "min_class": min_class,
            "max_class": max_class,
        })
        return result.get("status") == "sent"
    
    # =========================================================================
    # Registered Users
    # =========================================================================
    
    def get_registered_users(
        self,
        limit: int = 100,
        offset: int = 0,
        class_filter: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Get list of registered users."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if class_filter is not None:
            params["class"] = class_filter
        return self._request("GET", "/users/registered", params=params)
    
    def register_user(
        self,
        nick: str,
        password: str,
        user_class: int = 1,
    ) -> dict[str, Any]:
        """Register a new user."""
        return self._request("POST", "/users/registered", json={
            "nick": nick,
            "password": password,
            "user_class": user_class,
        })
    
    def delete_registration(self, nick: str) -> bool:
        """Delete a user registration."""
        result = self._request("DELETE", f"/users/registered/{nick}")
        return result.get("status") == "deleted"
    
    def update_user(self, nick: str, **kwargs) -> dict[str, Any]:
        """Update a registered user."""
        return self._request("PATCH", f"/users/registered/{nick}", json=kwargs)
    
    # =========================================================================
    # Ban Management
    # =========================================================================
    
    def get_bans(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Get list of active bans."""
        return self._request("GET", "/bans", params={
            "limit": limit,
            "offset": offset,
        })
    
    def ban_user(
        self,
        nick: str,
        reason: str,
        duration_hours: int = 0,
        ban_ip: bool = True,
    ) -> dict[str, Any]:
        """Ban a user."""
        return self._request("POST", "/bans", json={
            "nick": nick,
            "reason": reason,
            "duration_hours": duration_hours,
            "ban_ip": ban_ip,
        })
    
    def unban(self, ban_id: int) -> bool:
        """Remove a ban by ID."""
        result = self._request("DELETE", f"/bans/{ban_id}")
        return result.get("status") == "removed"
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def get_config(self, section: str, key: str, default: str = "") -> str:
        """Get a configuration value."""
        try:
            result = self._request("GET", f"/config/{section}/{key}")
            return result.get("value", default)
        except HubClientError:
            return default
    
    def set_config(self, section: str, key: str, value: str) -> bool:
        """Set a configuration value."""
        result = self._request("PUT", f"/config/{section}/{key}", json={
            "value": value,
        })
        return result.get("status") == "updated"


class AsyncHubClient:
    """
    Asynchronous REST API client for remote Verlihub hub management.
    
    Same interface as HubClient but with async methods.
    
    Example:
        async with AsyncHubClient("https://myhub.com/api/v1") as hub:
            await hub.login("admin", "password")
            users = await hub.get_user_list()
            await hub.send_to_all("Hello!")
    """
    
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ):
        if not HTTPX_AVAILABLE:
            raise ImportError(
                "httpx is required for AsyncHubClient. "
                "Install with: pip install httpx"
            )
        
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._user_class: int = 0
        self._user_nick: str = ""
        self._lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self) -> "AsyncHubClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client:
            await self._client.aclose()
    
    # =========================================================================
    # Authentication
    # =========================================================================
    
    async def login(self, username: str, password: str) -> bool:
        """Authenticate with the hub API."""
        response = await self._client.post(
            "/auth/login",
            json={"nick": username, "password": password},
        )
        response.raise_for_status()
        data = response.json()
        
        async with self._lock:
            self._token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self._token_expires = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            self._user_class = data.get("user_class", 0)
            self._user_nick = username
        
        return True
    
    async def logout(self) -> None:
        """Clear authentication state."""
        async with self._lock:
            self._token = None
            self._token_expires = None
            self._user_class = 0
    
    @property
    def is_authenticated(self) -> bool:
        """Check if authenticated."""
        if not self._token:
            return False
        if self._token_expires and datetime.now(timezone.utc) > self._token_expires:
            return False
        return True
    
    async def _headers(self) -> dict[str, str]:
        """Get authorization headers."""
        async with self._lock:
            if self._token:
                return {"Authorization": f"Bearer {self._token}"}
            return {}
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """Make authenticated API request."""
        headers = await self._headers()
        headers.update(kwargs.pop("headers", {}))
        
        response = await self._client.request(method, endpoint, headers=headers, **kwargs)
        response.raise_for_status()
        
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return response.text
    
    # =========================================================================
    # Hub Operations
    # =========================================================================
    
    async def get_hub_stats(self) -> dict[str, Any]:
        """Get hub statistics."""
        return await self._request("GET", "/hub/stats")
    
    async def get_user_count(self) -> int:
        """Get number of online users."""
        result = await self.get_hub_stats()
        return result.get("user_count", 0)
    
    async def get_user_list(self) -> list[str]:
        """Get list of online user nicknames."""
        result = await self._request("GET", "/users/online")
        return result.get("users", [])
    
    async def kick_user(self, op: str, nick: str, reason: str) -> bool:
        """Kick a user from the hub."""
        result = await self._request("POST", f"/users/{nick}/kick", json={
            "operator": op,
            "reason": reason,
        })
        return result.get("status") == "kicked"
    
    async def send_to_all(self, message: str) -> bool:
        """Broadcast a message to all users."""
        result = await self._request("POST", "/hub/broadcast", json={
            "message": message,
        })
        return result.get("status") == "ok"
    
    async def send_to_user(self, nick: str, message: str) -> bool:
        """Send a private message to a user."""
        result = await self._request("POST", f"/users/{nick}/message", json={
            "message": message,
        })
        return result.get("status") == "sent"
    
    # =========================================================================
    # Registered Users
    # =========================================================================
    
    async def get_registered_users(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get list of registered users."""
        return await self._request("GET", "/users/registered", params={
            "limit": limit,
            "offset": offset,
        })
    
    async def register_user(
        self,
        nick: str,
        password: str,
        user_class: int = 1,
    ) -> dict[str, Any]:
        """Register a new user."""
        return await self._request("POST", "/users/registered", json={
            "nick": nick,
            "password": password,
            "user_class": user_class,
        })
    
    # =========================================================================
    # Bans
    # =========================================================================
    
    async def get_bans(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Get list of active bans."""
        return await self._request("GET", "/bans", params={
            "limit": limit,
            "offset": offset,
        })
    
    async def ban_user(
        self,
        nick: str,
        reason: str,
        duration_hours: int = 0,
    ) -> dict[str, Any]:
        """Ban a user."""
        return await self._request("POST", "/bans", json={
            "nick": nick,
            "reason": reason,
            "duration_hours": duration_hours,
        })
