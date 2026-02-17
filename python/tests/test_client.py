"""
Tests for Verlihub client modules.

Tests for:
- NMDCClient (NMDC protocol client)
- HubClient (REST API client)
- AsyncHubClient (Async REST API client)
"""
import pytest
from unittest import mock
from datetime import datetime, timezone
import socket
import threading
import time

from verlihub.client.nmdc import (
    NMDCClient,
    NMDCClientConfig,
    NMDCError,
    NMDCConnectionError,
    ConnectionState,
    HubInfo,
    UserInfo,
)
from verlihub.client.api import (
    HubClient,
    AsyncHubClient,
    HubClientError,
    AuthenticationError,
    PermissionError,
)


# =============================================================================
# NMDC Client Tests
# =============================================================================


class TestNMDCClientConfig:
    """Tests for NMDC client configuration."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = NMDCClientConfig(
            host="localhost",
            port=4111,
            nick="TestBot",
        )
        assert config.host == "localhost"
        assert config.port == 4111
        assert config.nick == "TestBot"
        assert config.password == ""
        assert config.share_size == 0
        assert config.slots == 1
        assert config.timeout == 30.0
        
    def test_custom_config(self):
        """Test custom configuration."""
        config = NMDCClientConfig(
            host="hub.example.com",
            port=411,
            nick="MyBot",
            password="secret",
            share_size=1024,
            slots=5,
            description="My Test Bot",
            debug=True,
        )
        assert config.host == "hub.example.com"
        assert config.port == 411
        assert config.password == "secret"
        assert config.share_size == 1024
        assert config.description == "My Test Bot"
        assert config.debug is True


class TestNMDCClientKeyCalculation:
    """Tests for NMDC lock-to-key calculation."""
    
    def test_calculate_key_simple(self):
        """Test key calculation with simple lock."""
        # Test a known lock/key pair
        lock = "EXTENDEDPROTOCOL"
        key = NMDCClient._calculate_key(lock)
        
        # Key should be non-empty
        assert key is not None
        assert len(key) > 0
        
    def test_calculate_key_with_special_chars(self):
        """Test key calculation escapes special characters."""
        # Create a lock that would produce special chars
        lock = "TEST" + chr(0) * 5  # Would produce 0 bytes
        key = NMDCClient._calculate_key(lock)
        
        # Should handle without crashing
        assert key is not None


class TestNMDCClientInit:
    """Tests for NMDC client initialization."""
    
    def test_init_simple(self):
        """Test simple client initialization."""
        client = NMDCClient("localhost", 4111, "TestBot")
        
        assert client.nick == "TestBot"
        assert client._config.host == "localhost"
        assert client._config.port == 4111
        assert client._state == ConnectionState.DISCONNECTED
        assert not client.is_connected
        
    def test_init_with_password(self):
        """Test initialization with password."""
        client = NMDCClient("localhost", 4111, "Admin", "secret")
        
        assert client._config.password == "secret"
        
    def test_init_with_config(self):
        """Test initialization with config object."""
        config = NMDCClientConfig(
            host="hub.example.com",
            port=411,
            nick="CustomBot",
            password="pass123",
            description="Custom Bot",
        )
        client = NMDCClient("ignored", 0, "ignored", config=config)
        
        assert client._config.host == "hub.example.com"
        assert client._config.nick == "CustomBot"
        assert client._config.description == "Custom Bot"


class TestNMDCClientProperties:
    """Tests for NMDC client properties."""
    
    def test_hub_info_initial(self):
        """Test hub info is empty initially."""
        client = NMDCClient("localhost", 4111, "Test")
        
        info = client.hub_info
        assert info.name == ""
        assert info.user_count == 0
        assert info.connected_at is None
        
    def test_users_collection_empty(self):
        """Test users collection is empty initially."""
        client = NMDCClient("localhost", 4111, "Test")
        
        assert client.users == {}
        assert client.user_count == 0
        assert client.operators == set()


class TestNMDCClientCallbacks:
    """Tests for NMDC client callbacks."""
    
    def test_chat_callback_can_be_set(self):
        """Test chat callback can be set."""
        client = NMDCClient("localhost", 4111, "Test")
        
        received = []
        def on_chat(nick, msg):
            received.append((nick, msg))
        
        client.on_chat_message = on_chat
        
        # Simulate receiving a chat message
        client._handle_chat("<SomeUser> Hello world")
        
        assert len(received) == 1
        assert received[0] == ("SomeUser", "Hello world")
        
    def test_pm_callback_can_be_set(self):
        """Test PM callback can be set."""
        client = NMDCClient("localhost", 4111, "Test")
        
        received = []
        def on_pm(from_nick, to_nick, msg):
            received.append((from_nick, to_nick, msg))
        
        client.on_private_message = on_pm
        
        # Simulate receiving a PM
        client._handle_pm("$To: Test From: Admin $<Admin> Hello privately")
        
        assert len(received) == 1
        assert received[0] == ("Admin", "Test", "Hello privately")


class TestNMDCClientMessageParsing:
    """Tests for NMDC message parsing."""
    
    def test_parse_hubname(self):
        """Test HubName parsing."""
        client = NMDCClient("localhost", 4111, "Test")
        client._handle_message("$HubName My Awesome Hub")
        
        assert client.hub_info.name == "My Awesome Hub"
        
    def test_parse_hubtopic(self):
        """Test HubTopic parsing."""
        client = NMDCClient("localhost", 4111, "Test")
        client._handle_message("$HubTopic Welcome to the hub!")
        
        assert client.hub_info.topic == "Welcome to the hub!"
        
    def test_parse_nicklist(self):
        """Test NickList parsing."""
        client = NMDCClient("localhost", 4111, "Test")
        client._handle_message("$NickList User1$$User2$$User3$$")
        
        assert "User1" in client.users
        assert "User2" in client.users
        assert "User3" in client.users
        assert client.user_count == 3
        
    def test_parse_oplist(self):
        """Test OpList parsing."""
        client = NMDCClient("localhost", 4111, "Test")
        # First add users
        client._handle_message("$NickList Admin$$User1$$")
        client._handle_message("$OpList Admin$$")
        
        assert "Admin" in client.operators
        assert "User1" not in client.operators
        assert client.users["Admin"].is_op is True
        
    def test_parse_quit(self):
        """Test Quit message parsing."""
        client = NMDCClient("localhost", 4111, "Test")
        client._handle_message("$NickList User1$$User2$$")
        assert "User1" in client.users
        
        client._handle_message("$Quit User1")
        assert "User1" not in client.users


# =============================================================================
# HubClient (REST API) Tests
# =============================================================================


class TestHubClientInit:
    """Tests for HubClient initialization."""
    
    def test_init(self):
        """Test basic initialization."""
        client = HubClient("https://hub.example.com/api/v1")
        
        assert client._base_url == "https://hub.example.com/api/v1"
        assert not client.is_authenticated
        assert client.user_class == 0
        
        client.close()
        
    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from URL."""
        client = HubClient("https://hub.example.com/api/v1/")
        assert client._base_url == "https://hub.example.com/api/v1"
        client.close()
        
    def test_init_custom_timeout(self):
        """Test custom timeout."""
        client = HubClient("https://example.com", timeout=60.0)
        assert client._timeout == 60.0
        client.close()


class TestHubClientAuth:
    """Tests for HubClient authentication."""
    
    def test_not_authenticated_initially(self):
        """Test client is not authenticated initially."""
        client = HubClient("https://example.com")
        
        assert not client.is_authenticated
        assert client.user_class == 0
        
        client.close()
        
    def test_logout_clears_state(self):
        """Test logout clears authentication state."""
        client = HubClient("https://example.com")
        
        # Manually set some auth state
        client._token = "test_token"
        client._user_class = 5
        client._user_nick = "admin"
        
        client.logout()
        
        assert client._token is None
        assert client.user_class == 0
        assert not client.is_authenticated
        
        client.close()


class TestHubClientContextManager:
    """Tests for HubClient context manager."""
    
    def test_context_manager(self):
        """Test using client as context manager."""
        with HubClient("https://example.com") as client:
            assert client is not None
            assert isinstance(client, HubClient)
        # Should not raise after exiting


# =============================================================================
# AsyncHubClient Tests
# =============================================================================


class TestAsyncHubClientInit:
    """Tests for AsyncHubClient initialization."""
    
    def test_init(self):
        """Test basic initialization."""
        client = AsyncHubClient("https://hub.example.com/api/v1")
        
        assert client._base_url == "https://hub.example.com/api/v1"
        assert not client.is_authenticated
        
    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from URL."""
        client = AsyncHubClient("https://hub.example.com/api/v1/")
        assert client._base_url == "https://hub.example.com/api/v1"


@pytest.mark.asyncio
class TestAsyncHubClientContextManager:
    """Tests for AsyncHubClient context manager."""
    
    async def test_async_context_manager(self):
        """Test using async client as context manager."""
        async with AsyncHubClient("https://example.com") as client:
            assert client is not None
            assert isinstance(client, AsyncHubClient)
        # Should not raise after exiting


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestClientErrors:
    """Tests for client error classes."""
    
    def test_hub_client_error(self):
        """Test HubClientError exception."""
        error = HubClientError("Something went wrong")
        assert str(error) == "Something went wrong"
        
    def test_authentication_error(self):
        """Test AuthenticationError exception."""
        error = AuthenticationError("Invalid credentials")
        assert isinstance(error, HubClientError)
        
    def test_permission_error(self):
        """Test PermissionError exception."""
        error = PermissionError("Access denied")
        assert isinstance(error, HubClientError)
        
    def test_nmdc_error(self):
        """Test NMDCError exception."""
        error = NMDCError("Protocol error")
        assert str(error) == "Protocol error"
        
    def test_nmdc_connection_error(self):
        """Test NMDCConnectionError exception."""
        error = NMDCConnectionError("Connection refused")
        assert isinstance(error, NMDCError)


# =============================================================================
# HubClient Tests
# =============================================================================


class TestHubClientInit:
    """Tests for HubClient initialization."""
    
    def test_init_simple(self):
        """Test simple client initialization."""
        client = HubClient("http://localhost:8000/api/v1")
        assert client._base_url == "http://localhost:8000/api/v1"
        assert not client.is_authenticated
        client.close()
    
    def test_init_with_options(self):
        """Test client initialization with options."""
        client = HubClient(
            "http://localhost:8000/api/v1",
            timeout=60.0,
            verify_ssl=False,
        )
        assert client._timeout == 60.0
        assert client._verify_ssl is False
        client.close()
    
    def test_context_manager(self):
        """Test client as context manager."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert client is not None
            assert not client.is_authenticated


class TestHubClientAuth:
    """Tests for HubClient authentication."""
    
    def test_not_authenticated_initially(self):
        """Test client is not authenticated initially."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert not client.is_authenticated
            assert client.user_class == 0
    
    def test_logout_clears_state(self):
        """Test logout clears authentication state."""
        with HubClient("http://localhost:8000/api/v1") as client:
            # Manually set some state
            client._token = "fake_token"
            client._user_class = 5
            client._user_nick = "admin"
            
            client.logout()
            
            assert not client.is_authenticated
            assert client._token is None
            assert client.user_class == 0


class TestHubClientMethods:
    """Tests for HubClient methods without network."""
    
    def test_headers_without_auth(self):
        """Test headers when not authenticated."""
        with HubClient("http://localhost:8000/api/v1") as client:
            headers = client._headers()
            assert headers == {}
    
    def test_headers_with_auth(self):
        """Test headers when authenticated."""
        from datetime import timedelta
        
        with HubClient("http://localhost:8000/api/v1") as client:
            client._token = "test_token"
            client._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
            
            headers = client._headers()
            assert headers == {"Authorization": "Bearer test_token"}


class TestHubClientStatisticsMethods:
    """Tests for HubClient statistics method interfaces."""
    
    def test_get_statistics_method_exists(self):
        """Test get_statistics method exists."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert hasattr(client, "get_statistics")
            assert callable(client.get_statistics)
    
    def test_get_geo_distribution_method_exists(self):
        """Test get_geo_distribution method exists."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert hasattr(client, "get_geo_distribution")
            assert callable(client.get_geo_distribution)
    
    def test_get_share_stats_method_exists(self):
        """Test get_share_stats method exists."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert hasattr(client, "get_share_stats")
            assert callable(client.get_share_stats)
    
    def test_get_operators_method_exists(self):
        """Test get_operators method exists."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert hasattr(client, "get_operators")
            assert callable(client.get_operators)
    
    def test_get_bots_method_exists(self):
        """Test get_bots method exists."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert hasattr(client, "get_bots")
            assert callable(client.get_bots)
    
    def test_get_detailed_users_method_exists(self):
        """Test get_detailed_users method exists."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert hasattr(client, "get_detailed_users")
            assert callable(client.get_detailed_users)
    
    def test_health_check_method_exists(self):
        """Test health_check method exists."""
        with HubClient("http://localhost:8000/api/v1") as client:
            assert hasattr(client, "health_check")
            assert callable(client.health_check)


class TestAsyncHubClientMethods:
    """Tests for AsyncHubClient statistics method interfaces."""
    
    def test_async_statistics_methods_exist(self):
        """Test async statistics methods exist."""
        # AsyncHubClient uses httpx.AsyncClient internally
        # Just check the methods exist
        assert hasattr(AsyncHubClient, "get_statistics")
        assert hasattr(AsyncHubClient, "get_geo_distribution")
        assert hasattr(AsyncHubClient, "get_share_stats")
        assert hasattr(AsyncHubClient, "get_operators")
        assert hasattr(AsyncHubClient, "get_bots")
        assert hasattr(AsyncHubClient, "get_detailed_users")
        assert hasattr(AsyncHubClient, "health_check")
        assert hasattr(AsyncHubClient, "get_hub_info")


# =============================================================================
# Client Exception Tests
# =============================================================================


class TestClientExceptions:
    """Tests for client exceptions."""
    
    def test_hub_client_error(self):
        """Test HubClientError exception."""
        error = HubClientError("API error")
        assert str(error) == "API error"
    
    def test_authentication_error(self):
        """Test AuthenticationError exception."""
        error = AuthenticationError("Invalid credentials")
        assert isinstance(error, HubClientError)
        assert str(error) == "Invalid credentials"
    
    def test_permission_error(self):
        """Test PermissionError exception."""
        error = PermissionError("Insufficient permissions")
        assert isinstance(error, HubClientError)


# =============================================================================
# Data Classes Tests
# =============================================================================


class TestDataClasses:
    """Tests for client data classes."""
    
    def test_hub_info(self):
        """Test HubInfo data class."""
        info = HubInfo(
            name="Test Hub",
            topic="Welcome!",
            user_count=42,
            share_total=1024 * 1024 * 1024,
            connected_at=datetime.now(timezone.utc),
        )
        
        assert info.name == "Test Hub"
        assert info.topic == "Welcome!"
        assert info.user_count == 42
        assert info.connected_at is not None
        
    def test_user_info(self):
        """Test UserInfo data class."""
        user = UserInfo(
            nick="TestUser",
            description="A test user",
            connection="Cable",
            email="test@example.com",
            share=1024,
            is_op=True,
        )
        
        assert user.nick == "TestUser"
        assert user.is_op is True
        assert user.share == 1024


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
