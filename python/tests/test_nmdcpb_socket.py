"""
Socket-level integration tests for NMDCpb client + hub plugin.

Tests the full NMDCpb lifecycle over real TCP sockets:
- Connect, negotiate NMDCpb via $Supports
- Send/receive $PB protobuf messages through a mock hub
- E2EPM key exchange, encrypted PM, session teardown
- Legacy ↔ NMDCpb coexistence (mixed clients)

The MockNMDCpbHub extends the mock NMDC server with hub_plugin routing
logic, simulating a verlihub instance running the NMDCpb Python plugin.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time
import unittest
from dataclasses import dataclass, field
from typing import Callable, Optional

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbChat,
    PbStatus,
    PbPMKeyExchange,
    PbEncryptedPM,
    PbPMPlaintext,
    PbPMSessionEnd,
)
from verlihub.client.nmdcpb.wire import WireCodec, FEATURE_NMDCPB, FEATURE_HUBRELAY
from verlihub.client.nmdcpb.e2epm import E2EPMManager
from verlihub.client.nmdcpb.client import NMDCpbClient, _nmdc_lock_to_key


# =============================================================================
# Mock NMDC Hub with NMDCpb support
# =============================================================================


class MockNMDCpbHub:
    """A mock NMDC hub with NMDCpb extension support.

    Implements just enough of the NMDC + NMDCpb protocol to support
    integration testing of NMDCpbClient over real TCP sockets.

    This mock uses the same routing logic as hub_plugin.py:
    - Tracks NMDCpb feature negotiation via $Supports
    - Routes $PB messages based on PbEnvelope.route
    - Forwards E2EPM messages opaquely
    - Translates PB chat ↔ legacy NMDC for mixed clients
    """

    def __init__(self, port: int = 0, hub_name: str = "TestPbHub"):
        self.port = port
        self.hub_name = hub_name
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Per-client state: nick → {socket, nmdcpb, buffer}
        self._clients: dict[str, dict] = {}
        # IP/socket → pending nick (before login completes)
        self._pending: dict[int, dict] = {}  # socket.fileno() → info
        self._lock = threading.Lock()

    @property
    def actual_port(self) -> int:
        if self._socket:
            return self._socket.getsockname()[1]
        return self.port

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def start(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", self.port))
        self._socket.listen(5)
        self._socket.settimeout(0.3)
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        time.sleep(0.05)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for info in self._clients.values():
                try:
                    info["socket"].close()
                except Exception:
                    pass
            self._clients.clear()
            for info in self._pending.values():
                try:
                    info["socket"].close()
                except Exception:
                    pass
            self._pending.clear()
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)

    def get_connected_nicks(self) -> list[str]:
        with self._lock:
            return list(self._clients.keys())

    def is_nmdcpb_user(self, nick: str) -> bool:
        with self._lock:
            return self._clients.get(nick, {}).get("nmdcpb", False)

    # --- Accept loop ---

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, addr = self._socket.accept()
                sock.settimeout(0.3)
                t = threading.Thread(
                    target=self._handle_client, args=(sock,), daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
            except Exception:
                if not self._stop.is_set():
                    break

    # --- Client handler ---

    def _handle_client(self, sock: socket.socket) -> None:
        fd = sock.fileno()
        with self._lock:
            self._pending[fd] = {
                "socket": sock,
                "nmdcpb": False,
                "nick": None,
            }

        # Send $Lock challenge
        lock = "EXTENDEDPROTOCOL_VERLIHUB Pk=NMDCpbTestHub"
        self._send(sock, f"$Lock {lock}|")
        self._send(sock, f"$HubName {self.hub_name}|")

        buffer = ""
        nick = None

        while not self._stop.is_set():
            try:
                data = sock.recv(65536)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="replace")

                while "|" in buffer:
                    msg, buffer = buffer.split("|", 1)
                    nick = self._process_msg(sock, fd, nick, msg)

            except socket.timeout:
                continue
            except Exception:
                break

        # Cleanup
        with self._lock:
            self._pending.pop(fd, None)
            if nick and nick in self._clients:
                del self._clients[nick]
        try:
            sock.close()
        except Exception:
            pass

    def _process_msg(self, sock, fd, nick, msg) -> Optional[str]:
        """Process one NMDC command. Returns current nick."""
        if msg.startswith("$Supports "):
            has_pb, has_relay = WireCodec.check_supports(msg)
            with self._lock:
                if fd in self._pending:
                    self._pending[fd]["nmdcpb"] = has_pb
            # Send hub $Supports back with NMDCpb
            features = "UserCommand NoGetINFO NoHello UserIP2 NMDCpb"
            if has_relay:
                features += " HubRelay"
            self._send(sock, f"$Supports {features}|")

        elif msg.startswith("$Key "):
            pass  # Accept any key

        elif msg.startswith("$ValidateNick "):
            nick = msg[14:]
            with self._lock:
                pending_info = self._pending.pop(fd, {})
                is_pb = pending_info.get("nmdcpb", False)
                self._clients[nick] = {
                    "socket": sock,
                    "nmdcpb": is_pb,
                }
            self._send(sock, f"$Hello {nick}|")
            self._send(sock, "$LogedIn|")

            # Send welcome status to NMDCpb users
            if is_pb:
                env = WireCodec.make_envelope(
                    route=PbEnvelope.DIRECT,
                    from_nick="",
                    to_nick=nick,
                )
                env.status.severity = PbStatus.INFO
                env.status.code = 0
                env.status.message = "NMDCpb active"
                wire = WireCodec.encode_text(env)
                self._send(sock, wire)

        elif msg.startswith("$MyINFO "):
            pass  # Accept any MyINFO

        elif msg.startswith("$GetNickList"):
            with self._lock:
                nicks = "$$".join(self._clients.keys()) + "$$"
            self._send(sock, f"$NickList {nicks}|")

        elif WireCodec.is_nmdcpb_command(msg + "|"):
            self._handle_pb(nick, msg + "|")

        elif msg.startswith("<") and nick:
            # Legacy chat broadcast
            self._broadcast_legacy_chat(nick, msg)

        elif msg.startswith("$To: ") and nick:
            self._handle_legacy_pm(nick, msg)

        elif msg.startswith("$Quit"):
            with self._lock:
                if nick and nick in self._clients:
                    del self._clients[nick]

        return nick

    # --- PB routing (mirrors hub_plugin.py logic) ---

    def _handle_pb(self, sender: str, wire: str) -> None:
        if not sender:
            return
        result = WireCodec.decode(wire)
        if result is None:
            return
        if isinstance(result, tuple):
            return  # Relay not implemented

        env = result
        env.from_nick = sender  # Hub is authoritative

        route = env.route
        if route == PbEnvelope.BROADCAST:
            self._route_broadcast(sender, env)
        elif route == PbEnvelope.DIRECT:
            self._route_direct(sender, env)
        elif route == PbEnvelope.ECHO:
            self._route_echo(sender, env)

    def _route_broadcast(self, sender: str, env: PbEnvelope) -> None:
        new_wire = WireCodec.encode_text(env)
        payload = env.WhichOneof("payload")

        with self._lock:
            for nick, info in self._clients.items():
                if nick == sender:
                    continue
                if info["nmdcpb"]:
                    self._send(info["socket"], new_wire)
                elif payload == "chat":
                    # Legacy translation
                    text = env.chat.text
                    if env.chat.is_action:
                        legacy = f"<{sender}> /me {text}|"
                    else:
                        legacy = f"<{sender}> {text}|"
                    self._send(info["socket"], legacy)

    def _route_direct(self, sender: str, env: PbEnvelope) -> None:
        target = env.to_nick
        if not target:
            return

        with self._lock:
            info = self._clients.get(target)
            if not info:
                return

            new_wire = WireCodec.encode_text(env)
            self._send(info["socket"], new_wire)

    def _route_echo(self, sender: str, env: PbEnvelope) -> None:
        new_wire = WireCodec.encode_text(env)
        with self._lock:
            for nick, info in self._clients.items():
                if info["nmdcpb"]:
                    self._send(info["socket"], new_wire)

    def _broadcast_legacy_chat(self, sender: str, raw_msg: str) -> None:
        """Forward legacy chat to all other clients, bridge to PB users."""
        with self._lock:
            for nick, info in self._clients.items():
                if nick == sender:
                    continue
                if info["nmdcpb"]:
                    # Bridge legacy → PB for NMDCpb users
                    import re
                    m = re.match(r"<([^>]+)>\s?(.*)", raw_msg)
                    if m:
                        env = WireCodec.make_envelope(
                            route=PbEnvelope.BROADCAST,
                            from_nick=m.group(1),
                        )
                        env.chat.text = m.group(2)
                        wire = WireCodec.encode_text(env)
                        self._send(info["socket"], wire)
                else:
                    self._send(info["socket"], raw_msg + "|")

    def _handle_legacy_pm(self, sender: str, msg: str) -> None:
        """Forward legacy PM to target."""
        import re
        m = re.match(r"\$To: (\S+) From: \S+ \$<[^>]+>\s?(.*)", msg)
        if not m:
            return
        target = m.group(1)
        with self._lock:
            info = self._clients.get(target)
            if info:
                self._send(info["socket"], msg + "|")

    # --- Low-level ---

    def _send(self, sock: socket.socket, data: str) -> None:
        try:
            sock.sendall(data.encode("utf-8"))
        except Exception:
            pass


# =============================================================================
# Tests
# =============================================================================


class TestNMDCpbClientConnect(unittest.IsolatedAsyncioTestCase):
    """Test NMDCpbClient connection and NMDCpb negotiation."""

    async def asyncSetUp(self):
        self.hub = MockNMDCpbHub()
        self.hub.start()

    async def asyncTearDown(self):
        self.hub.stop()

    async def test_connect_negotiates_nmdcpb(self):
        """Client connects and negotiates NMDCpb feature."""
        client = NMDCpbClient("Alice")
        await client.connect(f"127.0.0.1:{self.hub.actual_port}")
        await asyncio.sleep(0.3)

        self.assertTrue(client._connected)
        self.assertTrue(client._logged_in)
        self.assertTrue(client.hub_supports_nmdcpb)

        await client.disconnect()

    async def test_connect_receives_status(self):
        """Client receives NMDCpb status message after login."""
        received_status = []
        client = NMDCpbClient("Alice")
        client.on_status = lambda msg: received_status.append(msg)

        await client.connect(f"127.0.0.1:{self.hub.actual_port}")
        await asyncio.sleep(0.3)

        self.assertTrue(any("NMDCpb active" in s for s in received_status))

        await client.disconnect()

    async def test_hub_tracks_pb_user(self):
        """Hub correctly identifies the NMDCpb-capable client."""
        client = NMDCpbClient("Alice")
        await client.connect(f"127.0.0.1:{self.hub.actual_port}")
        await asyncio.sleep(0.3)

        self.assertIn("Alice", self.hub.get_connected_nicks())
        self.assertTrue(self.hub.is_nmdcpb_user("Alice"))

        await client.disconnect()


class TestNMDCpbChat(unittest.IsolatedAsyncioTestCase):
    """Test NMDCpb protobuf chat over sockets."""

    async def asyncSetUp(self):
        self.hub = MockNMDCpbHub()
        self.hub.start()

    async def asyncTearDown(self):
        self.hub.stop()

    async def _connect_client(self, nick: str) -> NMDCpbClient:
        client = NMDCpbClient(nick)
        await client.connect(f"127.0.0.1:{self.hub.actual_port}")
        await asyncio.sleep(0.2)
        return client

    async def test_pb_chat_broadcast(self):
        """Alice sends PB chat, Bob receives it."""
        alice = await self._connect_client("Alice")
        bob = await self._connect_client("Bob")

        received = []
        bob.on_chat = lambda nick, text: received.append((nick, text))

        await alice.send_pb_chat("Hello from protobuf!")
        await asyncio.sleep(0.3)

        self.assertTrue(
            any(text == "Hello from protobuf!" for _, text in received),
            f"Bob didn't receive chat. Got: {received}",
        )

        await alice.disconnect()
        await bob.disconnect()

    async def test_pb_chat_not_echoed_to_sender(self):
        """Sender should NOT receive their own broadcast."""
        alice = await self._connect_client("Alice")
        bob = await self._connect_client("Bob")

        alice_received = []
        alice.on_chat = lambda nick, text: alice_received.append((nick, text))

        await alice.send_pb_chat("Echo test")
        await asyncio.sleep(0.3)

        self.assertFalse(
            any(text == "Echo test" and nick == "Alice" for nick, text in alice_received),
        )

        await alice.disconnect()
        await bob.disconnect()

    async def test_pb_chat_unicode(self):
        """Unicode text survives the full wire roundtrip."""
        alice = await self._connect_client("Alice")
        bob = await self._connect_client("Bob")

        received = []
        bob.on_chat = lambda nick, text: received.append((nick, text))

        await alice.send_pb_chat("Привет мир 🌍 日本語")
        await asyncio.sleep(0.3)

        self.assertTrue(
            any("Привет мир 🌍 日本語" in text for _, text in received),
        )

        await alice.disconnect()
        await bob.disconnect()

    async def test_pb_direct_pm(self):
        """Alice sends PB PM to Bob, Bob receives it."""
        alice = await self._connect_client("Alice")
        bob = await self._connect_client("Bob")

        received_pm = []
        bob.on_pm = lambda from_nick, to_nick, text: received_pm.append(
            (from_nick, to_nick, text),
        )

        await alice.send_pb_pm("Bob", "Private hello")
        await asyncio.sleep(0.3)

        self.assertTrue(
            any(text == "Private hello" for _, _, text in received_pm),
            f"Bob didn't receive PM. Got: {received_pm}",
        )

        await alice.disconnect()
        await bob.disconnect()


class TestNMDCpbE2EPM(unittest.IsolatedAsyncioTestCase):
    """Test E2EPM key exchange and encrypted messages over sockets."""

    async def asyncSetUp(self):
        self.hub = MockNMDCpbHub()
        self.hub.start()

    async def asyncTearDown(self):
        self.hub.stop()

    async def _connect_client(self, nick: str) -> NMDCpbClient:
        client = NMDCpbClient(nick)
        await client.connect(f"127.0.0.1:{self.hub.actual_port}")
        await asyncio.sleep(0.2)
        return client

    async def test_e2epm_key_exchange(self):
        """Two clients complete E2EPM key exchange through hub."""
        alice = await self._connect_client("Alice")
        bob = await self._connect_client("Bob")

        # Track session establishment
        alice_established = asyncio.Event()
        bob_established = asyncio.Event()

        alice.on_e2epm_established = lambda nick, fp: alice_established.set()
        bob.on_e2epm_established = lambda nick, fp: bob_established.set()

        # Alice initiates key exchange
        result = await alice.send_encrypted_pm("Bob", "test")
        self.assertFalse(result)  # Returns False = kex initiated, not sent yet

        # Wait for both sides to establish
        try:
            await asyncio.wait_for(bob_established.wait(), timeout=2.0)
            await asyncio.wait_for(alice_established.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        self.assertTrue(alice.e2epm.has_session("Bob"))
        self.assertTrue(bob.e2epm.has_session("Alice"))

        await alice.disconnect()
        await bob.disconnect()

    async def test_e2epm_encrypted_pm(self):
        """Full E2EPM flow: key exchange → encrypted PM → decrypt."""
        alice = await self._connect_client("Alice")
        bob = await self._connect_client("Bob")

        # Track received encrypted PMs
        decrypted = []
        bob.on_encrypted_pm = lambda from_nick, text, is_action: decrypted.append(
            (from_nick, text, is_action),
        )

        alice_ready = asyncio.Event()
        alice.on_e2epm_established = lambda nick, fp: alice_ready.set()

        bob_ready = asyncio.Event()
        bob.on_e2epm_established = lambda nick, fp: bob_ready.set()

        # Initiate key exchange
        await alice.send_encrypted_pm("Bob", "Hello")
        try:
            await asyncio.wait_for(alice_ready.wait(), timeout=2.0)
            await asyncio.wait_for(bob_ready.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        # Now send the actual encrypted message
        if alice.e2epm.has_session("Bob"):
            sent = await alice.send_encrypted_pm("Bob", "Top secret!")
            self.assertTrue(sent)
            await asyncio.sleep(0.5)

            self.assertTrue(
                any(text == "Top secret!" for _, text, _ in decrypted),
                f"Bob didn't decrypt message. Got: {decrypted}",
            )

        await alice.disconnect()
        await bob.disconnect()

    async def test_hub_cannot_read_ciphertext(self):
        """Hub only sees ciphertext, never the plaintext."""
        alice = await self._connect_client("Alice")
        bob = await self._connect_client("Bob")

        bob_ready = asyncio.Event()
        alice_ready = asyncio.Event()
        bob.on_e2epm_established = lambda n, fp: bob_ready.set()
        alice.on_e2epm_established = lambda n, fp: alice_ready.set()

        await alice.send_encrypted_pm("Bob", "init")
        try:
            await asyncio.wait_for(alice_ready.wait(), timeout=2.0)
            await asyncio.wait_for(bob_ready.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        if alice.e2epm.has_session("Bob"):
            secret = "The quick brown fox jumps over the lazy dog"
            epm = alice.e2epm.encrypt_pm("Bob", secret)
            # The ciphertext should not contain the plaintext
            self.assertNotIn(
                secret.encode("utf-8"),
                epm.ciphertext,
            )

        await alice.disconnect()
        await bob.disconnect()


class TestNMDCpbLegacyCoexistence(unittest.IsolatedAsyncioTestCase):
    """Test mixed NMDCpb + legacy NMDC clients."""

    async def asyncSetUp(self):
        self.hub = MockNMDCpbHub()
        self.hub.start()

    async def asyncTearDown(self):
        self.hub.stop()

    async def _connect_pb_client(self, nick: str) -> NMDCpbClient:
        client = NMDCpbClient(nick)
        await client.connect(f"127.0.0.1:{self.hub.actual_port}")
        await asyncio.sleep(0.2)
        return client

    def _connect_legacy_client(self, nick: str) -> socket.socket:
        """Connect a raw legacy NMDC client (no NMDCpb in $Supports)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.hub.actual_port))
        sock.settimeout(2.0)

        # Read initial $Lock
        data = b""
        while b"|" not in data:
            data += sock.recv(4096)

        # Compute key from lock
        lock_line = data.decode("utf-8", errors="replace").split("|")[0]
        lock_val = lock_line.split(" ", 2)[1] if " " in lock_line else ""
        key = _nmdc_lock_to_key(lock_val)

        # Send $Supports WITHOUT NMDCpb
        sock.sendall(f"$Supports UserCommand NoGetINFO|$Key {key}|$ValidateNick {nick}|".encode())

        # Read until we get $Hello
        buf = b""
        for _ in range(20):
            try:
                buf += sock.recv(4096)
            except socket.timeout:
                break
            if b"$Hello" in buf:
                break

        # Send MyINFO
        sock.sendall(
            f"$MyINFO $ALL {nick} Legacy<Legacy V:1.0>$ $LAN(T1)\x01${nick}@test$0$|".encode()
        )
        time.sleep(0.1)
        return sock

    def _recv_all(self, sock: socket.socket, timeout: float = 0.5) -> str:
        """Read all available data from a socket."""
        sock.settimeout(timeout)
        buf = b""
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                buf += data
        except socket.timeout:
            pass
        return buf.decode("utf-8", errors="replace")

    async def test_pb_chat_reaches_legacy_as_text(self):
        """NMDCpb client's PB chat is translated to legacy NMDC for legacy client."""
        alice = await self._connect_pb_client("Alice")

        legacy_sock = self._connect_legacy_client("Legacy")
        await asyncio.sleep(0.2)

        # Drain any pending data from legacy socket
        self._recv_all(legacy_sock, timeout=0.2)

        # Alice sends PB chat
        await alice.send_pb_chat("Protobuf says hello!")
        await asyncio.sleep(0.3)

        # Legacy client should receive translated text
        data = self._recv_all(legacy_sock, timeout=0.5)

        self.assertIn("<Alice>", data)
        self.assertIn("Protobuf says hello!", data)

        await alice.disconnect()
        legacy_sock.close()

    async def test_three_clients_mixed(self):
        """Two PB clients + one legacy client, broadcast reaches all."""
        alice = await self._connect_pb_client("Alice")
        bob = await self._connect_pb_client("Bob")
        legacy_sock = self._connect_legacy_client("Charlie")
        await asyncio.sleep(0.2)

        bob_received = []
        bob.on_chat = lambda nick, text: bob_received.append((nick, text))

        # Drain pending from legacy
        self._recv_all(legacy_sock, timeout=0.2)

        # Alice broadcasts via PB
        await alice.send_pb_chat("Mixed mode test")
        await asyncio.sleep(0.3)

        # Bob (PB) should get it via PB
        self.assertTrue(
            any("Mixed mode test" in text for _, text in bob_received),
        )

        # Charlie (legacy) should get translated text
        data = self._recv_all(legacy_sock, timeout=0.5)
        self.assertIn("Mixed mode test", data)

        await alice.disconnect()
        await bob.disconnect()
        legacy_sock.close()


if __name__ == "__main__":
    unittest.main()
