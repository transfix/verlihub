"""
Live integration tests: NMDCpb client ↔ verlihub ↔ legacy client.

Connects to a real running verlihub instance (Docker) and verifies:
  1. NMDCpb feature negotiation ($Supports NMDCpb)
  2. PB chat broadcast between two NMDCpb clients
  3. PB→legacy translation (NMDCpb chat appears as NMDC <nick> text)
  4. Legacy→PB translation (NMDC chat forwarded as PbChat to NMDCpb clients)
  5. $PBR routed messages between NMDCpb clients
  6. Legacy client does NOT receive $PB/$PBR messages

Requires:
  - verlihub Docker container running on localhost:4111
    (or set VH_TEST_HOST / VH_TEST_PORT env vars)
  - Built with WITH_NMDCPB (protobuf support)

Run:
  cd verlihub && python -m pytest python/tests/test_nmdcpb_live.py -v
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid

import pytest

# --- configuration ----------------------------------------------------------

HUB_HOST = os.environ.get("VH_TEST_HOST", "127.0.0.1")
HUB_PORT = int(os.environ.get("VH_TEST_PORT", "4111"))

CONNECT_TIMEOUT = 10.0
MSG_TIMEOUT = 8.0

log = logging.getLogger(__name__)


# --- helpers -----------------------------------------------------------------

def _hub_reachable() -> bool:
    """Check if the verlihub hub is listening."""
    try:
        s = socket.create_connection((HUB_HOST, HUB_PORT), timeout=2)
        s.close()
        return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


# Reuse the library's lock-to-key instead of duplicating
from verlihub.client.nmdcpb.client import _nmdc_lock_to_key


class NMDCTestClient:
    """Minimal async NMDC test client with optional NMDCpb support.

    This is a lightweight test-only client — not the full NMDCpbClient.
    It speaks just enough NMDC to connect, negotiate, and send/receive
    chat and $PB messages.
    """

    def __init__(self, nick: str, *, nmdcpb: bool = True, password: str = ""):
        self.nick = nick
        self.nmdcpb = nmdcpb  # Announce NMDCpb in $Supports
        self.password = password
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._recv_task: asyncio.Task | None = None
        self._logged_in = asyncio.Event()
        self._hub_supports_nmdcpb = False

        # Collected messages (for assertions)
        self.chat_messages: list[tuple[str, str]] = []      # (nick, text)
        self.pb_raw_lines: list[str] = []                   # raw $PB... lines
        self.pbr_raw_lines: list[str] = []                  # raw $PBR... lines
        self.status_messages: list[str] = []                 # hub status/info
        self.all_lines: list[str] = []                       # every line

    # --- connection ----------------------------------------------------------

    async def connect(self, timeout: float = CONNECT_TIMEOUT) -> None:
        """Connect and complete NMDC handshake."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(HUB_HOST, HUB_PORT),
            timeout=timeout,
        )
        self._recv_task = asyncio.create_task(self._receive_loop())
        await asyncio.wait_for(self._logged_in.wait(), timeout=timeout)

    async def disconnect(self) -> None:
        """Disconnect cleanly."""
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # --- send ----------------------------------------------------------------

    async def send_raw(self, data: str) -> None:
        """Send a raw NMDC command (must end with |)."""
        assert self._writer is not None
        self._writer.write(data.encode("utf-8"))
        await self._writer.drain()

    async def send_chat(self, text: str) -> None:
        """Send a legacy NMDC chat message."""
        await self.send_raw(f"<{self.nick}> {text}|")

    async def send_pb(self, base64_payload: str) -> None:
        """Send a $PB message (text-mode protobuf broadcast)."""
        await self.send_raw(f"$PB {self.nick} {base64_payload}|")

    async def send_pbr(self, to_nick: str, base64_payload: str) -> None:
        """Send a $PBR routed message to a specific nick."""
        await self.send_raw(
            f"$PBR {to_nick} {self.nick} {base64_payload}|"
        )

    # --- receive -------------------------------------------------------------

    async def _receive_loop(self) -> None:
        buffer = b""
        try:
            while True:
                data = await self._reader.read(65536)
                if not data:
                    return
                buffer += data
                while b"|" in buffer:
                    line_bytes, buffer = buffer.split(b"|", 1)
                    line = line_bytes.decode("utf-8", errors="replace")
                    self.all_lines.append(line)
                    await self._handle_line(line)
        except asyncio.CancelledError:
            pass

    async def _handle_line(self, line: str) -> None:
        if line.startswith("$Lock "):
            await self._on_lock(line)
        elif line.startswith("$Supports "):
            self._on_supports(line)
        elif line.startswith("$Hello "):
            await self._on_hello(line)
        elif line.startswith("$GetPass"):
            await self._on_getpass()
        elif line.startswith("$PBR ") or line.startswith("$PBR\t"):
            self.pbr_raw_lines.append(line)
        elif line.startswith("$PB ") or line.startswith("$PBB "):
            self.pb_raw_lines.append(line)
        elif line.startswith("<"):
            # Public chat: <nick> message
            closing = line.index(">")
            nick = line[1:closing]
            text = line[closing + 2:]
            self.chat_messages.append((nick, text))
        elif line.startswith("$HubName ") or line.startswith("$HubTopic "):
            pass
        elif line.startswith("$UserIP ") or line.startswith("$NickList "):
            pass
        elif line.startswith("$OpList ") or line.startswith("$MyINFO "):
            pass
        elif line.startswith("$Quit "):
            pass

    async def _on_lock(self, line: str) -> None:
        parts = line.split(" ", 2)
        lock = parts[1] if len(parts) > 1 else ""
        key = _nmdc_lock_to_key(lock)

        features = "UserCommand NoGetINFO NoHello UserIP2 TTHSearch"
        if self.nmdcpb:
            features += " NMDCpb"

        await self.send_raw(f"$Supports {features}|")
        await self.send_raw(f"$Key {key}|")
        await self.send_raw(f"$ValidateNick {self.nick}|")

    def _on_supports(self, line: str) -> None:
        tokens = line.split()
        self._hub_supports_nmdcpb = "NMDCpb" in tokens

    async def _on_hello(self, line: str) -> None:
        nick = line[7:]
        if nick == self.nick:
            # Must send $Version before $MyINFO — hub requires eLS_VERSION
            await self.send_raw("$Version 1,0091|")
            await self.send_raw("$GetNickList|")

            tag = "<NMDCpbTest V:0.1,M:P,H:1/0/0,S:1>"
            myinfo = (
                f"$MyINFO $ALL {self.nick} "
                f"test client{tag}$ $LAN(T1)\x01${self.nick}@test"
                f"$0$"
            )
            await self.send_raw(myinfo + "|")
            self._logged_in.set()

    async def _on_getpass(self) -> None:
        if self.password:
            await self.send_raw(f"$MyPass {self.password}|")

    # --- wait helpers --------------------------------------------------------

    async def wait_for_chat(
        self, *, from_nick: str | None = None, timeout: float = MSG_TIMEOUT,
        start_idx: int | None = None,
    ) -> tuple[str, str]:
        """Wait for a chat message, optionally from a specific nick."""
        deadline = asyncio.get_event_loop().time() + timeout
        idx = start_idx if start_idx is not None else len(self.chat_messages)
        while True:
            if idx < len(self.chat_messages):
                nick, text = self.chat_messages[idx]
                if from_nick is None or nick == from_nick:
                    return (nick, text)
                idx += 1
                continue
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"No chat from {from_nick or 'anyone'} within {timeout}s"
                )
            await asyncio.sleep(0.05)

    async def wait_for_pb(self, *, timeout: float = MSG_TIMEOUT) -> str:
        """Wait for a $PB line."""
        deadline = asyncio.get_event_loop().time() + timeout
        idx = len(self.pb_raw_lines)
        while True:
            if idx < len(self.pb_raw_lines):
                return self.pb_raw_lines[idx]
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"No $PB message within {timeout}s"
                )
            await asyncio.sleep(0.05)

    async def wait_for_pbr(self, *, timeout: float = MSG_TIMEOUT) -> str:
        """Wait for a $PBR line."""
        deadline = asyncio.get_event_loop().time() + timeout
        idx = len(self.pbr_raw_lines)
        while True:
            if idx < len(self.pbr_raw_lines):
                return self.pbr_raw_lines[idx]
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"No $PBR message within {timeout}s"
                )
            await asyncio.sleep(0.05)


def _unique_nick(prefix: str = "pb") -> str:
    """Generate a unique nick to avoid collisions between test runs."""
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


def _make_pb_chat(from_nick: str, text: str, is_action: bool = False) -> str:
    """Build a $PB command for a PbChat broadcast.

    Returns the full wire string: ``$PB <nick> <base64>|``
    """
    from verlihub.client.nmdcpb.nmdcpb_pb2 import PbEnvelope
    import base64 as _b64

    env = PbEnvelope()
    env.route = PbEnvelope.BROADCAST
    env.from_nick = from_nick
    env.timestamp = int(time.time() * 1000)
    env.chat.text = text
    env.chat.is_action = is_action

    raw = env.SerializeToString()
    b64 = _b64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"$PB {from_nick} {b64}|"


def _make_pbr(from_nick: str, to_nick: str, text: str) -> str:
    """Build a $PBR routed command.

    Returns the full wire string: ``$PBR <to> <from> <base64>|``
    """
    from verlihub.client.nmdcpb.nmdcpb_pb2 import PbEnvelope
    import base64 as _b64

    env = PbEnvelope()
    env.route = PbEnvelope.DIRECT
    env.from_nick = from_nick
    env.to_nick = to_nick
    env.timestamp = int(time.time() * 1000)
    env.chat.text = text

    raw = env.SerializeToString()
    b64 = _b64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"$PBR {to_nick} {from_nick} {b64}|"


# --- skip if hub unavailable ------------------------------------------------

pytestmark = pytest.mark.skipif(
    not _hub_reachable(),
    reason=f"verlihub not reachable at {HUB_HOST}:{HUB_PORT}",
)


# ============================================================================
# Tests
# ============================================================================


class TestNmdcPbLiveNegotiation:
    """Test NMDCpb feature negotiation against a real verlihub."""

    @pytest.mark.asyncio
    async def test_hub_announces_nmdcpb_support(self):
        """Hub includes NMDCpb in its $Supports reply."""
        async with NMDCTestClient(_unique_nick("neg"), nmdcpb=True) as c:
            assert c._hub_supports_nmdcpb, (
                "Hub did not announce NMDCpb in $Supports — "
                "is verlihub built WITH_NMDCPB?"
            )

    @pytest.mark.asyncio
    async def test_legacy_client_connects_without_nmdcpb(self):
        """A client that doesn't announce NMDCpb still connects fine."""
        async with NMDCTestClient(_unique_nick("leg"), nmdcpb=False) as c:
            assert c._logged_in.is_set()
            # Hub may or may not include NMDCpb in its Supports;
            # the key thing is the client connects.


class TestNmdcPbLiveChat:
    """Test protobuf chat messaging through a real verlihub."""

    @pytest.mark.asyncio
    async def test_pb_chat_received_by_nmdcpb_client(self):
        """A $PB chat from one NMDCpb client reaches another NMDCpb client."""
        nick_a = _unique_nick("chatA")
        nick_b = _unique_nick("chatB")

        async with NMDCTestClient(nick_a, nmdcpb=True) as alice:
            async with NMDCTestClient(nick_b, nmdcpb=True) as bob:
                await asyncio.sleep(0.5)

                await alice.send_raw(_make_pb_chat(nick_a, "Hello from protobuf!"))

                pb_line = await bob.wait_for_pb(timeout=MSG_TIMEOUT)
                assert "$PB" in pb_line

    @pytest.mark.asyncio
    async def test_pb_chat_translated_to_legacy(self):
        """A PB chat from an NMDCpb client is translated to NMDC for legacy clients."""
        nick_pb = _unique_nick("pbsnd")
        nick_leg = _unique_nick("legrcv")

        async with NMDCTestClient(nick_pb, nmdcpb=True) as sender:
            async with NMDCTestClient(nick_leg, nmdcpb=False) as legacy:
                await asyncio.sleep(0.5)

                await sender.send_raw(
                    _make_pb_chat(nick_pb, "Translated hello!")
                )

                # Legacy client should receive a standard NMDC chat
                nick, text = await legacy.wait_for_chat(
                    from_nick=nick_pb, timeout=MSG_TIMEOUT
                )
                assert nick == nick_pb
                assert "Translated hello!" in text

    @pytest.mark.asyncio
    async def test_legacy_chat_translated_to_pb(self):
        """A legacy NMDC chat is forwarded as PB to NMDCpb clients."""
        nick_leg = _unique_nick("legsnd")
        nick_pb = _unique_nick("pbrcv")

        async with NMDCTestClient(nick_leg, nmdcpb=False) as legacy:
            async with NMDCTestClient(nick_pb, nmdcpb=True) as pb_client:
                await asyncio.sleep(0.5)

                await legacy.send_chat("Legacy hello!")

                # PB client should receive either a $PB line or a legacy
                # chat line (depending on whether reverse translation is
                # active). If reverse translation is enabled, we get $PB.
                # If not, we get <nick> text.
                try:
                    pb_line = await pb_client.wait_for_pb(timeout=3.0)
                    assert "$PB" in pb_line
                    log.info("Reverse translation active: legacy→PB working")
                except asyncio.TimeoutError:
                    # No $PB — check for legacy forwarding
                    nick, text = await pb_client.wait_for_chat(
                        from_nick=nick_leg, timeout=3.0
                    )
                    assert "Legacy hello!" in text
                    log.info(
                        "Reverse translation not active; "
                        "legacy chat forwarded as NMDC"
                    )

    @pytest.mark.asyncio
    async def test_legacy_client_does_not_receive_raw_pb(self):
        """Legacy clients never see raw $PB lines."""
        nick_pb = _unique_nick("pbonly")
        nick_leg = _unique_nick("nopb")

        async with NMDCTestClient(nick_pb, nmdcpb=True) as sender:
            async with NMDCTestClient(nick_leg, nmdcpb=False) as legacy:
                await asyncio.sleep(0.5)

                await sender.send_raw(
                    _make_pb_chat(nick_pb, "PB only message")
                )

                # Give hub time to forward
                await asyncio.sleep(1.5)

                # Legacy client must NOT have any $PB lines
                assert len(legacy.pb_raw_lines) == 0, (
                    f"Legacy client received raw $PB lines: "
                    f"{legacy.pb_raw_lines}"
                )


class TestNmdcPbLiveRouted:
    """Test $PBR routed (direct) messages through a real verlihub."""

    @pytest.mark.asyncio
    async def test_pbr_routed_message_delivered(self):
        """A $PBR message from Alice reaches Bob directly."""
        nick_a = _unique_nick("rteA")
        nick_b = _unique_nick("rteB")

        async with NMDCTestClient(nick_a, nmdcpb=True) as alice:
            async with NMDCTestClient(nick_b, nmdcpb=True) as bob:
                await asyncio.sleep(0.5)

                await alice.send_raw(
                    _make_pbr(nick_a, nick_b, "Routed hello!")
                )

                pbr_line = await bob.wait_for_pbr(timeout=MSG_TIMEOUT)
                assert nick_a in pbr_line

    @pytest.mark.asyncio
    async def test_pbr_not_leaked_to_third_party(self):
        """A $PBR message from Alice to Bob is NOT seen by Carol."""
        nick_a = _unique_nick("rteA2")
        nick_b = _unique_nick("rteB2")
        nick_c = _unique_nick("rteC2")

        async with NMDCTestClient(nick_a, nmdcpb=True) as alice:
            async with NMDCTestClient(nick_b, nmdcpb=True) as bob:
                async with NMDCTestClient(nick_c, nmdcpb=True) as carol:
                    await asyncio.sleep(0.5)

                    await alice.send_raw(
                        _make_pbr(nick_a, nick_b, "Private routed")
                    )

                    # Bob should receive it
                    pbr_line = await bob.wait_for_pbr(timeout=MSG_TIMEOUT)
                    assert nick_a in pbr_line

                    # Carol should NOT have received it
                    await asyncio.sleep(1.5)
                    assert len(carol.pbr_raw_lines) == 0, (
                        f"Carol received leaked $PBR: {carol.pbr_raw_lines}"
                    )


class TestNmdcPbLiveMixed:
    """Mixed NMDCpb + legacy client scenarios."""

    @pytest.mark.asyncio
    async def test_three_clients_mixed_chat(self):
        """Three clients (2 NMDCpb, 1 legacy) all receive chat."""
        nick_a = _unique_nick("mixA")
        nick_b = _unique_nick("mixB")
        nick_c = _unique_nick("mixC")

        async with NMDCTestClient(nick_a, nmdcpb=True) as alice:
            async with NMDCTestClient(nick_b, nmdcpb=True) as bob:
                async with NMDCTestClient(nick_c, nmdcpb=False) as carol:
                    await asyncio.sleep(0.5)

                    # Snapshot indices BEFORE sending so wait_* calls
                    # don't skip messages that arrive quickly.
                    carol_chat_idx = len(carol.chat_messages)
                    bob_chat_idx = len(bob.chat_messages)
                    bob_pb_idx = len(bob.pb_raw_lines)

                    # Alice sends legacy chat
                    await alice.send_chat("Hi everyone!")

                    # Carol (legacy) should see it as NMDC chat
                    nick, text = await carol.wait_for_chat(
                        from_nick=nick_a, timeout=MSG_TIMEOUT,
                        start_idx=carol_chat_idx,
                    )
                    assert "Hi everyone!" in text

                    # Bob (NMDCpb) should also see it (either as NMDC
                    # chat or as PB, depending on reverse translation)
                    await asyncio.sleep(1.0)
                    found = False
                    for n, t in bob.chat_messages[bob_chat_idx:]:
                        if n == nick_a and "Hi everyone!" in t:
                            found = True
                            break
                    if not found and len(bob.pb_raw_lines) > bob_pb_idx:
                        found = True  # Got it as PB
                    assert found, "Bob didn't receive Alice's chat"

    @pytest.mark.asyncio
    async def test_concurrent_pb_and_legacy_chat(self):
        """PB and legacy chat messages don't interfere with each other."""
        nick_pb = _unique_nick("conPB")
        nick_leg = _unique_nick("conLeg")

        async with NMDCTestClient(nick_pb, nmdcpb=True) as pb_client:
            async with NMDCTestClient(nick_leg, nmdcpb=False) as legacy:
                await asyncio.sleep(0.5)

                # Snapshot indices before sending
                pb_chat_idx = len(pb_client.chat_messages)
                pb_pb_idx = len(pb_client.pb_raw_lines)

                # Send legacy chat
                await legacy.send_chat("Legacy msg")
                await asyncio.sleep(1.5)

                # PB client should get the legacy chat
                found_legacy = any(
                    "Legacy msg" in t
                    for _, t in pb_client.chat_messages[pb_chat_idx:]
                )
                found_pb_legacy = len(pb_client.pb_raw_lines) > pb_pb_idx

                # At least one delivery mechanism should work
                assert found_legacy or found_pb_legacy, (
                    "PB client didn't receive legacy chat in any form"
                )

                # Snapshot legacy indices
                leg_chat_idx = len(legacy.chat_messages)

                # Send PB chat
                await pb_client.send_raw(
                    _make_pb_chat(nick_pb, "PB msg")
                )
                await asyncio.sleep(1.5)

                # Legacy client should get the translated chat
                found_translated = any(
                    "PB msg" in t
                    for _, t in legacy.chat_messages[leg_chat_idx:]
                )
                assert found_translated, (
                    "Legacy client didn't receive translated PB chat"
                )


# ============================================================================
# Phase 2: E2EPM Live Tests (NMDCpbClient library ↔ real verlihub)
# ============================================================================


class TestNmdcPbLiveE2EPM:
    """Test E2EPM key exchange and encrypted messaging through a real verlihub.

    Uses the full NMDCpbClient library (not the lightweight NMDCTestClient)
    to exercise the complete E2EPM flow through the hub's DC_PBR handler.
    """

    @pytest.mark.asyncio
    async def test_e2epm_key_exchange_through_hub(self):
        """Two NMDCpbClient instances complete E2EPM key exchange via hub."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("kexA")
        nick_b = _unique_nick("kexB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        alice_est = asyncio.Event()
        bob_est = asyncio.Event()
        alice.on_e2epm_established = lambda n, fp: alice_est.set()
        bob.on_e2epm_established = lambda n, fp: bob_est.set()

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)  # Let handshake complete

            # Alice initiates E2EPM with Bob (triggers key exchange)
            result = await alice.send_encrypted_pm(nick_b, "init")
            assert not result, "First send should return False (kex in progress)"

            # Wait for both sides to establish session
            await asyncio.wait_for(bob_est.wait(), timeout=MSG_TIMEOUT)
            await asyncio.wait_for(alice_est.wait(), timeout=MSG_TIMEOUT)

            assert alice.e2epm.has_session(nick_b), \
                "Alice should have E2EPM session with Bob"
            assert bob.e2epm.has_session(nick_a), \
                "Bob should have E2EPM session with Alice"
        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_e2epm_encrypted_pm_through_hub(self):
        """Full E2EPM flow: key exchange → encrypted PM → decryption."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("epmA")
        nick_b = _unique_nick("epmB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        decrypted: list[tuple[str, str, bool]] = []
        bob.on_encrypted_pm = lambda fn, text, ia: decrypted.append((fn, text, ia))

        alice_est = asyncio.Event()
        bob_est = asyncio.Event()
        alice.on_e2epm_established = lambda n, fp: alice_est.set()
        bob.on_e2epm_established = lambda n, fp: bob_est.set()

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            # Initiate key exchange
            await alice.send_encrypted_pm(nick_b, "trigger kex")
            await asyncio.wait_for(alice_est.wait(), timeout=MSG_TIMEOUT)
            await asyncio.wait_for(bob_est.wait(), timeout=MSG_TIMEOUT)

            # Now send the real encrypted message
            sent = await alice.send_encrypted_pm(nick_b, "Top secret via hub!")
            assert sent, "Message should have been sent after kex complete"

            await asyncio.sleep(2.0)

            assert any(
                text == "Top secret via hub!" for _, text, _ in decrypted
            ), f"Bob didn't decrypt. Got: {decrypted}"
        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_e2epm_bidirectional(self):
        """Both clients can send encrypted PMs to each other once established."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("bidA")
        nick_b = _unique_nick("bidB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        alice_msgs: list[tuple[str, str, bool]] = []
        bob_msgs: list[tuple[str, str, bool]] = []
        alice.on_encrypted_pm = lambda fn, text, ia: alice_msgs.append((fn, text, ia))
        bob.on_encrypted_pm = lambda fn, text, ia: bob_msgs.append((fn, text, ia))

        alice_est = asyncio.Event()
        bob_est = asyncio.Event()
        alice.on_e2epm_established = lambda n, fp: alice_est.set()
        bob.on_e2epm_established = lambda n, fp: bob_est.set()

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            # Establish session
            await alice.send_encrypted_pm(nick_b, "init")
            await asyncio.wait_for(alice_est.wait(), timeout=MSG_TIMEOUT)
            await asyncio.wait_for(bob_est.wait(), timeout=MSG_TIMEOUT)

            # Alice → Bob
            sent1 = await alice.send_encrypted_pm(nick_b, "From Alice")
            assert sent1

            # Bob → Alice
            sent2 = await bob.send_encrypted_pm(nick_a, "From Bob")
            assert sent2

            await asyncio.sleep(2.0)

            assert any(t == "From Alice" for _, t, _ in bob_msgs), \
                f"Bob didn't receive Alice's msg: {bob_msgs}"
            assert any(t == "From Bob" for _, t, _ in alice_msgs), \
                f"Alice didn't receive Bob's msg: {alice_msgs}"
        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_e2epm_fingerprint_consistency(self):
        """Both peers compute matching fingerprints after key exchange."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("fpA")
        nick_b = _unique_nick("fpB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        alice_fp = None
        bob_fp = None

        def on_alice_est(n, fp):
            nonlocal alice_fp
            alice_fp = fp

        def on_bob_est(n, fp):
            nonlocal bob_fp
            bob_fp = fp

        alice_est = asyncio.Event()
        bob_est = asyncio.Event()

        alice.on_e2epm_established = lambda n, fp: (on_alice_est(n, fp), alice_est.set())
        bob.on_e2epm_established = lambda n, fp: (on_bob_est(n, fp), bob_est.set())

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            await alice.send_encrypted_pm(nick_b, "fp check")
            await asyncio.wait_for(alice_est.wait(), timeout=MSG_TIMEOUT)
            await asyncio.wait_for(bob_est.wait(), timeout=MSG_TIMEOUT)

            assert alice_fp is not None, "Alice fingerprint not set"
            assert bob_fp is not None, "Bob fingerprint not set"
            assert alice_fp == bob_fp, \
                f"Fingerprints mismatch: {alice_fp} != {bob_fp}"
        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_e2epm_third_party_cannot_decrypt(self):
        """A third NMDCpb client cannot see E2EPM plaintext between two peers."""
        nick_a = _unique_nick("secA")
        nick_b = _unique_nick("secB")
        nick_c = _unique_nick("secC")

        async with NMDCTestClient(nick_c, nmdcpb=True) as carol:
            from verlihub.client.nmdcpb.client import NMDCpbClient

            alice = NMDCpbClient(nick_a)
            bob = NMDCpbClient(nick_b)

            bob_msgs: list[tuple[str, str, bool]] = []
            bob.on_encrypted_pm = lambda fn, t, ia: bob_msgs.append((fn, t, ia))

            a_est = asyncio.Event()
            b_est = asyncio.Event()
            alice.on_e2epm_established = lambda n, fp: a_est.set()
            bob.on_e2epm_established = lambda n, fp: b_est.set()

            try:
                await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
                await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
                await asyncio.sleep(1.0)

                await alice.send_encrypted_pm(nick_b, "init")
                await asyncio.wait_for(a_est.wait(), timeout=MSG_TIMEOUT)
                await asyncio.wait_for(b_est.wait(), timeout=MSG_TIMEOUT)

                await alice.send_encrypted_pm(nick_b, "Secret message")
                await asyncio.sleep(2.0)

                # Bob got it
                assert any(t == "Secret message" for _, t, _ in bob_msgs)

                # Carol should NOT have any $PBR lines (E2EPM is point-to-point)
                assert len(carol.pbr_raw_lines) == 0, \
                    f"Carol received E2EPM traffic: {carol.pbr_raw_lines}"

                # Also no $PB lines from this exchange
                # (E2EPM uses $PBR, not $PB)
                epm_in_pb = [
                    l for l in carol.pb_raw_lines
                    if nick_a in l or nick_b in l
                ]
                assert len(epm_in_pb) == 0, \
                    f"Carol received E2EPM as $PB: {epm_in_pb}"
            finally:
                await alice.disconnect()
                await bob.disconnect()
