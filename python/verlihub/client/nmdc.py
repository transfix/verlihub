"""
NMDC Protocol Client for Verlihub

This is a first-class NMDC protocol client that can connect directly to
Verlihub and other NMDC-compatible DC++ hubs. Useful for:

- Integration testing
- Bot development
- Hub administration scripts
- Monitoring and automation

The client handles the complete NMDC handshake including:
- Lock/Key challenge-response
- Password authentication
- MyINFO exchange
- Command execution

Example:
    from verlihub.client import NMDCClient
    
    client = NMDCClient("localhost", 4111, "TestBot", "password")
    if client.connect():
        # Send messages
        client.send_chat("Hello everyone!")
        client.send_pm("admin", "Private message")
        
        # Execute hub commands
        responses = client.execute_command("!help")
        
        # Wait for specific patterns
        messages = client.wait_for_response(pattern=r"<Hub-Security>", timeout=5.0)
        
        client.close()
"""
from __future__ import annotations

import logging
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from queue import Empty, Queue
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class NMDCError(Exception):
    """Base exception for NMDC client errors."""
    pass


class NMDCConnectionError(NMDCError):
    """Connection-related errors."""
    pass


class NMDCAuthError(NMDCError):
    """Authentication errors."""
    pass


class ConnectionState(IntEnum):
    """NMDC connection state machine."""
    DISCONNECTED = 0
    CONNECTING = 1
    HANDSHAKE = 2
    AUTHENTICATING = 3
    CONNECTED = 4


@dataclass
class NMDCClientConfig:
    """Configuration for NMDC client."""
    host: str
    port: int
    nick: str
    password: str = ""
    share_size: int = 0
    slots: int = 1
    description: str = "Verlihub Python Client"
    email: str = ""
    connection_type: str = "Bot"
    timeout: float = 30.0
    debug: bool = False


@dataclass
class HubInfo:
    """Information about connected hub."""
    name: str = ""
    topic: str = ""
    user_count: int = 0
    share_total: int = 0
    connected_at: Optional[datetime] = None


@dataclass 
class UserInfo:
    """Information about a hub user."""
    nick: str
    description: str = ""
    connection: str = ""
    email: str = ""
    share: int = 0
    is_op: bool = False


class NMDCClient:
    """
    NMDC Protocol client for direct hub connections.
    
    This client implements the NMDC (Neo-Modus DC) protocol used by
    Verlihub and compatible DC++ hubs. It handles authentication,
    chat messages, and command execution.
    
    Thread Safety:
        The client is thread-safe and uses a background receiver thread.
        Message callbacks are invoked from the receiver thread.
    
    Args:
        host: Hub hostname or IP address
        port: Hub port number (default 4111)
        nick: Client nickname
        password: Authentication password (empty for unregistered)
        config: Optional NMDCClientConfig for advanced settings
    
    Example:
        # Simple usage
        with NMDCClient("localhost", 4111, "MyBot", "secret") as client:
            client.send_chat("Hello!")
        
        # With message handler
        def on_chat(nick: str, message: str):
            print(f"<{nick}> {message}")
        
        client = NMDCClient("localhost", 4111, "MyBot")
        client.on_chat_message = on_chat
        client.connect()
    """
    
    def __init__(
        self,
        host: str,
        port: int = 4111,
        nick: str = "VerlihubClient",
        password: str = "",
        *,
        config: Optional[NMDCClientConfig] = None,
    ):
        if config:
            self._config = config
        else:
            self._config = NMDCClientConfig(
                host=host,
                port=port,
                nick=nick,
                password=password,
            )
        
        # Connection state
        self._socket: Optional[socket.socket] = None
        self._state = ConnectionState.DISCONNECTED
        self._buffer = ""
        self._lock = threading.RLock()
        
        # Hub information
        self._hub_info = HubInfo()
        self._users: dict[str, UserInfo] = {}
        self._ops: set[str] = set()
        
        # Protocol state
        self._lock_key = ""
        self._supports: set[str] = set()
        
        # Receiver thread
        self._receiver_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._message_queue: Queue[str] = Queue()
        
        # Callbacks
        self.on_chat_message: Optional[Callable[[str, str], None]] = None
        self.on_private_message: Optional[Callable[[str, str, str], None]] = None
        self.on_user_join: Optional[Callable[[str], None]] = None
        self.on_user_quit: Optional[Callable[[str], None]] = None
        self.on_hub_message: Optional[Callable[[str], None]] = None
        self.on_raw_message: Optional[Callable[[str], None]] = None
    
    def __enter__(self) -> "NMDCClient":
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
    
    # =========================================================================
    # Properties
    # =========================================================================
    
    @property
    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        return self._state == ConnectionState.CONNECTED
    
    @property
    def hub_info(self) -> HubInfo:
        """Get hub information."""
        return self._hub_info
    
    @property
    def nick(self) -> str:
        """Get configured nickname."""
        return self._config.nick
    
    @property
    def users(self) -> dict[str, UserInfo]:
        """Get online users (nick -> UserInfo)."""
        with self._lock:
            return dict(self._users)
    
    @property
    def operators(self) -> set[str]:
        """Get operator nicknames."""
        with self._lock:
            return set(self._ops)
    
    @property
    def user_count(self) -> int:
        """Get number of online users."""
        with self._lock:
            return len(self._users)
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def connect(self, timeout: Optional[float] = None) -> bool:
        """
        Connect to the hub and complete handshake.
        
        Args:
            timeout: Connection timeout in seconds (uses config default if None)
            
        Returns:
            True if connected and authenticated successfully
            
        Raises:
            NMDCConnectionError: If connection fails
            NMDCAuthError: If authentication fails
        """
        timeout = timeout or self._config.timeout
        
        with self._lock:
            if self._state != ConnectionState.DISCONNECTED:
                raise NMDCConnectionError("Already connected or connecting")
            
            self._state = ConnectionState.CONNECTING
        
        try:
            # Create socket
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(timeout)
            
            logger.debug(f"Connecting to {self._config.host}:{self._config.port}")
            self._socket.connect((self._config.host, self._config.port))
            
            with self._lock:
                self._state = ConnectionState.HANDSHAKE
            
            # Start receiver thread
            self._stop_event.clear()
            self._receiver_thread = threading.Thread(
                target=self._receiver_loop,
                daemon=True,
            )
            self._receiver_thread.start()
            
            # Wait for handshake to complete
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self._state == ConnectionState.CONNECTED:
                    self._hub_info.connected_at = datetime.now(timezone.utc)
                    logger.info(f"Connected to {self._hub_info.name}")
                    return True
                elif self._state == ConnectionState.DISCONNECTED:
                    raise NMDCConnectionError("Connection failed during handshake")
                time.sleep(0.1)
            
            raise NMDCConnectionError("Handshake timeout")
            
        except socket.timeout:
            self._cleanup()
            raise NMDCConnectionError(f"Connection timeout to {self._config.host}:{self._config.port}")
        except socket.error as e:
            self._cleanup()
            raise NMDCConnectionError(f"Socket error: {e}")
        except NMDCError:
            self._cleanup()
            raise
    
    def close(self) -> None:
        """Close the connection gracefully."""
        with self._lock:
            if self._state == ConnectionState.DISCONNECTED:
                return
        
        try:
            self._send("$Quit")
        except Exception:
            pass
        
        self._cleanup()
        logger.info("Disconnected from hub")
    
    def _cleanup(self) -> None:
        """Clean up connection resources."""
        self._stop_event.set()
        
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        
        with self._lock:
            self._state = ConnectionState.DISCONNECTED
            self._buffer = ""
            self._users.clear()
            self._ops.clear()
        
        # Wait for receiver thread (but not if called from the receiver thread itself)
        current_thread = threading.current_thread()
        if (self._receiver_thread and 
            self._receiver_thread.is_alive() and 
            self._receiver_thread != current_thread):
            self._receiver_thread.join(timeout=2.0)
        self._receiver_thread = None
    
    # =========================================================================
    # Message Sending
    # =========================================================================
    
    def send_chat(self, message: str) -> None:
        """
        Send a message to main chat.
        
        Args:
            message: Message text to send
        """
        if not self.is_connected:
            raise NMDCError("Not connected")
        
        # Format: <nick> message|
        self._send(f"<{self._config.nick}> {message}")
    
    def send_pm(self, to_nick: str, message: str) -> None:
        """
        Send a private message.
        
        Args:
            to_nick: Recipient nickname
            message: Message text
        """
        if not self.is_connected:
            raise NMDCError("Not connected")
        
        # Format: $To: nick From: from $<from> message|
        self._send(f"$To: {to_nick} From: {self._config.nick} $<{self._config.nick}> {message}")
    
    def execute_command(self, command: str, wait_time: float = 2.0) -> list[str]:
        """
        Execute a hub command and wait for responses.
        
        Args:
            command: Command to execute (e.g., "!help", "+reguser nick pass")
            wait_time: Time to wait for responses in seconds
            
        Returns:
            List of response messages
        """
        if not self.is_connected:
            raise NMDCError("Not connected")
        
        # Clear message queue
        while not self._message_queue.empty():
            try:
                self._message_queue.get_nowait()
            except Empty:
                break
        
        # Send command as chat
        self.send_chat(command)
        
        # Collect responses
        responses = []
        deadline = time.time() + wait_time
        
        while time.time() < deadline:
            try:
                msg = self._message_queue.get(timeout=0.1)
                responses.append(msg)
            except Empty:
                continue
        
        return responses
    
    def wait_for_response(
        self,
        pattern: Optional[str] = None,
        timeout: float = 10.0,
    ) -> list[str]:
        """
        Wait for messages, optionally matching a pattern.
        
        Args:
            pattern: Regex pattern to match (returns when matched)
            timeout: Maximum wait time in seconds
            
        Returns:
            List of messages received (up to pattern match or timeout)
        """
        messages = []
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                msg = self._message_queue.get(timeout=0.1)
                messages.append(msg)
                
                if pattern and re.search(pattern, msg):
                    return messages
            except Empty:
                continue
        
        return messages
    
    def _send(self, msg: str) -> None:
        """Send raw NMDC message."""
        if not self._socket:
            raise NMDCError("Not connected")
        
        if self._config.debug:
            logger.debug(f"-> {msg}")
        
        self._socket.sendall((msg + "|").encode("utf-8", errors="replace"))
    
    # =========================================================================
    # Protocol Handling
    # =========================================================================
    
    def _receiver_loop(self) -> None:
        """Background thread for receiving messages."""
        while not self._stop_event.is_set():
            try:
                if not self._socket:
                    break
                
                self._socket.settimeout(0.5)
                data = self._socket.recv(4096)
                
                if not data:
                    logger.warning("Connection closed by hub")
                    self._cleanup()
                    break
                
                self._buffer += data.decode("utf-8", errors="replace")
                
                # Process complete messages
                while "|" in self._buffer:
                    msg, self._buffer = self._buffer.split("|", 1)
                    self._handle_message(msg)
                    
            except socket.timeout:
                continue
            except socket.error as e:
                if not self._stop_event.is_set():
                    logger.error(f"Socket error: {e}")
                    self._cleanup()
                break
    
    def _handle_message(self, msg: str) -> None:
        """Handle incoming NMDC message."""
        if self._config.debug:
            logger.debug(f"<- {msg}")
        
        # Invoke raw callback
        if self.on_raw_message:
            self.on_raw_message(msg)
        
        # Queue for wait_for_response
        self._message_queue.put(msg)
        
        # Protocol handling
        if msg.startswith("$Lock "):
            self._handle_lock(msg)
        elif msg.startswith("$HubName "):
            self._hub_info.name = msg[9:]
        elif msg.startswith("$Hello "):
            self._handle_hello(msg[7:])
        elif msg.startswith("$GetPass"):
            self._handle_getpass()
        elif msg.startswith("$BadPass"):
            self._handle_badpass()
        elif msg.startswith("$LogedIn"):
            self._handle_loggedin()
        elif msg.startswith("$ValidateDenide"):
            self._handle_validate_denied()
        elif msg.startswith("$Supports "):
            self._handle_supports(msg[10:])
        elif msg.startswith("$NickList "):
            self._handle_nicklist(msg[10:])
        elif msg.startswith("$OpList "):
            self._handle_oplist(msg[8:])
        elif msg.startswith("$MyINFO "):
            self._handle_myinfo(msg[8:])
        elif msg.startswith("$Quit "):
            self._handle_quit(msg[6:])
        elif msg.startswith("$HubTopic "):
            self._hub_info.topic = msg[10:]
        elif msg.startswith("<"):
            self._handle_chat(msg)
        elif msg.startswith("$To: "):
            self._handle_pm(msg)
    
    def _handle_lock(self, msg: str) -> None:
        """Handle $Lock challenge."""
        # Extract lock data
        lock_data = msg[6:].split(" ", 1)[0]
        self._lock_key = self._calculate_key(lock_data)
        
        # Send handshake response
        supports = "$Supports UserCommand NoGetINFO NoHello UserIP2 BotINFO HubINFO ZPipe0"
        self._send(supports)
        self._send(f"$Key {self._lock_key}")
        self._send(f"$ValidateNick {self._config.nick}")
    
    def _handle_hello(self, nick: str) -> None:
        """Handle $Hello response."""
        if nick == self._config.nick:
            # Our nick was accepted - authentication may follow
            pass
    
    def _handle_getpass(self) -> None:
        """Handle password request."""
        with self._lock:
            self._state = ConnectionState.AUTHENTICATING
        
        if self._config.password:
            self._send(f"$MyPass {self._config.password}")
        else:
            logger.error("Password required but not provided")
            self._cleanup()
            raise NMDCAuthError("Password required")
    
    def _handle_badpass(self) -> None:
        """Handle bad password response."""
        logger.error("Invalid password")
        self._cleanup()
        raise NMDCAuthError("Invalid password")
    
    def _handle_loggedin(self) -> None:
        """Handle successful login."""
        logger.debug("Logged in as operator")
        self._send_myinfo()
        self._send("$GetNickList")
        
        with self._lock:
            self._state = ConnectionState.CONNECTED
    
    def _handle_validate_denied(self) -> None:
        """Handle nick validation denied."""
        logger.error(f"Nick '{self._config.nick}' denied")
        self._cleanup()
        raise NMDCAuthError(f"Nick '{self._config.nick}' denied by hub")
    
    def _handle_supports(self, features: str) -> None:
        """Handle $Supports response."""
        self._supports = set(features.split())
    
    def _handle_nicklist(self, nicks: str) -> None:
        """Handle $NickList."""
        with self._lock:
            for nick in nicks.split("$$"):
                nick = nick.strip()
                if nick and nick not in self._users:
                    self._users[nick] = UserInfo(nick=nick)
    
    def _handle_oplist(self, ops: str) -> None:
        """Handle $OpList."""
        with self._lock:
            for nick in ops.split("$$"):
                nick = nick.strip()
                if nick:
                    self._ops.add(nick)
                    if nick in self._users:
                        self._users[nick].is_op = True
    
    def _handle_myinfo(self, info: str) -> None:
        """Handle $MyINFO from another user."""
        # Format: $ALL nick desc<tag>$ $conn\x01$email$share$
        match = re.match(r"\$ALL (\S+) (.*)\$ \$(.+)\$(.+)\$(\d+)\$", info)
        if match:
            nick, desc, conn, email, share = match.groups()
            with self._lock:
                user = UserInfo(
                    nick=nick,
                    description=desc.split("<")[0].strip(),
                    connection=conn.replace("\x01", ""),
                    email=email,
                    share=int(share),
                    is_op=nick in self._ops,
                )
                self._users[nick] = user
            
            if self.on_user_join:
                self.on_user_join(nick)
    
    def _handle_quit(self, nick: str) -> None:
        """Handle $Quit notification."""
        with self._lock:
            self._users.pop(nick, None)
            self._ops.discard(nick)
        
        if self.on_user_quit:
            self.on_user_quit(nick)
    
    def _handle_chat(self, msg: str) -> None:
        """Handle chat message."""
        # Format: <nick> message
        match = re.match(r"<([^>]+)> (.+)", msg)
        if match:
            nick, message = match.groups()
            if self.on_chat_message:
                self.on_chat_message(nick, message)
        elif msg.startswith("<"):
            # Hub message
            if self.on_hub_message:
                self.on_hub_message(msg)
    
    def _handle_pm(self, msg: str) -> None:
        """Handle private message."""
        # Format: $To: to From: from $<from> message
        match = re.match(r"\$To: (\S+) From: (\S+) \$<[^>]+> (.+)", msg)
        if match:
            to_nick, from_nick, message = match.groups()
            if self.on_private_message:
                self.on_private_message(from_nick, to_nick, message)
    
    def _send_myinfo(self) -> None:
        """Send MyINFO to hub."""
        desc = self._config.description
        conn = f"{self._config.connection_type}\x01"
        email = self._config.email
        share = str(self._config.share_size)
        
        # Build tag
        tag = f"<VH-Py V:1.0,M:A,H:1/0/0,S:{self._config.slots}>"
        
        myinfo = f"$MyINFO $ALL {self._config.nick} {desc}{tag}$ ${conn}${email}${share}$"
        self._send(myinfo)
    
    @staticmethod
    def _calculate_key(lock: str) -> str:
        """
        Calculate NMDC key from lock challenge.
        
        This implements the standard NMDC lock-to-key algorithm.
        """
        key = []
        for i in range(len(lock)):
            if i == 0:
                key.append(ord(lock[0]) ^ ord(lock[-1]) ^ ord(lock[-2]) ^ 5)
            else:
                key.append(ord(lock[i]) ^ ord(lock[i - 1]))
        
        # Escape special characters
        result = ""
        for b in key:
            b = b & 0xFF
            if b in (0, 5, 36, 96, 124, 126):
                result += f"/%DCN{b:03d}%/"
            else:
                result += chr(b)
        
        return result
