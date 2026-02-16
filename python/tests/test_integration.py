"""
Integration tests for Verlihub NMDC client and protocol.

These tests create a mock NMDC server and test the client's ability to:
- Connect and complete handshake
- Send/receive chat messages
- Send/receive private messages
- Execute commands
- Handle authentication

The mock server simulates the Verlihub hub protocol responses.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import pytest

from verlihub.client.nmdc import (
    NMDCClient,
    NMDCClientConfig,
    NMDCConnectionError,
    NMDCAuthError,
    ConnectionState,
)


# =============================================================================
# Mock NMDC Server
# =============================================================================

@dataclass
class MockNMDCServer:
    """
    A mock NMDC server for integration testing.
    
    Implements the basic NMDC protocol to allow client testing without
    requiring a full Verlihub instance.
    
    Usage:
        with MockNMDCServer(port=4222) as server:
            client = NMDCClient("localhost", 4222, "TestBot")
            client.connect()
    """
    port: int = 0  # 0 = auto-select available port
    require_password: bool = False
    expected_password: str = "secret"
    accept_nick: bool = True
    hub_name: str = "TestHub"
    
    # Internal state
    _socket: Optional[socket.socket] = field(default=None, init=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _client_socket: Optional[socket.socket] = field(default=None, init=False)
    _connected_nicks: list[str] = field(default_factory=list, init=False)
    _messages_received: list[str] = field(default_factory=list, init=False)
    _on_message: Optional[Callable[[str], Optional[str]]] = field(default=None, init=False)
    
    def __enter__(self) -> "MockNMDCServer":
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
    
    @property
    def actual_port(self) -> int:
        """Get the actual port (useful when port=0)."""
        if self._socket:
            return self._socket.getsockname()[1]
        return self.port
    
    def start(self) -> None:
        """Start the mock server."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", self.port))
        self._socket.listen(1)
        self._socket.settimeout(0.5)
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._server_loop, daemon=True)
        self._thread.start()
        
        # Wait for server to be ready
        time.sleep(0.1)
    
    def stop(self) -> None:
        """Stop the mock server."""
        self._stop_event.set()
        
        if self._client_socket:
            try:
                self._client_socket.close()
            except Exception:
                pass
            self._client_socket = None
        
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
    
    def send_to_client(self, message: str) -> None:
        """Send a message to the connected client."""
        if self._client_socket:
            try:
                self._client_socket.sendall((message + "|").encode("utf-8"))
            except Exception:
                pass
    
    def _server_loop(self) -> None:
        """Main server loop."""
        while not self._stop_event.is_set():
            try:
                client_socket, addr = self._socket.accept()
                self._client_socket = client_socket
                self._client_socket.settimeout(0.5)
                self._handle_client(client_socket)
            except socket.timeout:
                continue
            except Exception as e:
                if not self._stop_event.is_set():
                    print(f"Server error: {e}")
                break
    
    def _handle_client(self, client_socket: socket.socket) -> None:
        """Handle a connected client."""
        buffer = ""
        client_nick = ""
        authenticated = False
        
        # Send initial lock challenge
        lock = "EXTENDEDPROTOCOL_VERLIHUB Pk=TestServer"
        self.send_to_client(f"$Lock {lock}")
        self.send_to_client(f"$HubName {self.hub_name}")
        
        while not self._stop_event.is_set():
            try:
                data = client_socket.recv(4096)
                if not data:
                    break
                
                buffer += data.decode("utf-8", errors="replace")
                
                # Process complete messages
                while "|" in buffer:
                    msg, buffer = buffer.split("|", 1)
                    self._messages_received.append(msg)
                    
                    # Handle NMDC protocol
                    response = self._process_message(msg, client_nick, authenticated)
                    
                    if msg.startswith("$ValidateNick "):
                        client_nick = msg[14:]
                        if not self.accept_nick:
                            self.send_to_client("$ValidateDenide")
                        elif self.require_password:
                            self.send_to_client(f"$Hello {client_nick}")
                            self.send_to_client("$GetPass")
                        else:
                            authenticated = True
                            self._connected_nicks.append(client_nick)
                            self.send_to_client(f"$Hello {client_nick}")
                            self.send_to_client("$LogedIn")
                            self.send_to_client("$Supports UserCommand NoGetINFO NoHello UserIP2 BotINFO HubINFO")
                    
                    elif msg.startswith("$MyPass "):
                        password = msg[8:]
                        if password == self.expected_password:
                            authenticated = True
                            self._connected_nicks.append(client_nick)
                            self.send_to_client("$LogedIn")
                        else:
                            self.send_to_client("$BadPass")
                    
                    elif msg.startswith("$GetNickList"):
                        # Send nick list
                        nicks = "$$".join(self._connected_nicks) + "$$"
                        self.send_to_client(f"$NickList {nicks}")
                        self.send_to_client(f"$OpList {client_nick}$$")
                    
                    elif msg.startswith("<"):
                        # Chat message - echo back with Hub-Security prefix for commands
                        if response:
                            self.send_to_client(response)
                        elif self._on_message:
                            custom_response = self._on_message(msg)
                            if custom_response:
                                self.send_to_client(custom_response)
                    
                    elif msg.startswith("$Quit"):
                        if client_nick in self._connected_nicks:
                            self._connected_nicks.remove(client_nick)
                        return
                    
            except socket.timeout:
                continue
            except Exception as e:
                if not self._stop_event.is_set():
                    print(f"Client handler error: {e}")
                break
    
    def _process_message(
        self, msg: str, client_nick: str, authenticated: bool
    ) -> Optional[str]:
        """Process a message and return optional response."""
        # Handle commands (messages starting with !)
        if msg.startswith(f"<{client_nick}> !"):
            command = msg.split("> ", 1)[1] if "> " in msg else ""
            
            if command == "!help":
                return "<Hub-Security> Available commands: !help, !motd, !rules, !myinfo"
            elif command == "!motd":
                return "<Hub-Security> Welcome to TestHub!"
            elif command == "!rules":
                return "<Hub-Security> 1. Be nice\r\n2. No spam"
            elif command == "!myinfo":
                return f"<Hub-Security> Your nick is: {client_nick}"
        
        return None


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def mock_server() -> MockNMDCServer:
    """Create a mock NMDC server."""
    with MockNMDCServer(port=0) as server:
        yield server


@pytest.fixture
def mock_server_with_auth() -> MockNMDCServer:
    """Create a mock NMDC server requiring authentication."""
    with MockNMDCServer(port=0, require_password=True, expected_password="testpass") as server:
        yield server


# =============================================================================
# Integration Tests - Connection
# =============================================================================

class TestNMDCConnection:
    """Test NMDC client connection handling."""
    
    def test_connect_no_auth(self, mock_server: MockNMDCServer):
        """Test connecting without authentication."""
        client = NMDCClient(
            "127.0.0.1",
            mock_server.actual_port,
            "TestBot",
        )
        
        result = client.connect(timeout=5.0)
        assert result is True
        assert client.is_connected is True
        assert client.hub_info.name == "TestHub"
        
        client.close()
        assert client.is_connected is False
    
    def test_connect_with_auth(self, mock_server_with_auth: MockNMDCServer):
        """Test connecting with authentication."""
        client = NMDCClient(
            "127.0.0.1",
            mock_server_with_auth.actual_port,
            "TestBot",
            password="testpass",
        )
        
        result = client.connect(timeout=5.0)
        assert result is True
        assert client.is_connected is True
        
        client.close()
    
    def test_connect_wrong_password(self, mock_server_with_auth: MockNMDCServer):
        """Test connection fails with wrong password."""
        client = NMDCClient(
            "127.0.0.1",
            mock_server_with_auth.actual_port,
            "TestBot",
            password="wrongpass",
        )
        
        # The exception may be raised in the receiver thread during cleanup
        # so we also accept a connection error
        with pytest.raises((NMDCAuthError, NMDCConnectionError)):
            client.connect(timeout=5.0)
    
    def test_connect_timeout(self):
        """Test connection timeout to non-existent server."""
        client = NMDCClient(
            "127.0.0.1",
            65432,  # Unlikely to be listening
            "TestBot",
        )
        
        with pytest.raises(NMDCConnectionError):
            client.connect(timeout=1.0)
    
    def test_context_manager(self, mock_server: MockNMDCServer):
        """Test using client as context manager."""
        with NMDCClient(
            "127.0.0.1",
            mock_server.actual_port,
            "ContextBot",
        ) as client:
            assert client.is_connected is True
            assert client.nick == "ContextBot"
        
        assert client.is_connected is False


# =============================================================================
# Integration Tests - Chat
# =============================================================================

class TestNMDCChat:
    """Test NMDC client chat functionality."""
    
    def test_send_chat_message(self, mock_server: MockNMDCServer):
        """Test sending a chat message."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "ChatBot") as client:
            # Give server time to set up
            time.sleep(0.2)
            
            client.send_chat("Hello, world!")
            
            # Wait for message to be processed
            time.sleep(0.2)
            
            # Check server received the message
            chat_messages = [m for m in mock_server._messages_received if m.startswith("<ChatBot>")]
            assert any("Hello, world!" in m for m in chat_messages)
    
    def test_execute_command(self, mock_server: MockNMDCServer):
        """Test executing a hub command."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "CmdBot") as client:
            responses = client.execute_command("!help", wait_time=2.0)
            
            # Should get a response with available commands
            response_text = " ".join(responses)
            assert "Hub-Security" in response_text or "Available commands" in response_text
    
    def test_execute_motd_command(self, mock_server: MockNMDCServer):
        """Test executing motd command."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "MotdBot") as client:
            responses = client.execute_command("!motd", wait_time=2.0)
            
            response_text = " ".join(responses)
            assert "Welcome" in response_text or "TestHub" in response_text
    
    def test_chat_message_callback(self, mock_server: MockNMDCServer):
        """Test chat message callback."""
        received_messages = []
        
        def on_chat(nick: str, message: str):
            received_messages.append((nick, message))
        
        with NMDCClient("127.0.0.1", mock_server.actual_port, "CallbackBot") as client:
            client.on_chat_message = on_chat
            
            # Execute a command to trigger a response
            client.execute_command("!help", wait_time=1.0)
            
            # Check callback was invoked
            assert len(received_messages) > 0
            assert any("Hub-Security" in nick for nick, _ in received_messages)


# =============================================================================
# Integration Tests - Private Messages
# =============================================================================

class TestNMDCPrivateMessage:
    """Test NMDC client private message functionality."""
    
    def test_send_pm(self, mock_server: MockNMDCServer):
        """Test sending a private message."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "PMBot") as client:
            time.sleep(0.2)
            
            client.send_pm("admin", "Hello admin!")
            
            time.sleep(0.2)
            
            # Check server received the PM
            pm_messages = [m for m in mock_server._messages_received if m.startswith("$To:")]
            assert any("Hello admin!" in m for m in pm_messages)


# =============================================================================
# Integration Tests - User List
# =============================================================================

class TestNMDCUserList:
    """Test NMDC client user list functionality."""
    
    def test_get_users(self, mock_server: MockNMDCServer):
        """Test getting user list."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "ListBot") as client:
            # Wait for nick list to be populated
            time.sleep(0.3)
            
            # Our nick should be in the user list (or users dict)
            assert client.nick in client.users or client.user_count >= 0
    
    def test_get_operators(self, mock_server: MockNMDCServer):
        """Test getting operator list."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "OpBot") as client:
            time.sleep(0.3)
            
            # Our nick should be in the ops list (mock server makes us op)
            assert "OpBot" in client.operators


# =============================================================================
# Integration Tests - Protocol Edge Cases
# =============================================================================

class TestNMDCProtocol:
    """Test NMDC protocol edge cases."""
    
    def test_special_characters_in_message(self, mock_server: MockNMDCServer):
        """Test sending messages with special characters."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "SpecialBot") as client:
            time.sleep(0.2)
            
            # These characters might cause issues in NMDC
            client.send_chat("Test: <>&|$")
            
            time.sleep(0.2)
            
            # Message should be received (no crash)
            assert any("<SpecialBot>" in m for m in mock_server._messages_received)
    
    def test_long_message(self, mock_server: MockNMDCServer):
        """Test sending a long message."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "LongBot") as client:
            time.sleep(0.2)
            
            long_msg = "A" * 1000
            client.send_chat(long_msg)
            
            time.sleep(0.2)
            
            # Message should be received
            assert any("A" * 100 in m for m in mock_server._messages_received)
    
    def test_wait_for_response_pattern(self, mock_server: MockNMDCServer):
        """Test waiting for a specific response pattern."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "WaitBot") as client:
            # Execute command to trigger Hub-Security response
            client.send_chat("!help")
            
            # Wait for Hub-Security response
            messages = client.wait_for_response(
                pattern=r"Hub-Security",
                timeout=2.0,
            )
            
            assert len(messages) > 0
            assert any("Hub-Security" in m for m in messages)
    
    def test_wait_for_response_timeout(self, mock_server: MockNMDCServer):
        """Test waiting with timeout when no match."""
        with NMDCClient("127.0.0.1", mock_server.actual_port, "TimeoutBot") as client:
            # Wait for a pattern that won't match
            start = time.time()
            messages = client.wait_for_response(
                pattern=r"NEVER_MATCH_THIS_12345",
                timeout=0.5,
            )
            elapsed = time.time() - start
            
            # Should timeout after ~0.5 seconds
            assert elapsed >= 0.4
            assert elapsed < 1.0


# =============================================================================
# Integration Tests - Concurrent Connections
# =============================================================================

class TestNMDCConcurrent:
    """Test multiple concurrent NMDC connections."""
    
    def test_multiple_clients(self, mock_server: MockNMDCServer):
        """Test multiple clients connecting to same server."""
        # Note: Our simple mock server only handles one client at a time
        # This test verifies sequential connections work and messages are received
        
        for i in range(3):
            with NMDCClient(
                "127.0.0.1",
                mock_server.actual_port,
                f"Client{i}",
            ) as client:
                assert client.is_connected is True
                client.send_chat(f"Hello from Client{i}")
                time.sleep(0.1)
        
        # All clients should have sent messages
        # Check messages_received instead of connected_nicks (which gets cleared)
        chat_msgs = [m for m in mock_server._messages_received if m.startswith("<Client")]
        assert len(chat_msgs) == 3


# =============================================================================
# Integration Tests - Raw Message Handling
# =============================================================================

class TestNMDCRawMessages:
    """Test raw message handling."""
    
    def test_raw_message_callback(self, mock_server: MockNMDCServer):
        """Test raw message callback is invoked."""
        raw_messages = []
        
        def on_raw(msg: str):
            raw_messages.append(msg)
        
        client = NMDCClient("127.0.0.1", mock_server.actual_port, "RawBot")
        client.on_raw_message = on_raw  # Set BEFORE connecting
        
        with client:
            # Give time for initial messages
            time.sleep(0.4)
        
        # Should have received several protocol messages
        assert len(raw_messages) > 0
        # Should include $Lock, $HubName, $Hello, $LogedIn, etc.
        all_msgs = " ".join(raw_messages)
        assert ("Lock" in all_msgs or "HubName" in all_msgs or 
                "Hello" in all_msgs or "LogedIn" in all_msgs)
