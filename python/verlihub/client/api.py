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
    # Statistics and Monitoring
    # =========================================================================
    
    def get_statistics(self) -> dict[str, Any]:
        """
        Get comprehensive hub statistics.
        
        Returns dict with:
            - users_online: int
            - max_users: int 
            - operators_online: int
            - bots_online: int
            - total_share: int (bytes)
            - total_share_formatted: str
            - average_share: int (bytes)
            - average_share_formatted: str
            - hub_name: str
            - uptime_seconds: int
            - uptime_formatted: str
        """
        return self._request("GET", "/stats/stats")
    
    def get_geo_distribution(self) -> dict[str, Any]:
        """
        Get geographic distribution of users.
        
        Returns dict with:
            - total_countries: int
            - distribution: list of {country_code, country_name, users, share, share_formatted}
        """
        return self._request("GET", "/stats/geo")
    
    def get_share_stats(self) -> dict[str, Any]:
        """
        Get share size statistics.
        
        Returns dict with:
            - total, total_formatted
            - average, average_formatted
            - median, median_formatted
            - max, max_formatted
            - min, min_formatted
        """
        return self._request("GET", "/stats/share")
    
    def get_operators(self) -> list[dict[str, Any]]:
        """
        Get list of online operators (class >= 3).
        
        Returns list of dicts with:
            - nick, user_class, class_name, ip, share, share_formatted
        """
        return self._request("GET", "/stats/ops")
    
    def get_bots(self) -> list[dict[str, Any]]:
        """
        Get list of hub bots.
        
        Returns list of dicts with:
            - nick, description
        """
        return self._request("GET", "/stats/bots")
    
    def get_detailed_users(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Get detailed list of online users with geo info and clone detection.
        
        Args:
            limit: Maximum number of users to return
            offset: Number of users to skip
        
        Returns list of dicts with:
            - nick, user_class, class_name
            - ip, host
            - country_code, country, city, region, asn
            - description, tag, email
            - share, share_formatted
            - is_clone, clone_group, same_ip_users
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        return self._request("GET", "/stats/users/detailed", params=params or None)
    
    def health_check(self) -> dict[str, Any]:
        """
        Perform health check.
        
        Returns dict with:
            - status: "healthy" or "degraded"
            - timestamp: ISO format
            - hub_running: bool
            - database_connected: bool
            - uptime_seconds: int
        """
        return self._request("GET", "/stats/health")
    
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

    # =========================================================================
    # Hub Config (full)
    # =========================================================================

    def get_hub_config(self) -> dict[str, Any]:
        """Get full hub configuration."""
        return self._request("GET", "/hub/config")

    def update_hub_config(self, **kwargs) -> dict[str, Any]:
        """Update hub configuration fields."""
        return self._request("PUT", "/hub/config", json=kwargs)

    # =========================================================================
    # LLM Status
    # =========================================================================

    def get_llm_status(self) -> dict[str, Any]:
        """Get LLM backend status."""
        return self._request("GET", "/llm/status")


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
    
    async def get_hub_info(self) -> dict[str, Any]:
        """Get full hub information."""
        return await self._request("GET", "/hub/info")
    
    # =========================================================================
    # Statistics and Monitoring
    # =========================================================================
    
    async def get_statistics(self) -> dict[str, Any]:
        """Get comprehensive hub statistics."""
        return await self._request("GET", "/stats/stats")
    
    async def get_geo_distribution(self) -> dict[str, Any]:
        """Get geographic distribution of users."""
        return await self._request("GET", "/stats/geo")
    
    async def get_share_stats(self) -> dict[str, Any]:
        """Get share size statistics."""
        return await self._request("GET", "/stats/share")
    
    async def get_operators(self) -> list[dict[str, Any]]:
        """Get list of online operators."""
        return await self._request("GET", "/stats/ops")
    
    async def get_bots(self) -> list[dict[str, Any]]:
        """Get list of hub bots."""
        return await self._request("GET", "/stats/bots")
    
    async def get_detailed_users(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get detailed list of online users."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        return await self._request("GET", "/stats/users/detailed", params=params or None)
    
    async def health_check(self) -> dict[str, Any]:
        """Perform health check."""
        return await self._request("GET", "/stats/health")
    
    # =========================================================================
    # User Operations
    # =========================================================================
    
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

    async def unban(self, ban_id: int) -> bool:
        """Remove a ban by ID."""
        result = await self._request("DELETE", f"/bans/{ban_id}")
        return result.get("status") == "removed"

    # =========================================================================
    # Hub Management (lifecycle, config, topic)
    # =========================================================================

    @property
    def user_class(self) -> int:
        """Return the authenticated user's class."""
        return self._user_class

    async def start(self) -> dict[str, Any]:
        """Start the hub."""
        return await self._request("POST", "/hub/start")

    async def stop(self) -> dict[str, Any]:
        """Stop the hub."""
        return await self._request("POST", "/hub/shutdown")

    async def restart(self) -> dict[str, Any]:
        """Restart the hub."""
        return await self._request("POST", "/hub/restart")

    @property
    def is_running(self) -> bool:
        """Check if the hub is running (requires a prior status call)."""
        # Async property workaround — callers should await get_hub_info() instead
        return self.is_authenticated

    async def reload_config(self) -> dict[str, Any]:
        """Reload hub configuration."""
        return await self._request("POST", "/hub/reload")

    async def get_hub_name(self) -> str:
        """Get the hub name."""
        info = await self.get_hub_info()
        return info.get("hub_name", "")

    async def get_hub_topic(self) -> str:
        """Get the hub topic."""
        info = await self.get_hub_info()
        return info.get("hub_topic", "")

    async def set_hub_topic(self, topic: str) -> dict[str, Any]:
        """Set the hub topic."""
        return await self._request("PUT", "/hub/topic", json={"topic": topic})

    async def get_total_share(self) -> int:
        """Get total share in bytes."""
        stats = await self.get_hub_stats()
        return stats.get("total_share", 0)

    async def get_hub_config(self) -> dict[str, Any]:
        """Get full hub configuration."""
        return await self._request("GET", "/hub/config")

    async def update_hub_config(self, **kwargs) -> dict[str, Any]:
        """Update hub configuration fields."""
        return await self._request("PUT", "/hub/config", json=kwargs)

    # =========================================================================
    # User Info & Management (extended)
    # =========================================================================

    async def get_user_info(self, nick: str) -> dict[str, Any]:
        """Get info for a specific online user."""
        return await self._request("GET", f"/users/{nick}")

    async def drop_user(self, nick: str) -> bool:
        """Drop a user's connection."""
        result = await self._request("POST", f"/users/{nick}/drop")
        return result.get("status") == "dropped"

    async def redirect_user(self, nick: str, address: str) -> bool:
        """Redirect a user to another hub."""
        result = await self._request("POST", f"/users/{nick}/redirect", json={
            "address": address,
        })
        return result.get("status") == "redirected"

    async def send_to_class(
        self,
        user_class: int,
        message: str,
    ) -> dict[str, Any]:
        """Send a message to all users of a specific class."""
        return await self._request("POST", "/hub/chat", json={
            "message": message,
            "min_class": user_class,
        })

    # =========================================================================
    # Registered Users (extended)
    # =========================================================================

    async def delete_registration(self, nick: str) -> bool:
        """Delete a user registration."""
        result = await self._request("DELETE", f"/users/registered/{nick}")
        return result.get("status") == "deleted"

    async def update_user(self, nick: str, **kwargs) -> dict[str, Any]:
        """Update a registered user."""
        return await self._request("PATCH", f"/users/registered/{nick}", json=kwargs)

    # =========================================================================
    # Configuration (per-section/key)
    # =========================================================================

    async def get_config(self, section: str, key: str, default: str = "") -> str:
        """Get a configuration value."""
        try:
            result = await self._request("GET", f"/config/{section}/{key}")
            return result.get("value", default)
        except HubClientError:
            return default

    async def set_config(self, section: str, key: str, value: str) -> bool:
        """Set a configuration value."""
        result = await self._request("PUT", f"/config/{section}/{key}", json={
            "value": value,
        })
        return result.get("status") == "updated"

    # =========================================================================
    # LLM Status
    # =========================================================================

    async def get_llm_status(self) -> dict[str, Any]:
        """Get LLM backend status."""
        return await self._request("GET", "/llm/status")
