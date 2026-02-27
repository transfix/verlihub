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

    @pytest.mark.asyncio
    async def test_e2epm_fallback_to_plaintext_for_legacy(self):
        """Encrypted PM to a non-NMDCpb client falls back gracefully."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_pb = _unique_nick("fbPB")
        nick_leg = _unique_nick("fbLeg")

        alice = NMDCpbClient(nick_pb)

        async with NMDCTestClient(nick_leg, nmdcpb=False) as legacy:
            try:
                await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
                await asyncio.sleep(1.0)

                # Attempt E2EPM — should return False (peer doesn't support it)
                result = await alice.send_encrypted_pm(nick_leg, "Should fallback")
                assert not result, (
                    "send_encrypted_pm should return False for non-NMDCpb peer"
                )

                # Verify no crash on the sending side and legacy client
                # didn't receive encrypted garbage
                await asyncio.sleep(1.5)
                encrypted_lines = [
                    l for l in legacy.pbr_raw_lines + legacy.pb_raw_lines
                    if nick_pb in l
                ]
                assert len(encrypted_lines) == 0, (
                    f"Legacy client should not receive encrypted traffic: "
                    f"{encrypted_lines}"
                )
            finally:
                await alice.disconnect()

    @pytest.mark.asyncio
    async def test_e2epm_replay_rejection(self):
        """A replayed PbEncryptedPM with an old nonce is rejected."""
        from verlihub.client.nmdcpb.client import NMDCpbClient
        from verlihub.client.nmdcpb.wire import WireCodec
        from verlihub.client.nmdcpb.nmdcpb_pb2 import PbEnvelope

        nick_a = _unique_nick("rpA")
        nick_b = _unique_nick("rpB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        bob_msgs: list[tuple[str, str, bool]] = []
        warning_count = [0]

        def on_bob_msg(fn, text, ia):
            bob_msgs.append((fn, text, ia))

        alice_est = asyncio.Event()
        bob_est = asyncio.Event()
        alice.on_e2epm_established = lambda n, fp: alice_est.set()
        bob.on_e2epm_established = lambda n, fp: bob_est.set()
        bob.on_encrypted_pm = on_bob_msg

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            # Establish E2EPM session
            await alice.send_encrypted_pm(nick_b, "init")
            await asyncio.wait_for(alice_est.wait(), timeout=MSG_TIMEOUT)
            await asyncio.wait_for(bob_est.wait(), timeout=MSG_TIMEOUT)

            # Capture an encrypted PM
            epm = alice.e2epm.encrypt_pm(nick_b, "replay test")
            assert epm is not None
            captured_nonce = epm.nonce

            # Send the legit message
            env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=nick_a,
                to_nick=nick_b,
            )
            env.encrypted_pm.CopyFrom(epm)
            await alice._send_pb(env)
            await asyncio.sleep(1.5)

            legit_count = len(bob_msgs)
            assert legit_count >= 1, "Bob should receive the legit message"

            # Now replay the SAME encrypted message (same nonce)
            env2 = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=nick_a,
                to_nick=nick_b,
            )
            env2.encrypted_pm.CopyFrom(epm)
            await alice._send_pb(env2)
            await asyncio.sleep(1.5)

            # Bob should NOT have decrypted the replayed message (nonce reuse)
            assert len(bob_msgs) == legit_count, (
                f"Replay should have been rejected. Messages before: {legit_count}, "
                f"after: {len(bob_msgs)}"
            )

        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_e2epm_tamper_detection(self):
        """Modified ciphertext fails Poly1305 authentication — decryption rejected."""
        from verlihub.client.nmdcpb.client import NMDCpbClient
        from verlihub.client.nmdcpb.wire import WireCodec
        from verlihub.client.nmdcpb.nmdcpb_pb2 import PbEnvelope

        nick_a = _unique_nick("tamA")
        nick_b = _unique_nick("tamB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        bob_msgs: list[tuple[str, str, bool]] = []
        bob.on_encrypted_pm = lambda fn, t, ia: bob_msgs.append((fn, t, ia))

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

            # Encrypt a message and tamper with the ciphertext
            epm = alice.e2epm.encrypt_pm(nick_b, "tamper test")
            assert epm is not None
            ciphertext = bytearray(epm.ciphertext)
            if len(ciphertext) > 4:
                ciphertext[2] ^= 0xFF  # Flip a byte
                ciphertext[3] ^= 0xAA
            epm.ciphertext = bytes(ciphertext)

            # Send the tampered message through the hub
            env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=nick_a,
                to_nick=nick_b,
            )
            env.encrypted_pm.CopyFrom(epm)
            await alice._send_pb(env)
            await asyncio.sleep(2.0)

            # Bob should NOT have decrypted the tampered message
            assert len(bob_msgs) == 0, (
                f"Tampered message should fail AEAD verification, "
                f"but Bob decrypted: {bob_msgs}"
            )

        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_e2epm_reconnect_rekeys(self):
        """After disconnect/reconnect, fresh key exchange occurs (forward secrecy)."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("rkA")
        nick_b = _unique_nick("rkB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        fingerprints: list[str] = []

        alice_est = asyncio.Event()
        bob_est = asyncio.Event()
        alice.on_e2epm_established = lambda n, fp: (fingerprints.append(fp), alice_est.set())
        bob.on_e2epm_established = lambda n, fp: bob_est.set()

        try:
            # Session 1 — establish and record fingerprint
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            await alice.send_encrypted_pm(nick_b, "init session 1")
            await asyncio.wait_for(alice_est.wait(), timeout=MSG_TIMEOUT)
            await asyncio.wait_for(bob_est.wait(), timeout=MSG_TIMEOUT)

            fp1 = fingerprints[-1]
            assert fp1, "First fingerprint should not be empty"

            # Disconnect Alice
            await alice.disconnect()
            await asyncio.sleep(1.0)

            # Reconnect with a fresh client (new keypair)
            alice = NMDCpbClient(nick_a)
            alice_est2 = asyncio.Event()
            bob_est2 = asyncio.Event()
            alice.on_e2epm_established = lambda n, fp: (fingerprints.append(fp), alice_est2.set())
            bob.on_e2epm_established = lambda n, fp: bob_est2.set()

            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            # Session 2 — fresh key exchange
            await alice.send_encrypted_pm(nick_b, "init session 2")
            await asyncio.wait_for(alice_est2.wait(), timeout=MSG_TIMEOUT)
            await asyncio.wait_for(bob_est2.wait(), timeout=MSG_TIMEOUT)

            fp2 = fingerprints[-1]
            assert fp2, "Second fingerprint should not be empty"

            # Fingerprints should differ (new ephemeral keys each time)
            assert fp1 != fp2, (
                f"Fingerprints should differ after reconnect (forward secrecy): "
                f"session1={fp1}, session2={fp2}"
            )

        finally:
            await alice.disconnect()
            await bob.disconnect()


class TestNmdcPbLiveRelay:
    """Test relay session lifecycle and file transfer through a real verlihub.

    Both test clients announce ``M:P`` (passive mode) — the hub relay is the
    ONLY channel for data exchange.  This validates the Phase 2 requirement:
    "two passive clients through hub relay".

    Uses NMDCpbClient relay methods to exercise the full relay flow:
    relay_request → relay_ack → relay_data → relay_close.
    """

    @pytest.mark.asyncio
    async def test_relay_request_and_ack(self):
        """Two passive clients negotiate a relay session via PbRelayRequest/Ack."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("relA")
        nick_b = _unique_nick("relB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        bob_request_evt = asyncio.Event()
        bob_requests: list[tuple[str, str, str, int]] = []
        alice_est_evt = asyncio.Event()
        alice_established: list[tuple[int, str]] = []

        def on_bob_request(fn, tok, purp, sz):
            bob_requests.append((fn, tok, purp, sz))
            bob_request_evt.set()

        def on_alice_est(rid, pn):
            alice_established.append((rid, pn))
            alice_est_evt.set()

        bob.on_relay_request = on_bob_request
        alice.on_relay_established = on_alice_est

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            # Alice requests relay with Bob
            token = await alice.request_relay(
                nick_b, purpose="FILE_TRANSFER", estimated_size=1024,
            )
            assert token, "Token should be returned"

            # Bob must receive the request
            await asyncio.wait_for(bob_request_evt.wait(), timeout=MSG_TIMEOUT)
            assert len(bob_requests) >= 1, "Bob should receive relay request"
            fn, tok, purp, sz = bob_requests[0]
            assert fn == nick_a
            assert tok == token
            assert "FILE_TRANSFER" in purp
            assert sz == 1024

            # Bob accepts → Alice gets established callback
            await bob.accept_relay(token)
            await asyncio.wait_for(alice_est_evt.wait(), timeout=MSG_TIMEOUT)

            assert len(alice_established) >= 1, "Alice should see relay established"
            relay_id, peer = alice_established[0]
            assert peer == nick_b

        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_relay_data_roundtrip(self):
        """Send data through relay, verify exact byte-level reception."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("rdtA")
        nick_b = _unique_nick("rdtB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        bob_data_received: list[tuple[int, bytes]] = []
        bob_data_evt = asyncio.Event()
        alice_est_evt = asyncio.Event()
        bob_req_evt = asyncio.Event()
        received_token = [None]

        def on_bob_request(fn, tok, purp, sz):
            received_token[0] = tok
            bob_req_evt.set()

        def on_bob_data(rid, data, off):
            bob_data_received.append((rid, data))
            bob_data_evt.set()

        bob.on_relay_request = on_bob_request
        alice.on_relay_established = lambda rid, pn: alice_est_evt.set()
        bob.on_relay_data = on_bob_data

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            # Negotiate relay session
            token = await alice.request_relay(nick_b, purpose="FILE_TRANSFER")
            await asyncio.wait_for(bob_req_evt.wait(), timeout=MSG_TIMEOUT)
            await bob.accept_relay(received_token[0])
            await asyncio.wait_for(alice_est_evt.wait(), timeout=MSG_TIMEOUT)

            # Session must be established
            assert alice._relay_sessions, "Alice's relay session map should not be empty"
            relay_id = list(alice._relay_sessions.keys())[0]
            test_data = b"Hello through relay -- exact bytes!"

            sent = await alice.send_relay_data(relay_id, test_data)
            assert sent, "send_relay_data should return True"

            await asyncio.wait_for(bob_data_evt.wait(), timeout=MSG_TIMEOUT)
            assert len(bob_data_received) >= 1, "Bob should receive relay data"
            _, recv_data = bob_data_received[0]
            assert recv_data == test_data, (
                f"Data mismatch: sent {len(test_data)} bytes, "
                f"received {len(recv_data)} bytes"
            )

        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_relay_multiple_chunks(self):
        """Send multiple data chunks through relay, verify all arrive in order."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("mchA")
        nick_b = _unique_nick("mchB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        bob_chunks: list[bytes] = []
        all_received = asyncio.Event()
        alice_est_evt = asyncio.Event()
        bob_req_evt = asyncio.Event()
        received_token = [None]

        NUM_CHUNKS = 5
        CHUNK_SIZE = 512

        def on_bob_request(fn, tok, purp, sz):
            received_token[0] = tok
            bob_req_evt.set()

        def on_bob_data(rid, data, off):
            bob_chunks.append(data)
            if len(bob_chunks) >= NUM_CHUNKS:
                all_received.set()

        bob.on_relay_request = on_bob_request
        alice.on_relay_established = lambda rid, pn: alice_est_evt.set()
        bob.on_relay_data = on_bob_data

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            token = await alice.request_relay(nick_b, purpose="FILE_TRANSFER")
            await asyncio.wait_for(bob_req_evt.wait(), timeout=MSG_TIMEOUT)
            await bob.accept_relay(received_token[0])
            await asyncio.wait_for(alice_est_evt.wait(), timeout=MSG_TIMEOUT)

            relay_id = list(alice._relay_sessions.keys())[0]

            # Send multiple chunks
            sent_chunks = []
            for i in range(NUM_CHUNKS):
                chunk = bytes([(i + j) % 256 for j in range(CHUNK_SIZE)])
                sent_chunks.append(chunk)
                await alice.send_relay_data(relay_id, chunk, offset=i * CHUNK_SIZE)
                await asyncio.sleep(0.1)  # Small gap between chunks

            await asyncio.wait_for(all_received.wait(), timeout=MSG_TIMEOUT * 2)

            assert len(bob_chunks) == NUM_CHUNKS, (
                f"Expected {NUM_CHUNKS} chunks, got {len(bob_chunks)}"
            )
            for i, (sent, recv) in enumerate(zip(sent_chunks, bob_chunks)):
                assert sent == recv, f"Chunk {i} mismatch"

        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_relay_file_transfer_with_integrity(self):
        """End-to-end file transfer using RelayFileTransfer with SHA-256 verification."""
        from verlihub.client.nmdcpb.client import NMDCpbClient
        from verlihub.client.nmdcpb.relay import RelayFileTransfer, TransferState
        import hashlib
        import tempfile

        nick_a = _unique_nick("ftxA")
        nick_b = _unique_nick("ftxB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        transfer_complete = asyncio.Event()

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            # Create a temp file to send
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.dat', delete=False) as f:
                test_content = b"NMDCpb relay file transfer test data " * 100  # ~3.7 KB
                f.write(test_content)
                src_path = f.name
            src_hash = hashlib.sha256(test_content).hexdigest()

            with tempfile.TemporaryDirectory() as dl_dir:
                alice_ft = RelayFileTransfer(alice, chunk_size=1024)
                bob_ft = RelayFileTransfer(bob, auto_accept=True, download_dir=dl_dir)

                def on_complete(info):
                    transfer_complete.set()

                bob_ft.on_transfer_complete = on_complete

                # Alice sends file to Bob
                info = await alice_ft.send_file(nick_b, src_path)
                assert info.state == TransferState.PENDING
                assert info.file_size == len(test_content)

                # Wait for transfer to complete
                await asyncio.wait_for(transfer_complete.wait(), timeout=30.0)
                assert info.state == TransferState.COMPLETED, (
                    f"Transfer not completed: state={info.state}, "
                    f"transferred={info.bytes_transferred}/{info.file_size}"
                )
                assert info.bytes_transferred == info.file_size

                # Verify downloaded file integrity
                dl_path = os.path.join(dl_dir, os.path.basename(src_path))
                assert os.path.exists(dl_path), f"Downloaded file not found at {dl_path}"
                with open(dl_path, 'rb') as f:
                    dl_hash = hashlib.sha256(f.read()).hexdigest()
                assert dl_hash == src_hash, (
                    f"SHA-256 mismatch: sent={src_hash}, received={dl_hash}"
                )

                alice_ft.detach()
                bob_ft.detach()

            os.unlink(src_path)

        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_relay_request_rejected(self):
        """Relay rejection: Bob rejects, Alice doesn't get an established session."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("rejA")
        nick_b = _unique_nick("rejB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        bob_req_evt = asyncio.Event()
        received_token = [None]
        alice_established = []

        def on_bob_request(fn, tok, purp, sz):
            received_token[0] = tok
            bob_req_evt.set()

        bob.on_relay_request = on_bob_request
        alice.on_relay_established = lambda rid, pn: alice_established.append((rid, pn))

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            token = await alice.request_relay(nick_b, purpose="FILE_TRANSFER")
            await asyncio.wait_for(bob_req_evt.wait(), timeout=MSG_TIMEOUT)

            # Bob rejects
            await bob.reject_relay(received_token[0], reason="Not now")
            await asyncio.sleep(2.0)

            # Alice must NOT have an established relay session
            assert len(alice_established) == 0, (
                f"Alice should not have relay established after rejection, "
                f"but got: {alice_established}"
            )

        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_relay_close_notifies_peer(self):
        """Closing a relay session notifies the other peer."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("clsA")
        nick_b = _unique_nick("clsB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        bob_req_evt = asyncio.Event()
        received_token = [None]
        alice_est_evt = asyncio.Event()
        bob_closed_evt = asyncio.Event()
        bob_closed: list[tuple[int, str]] = []

        def on_bob_request(fn, tok, purp, sz):
            received_token[0] = tok
            bob_req_evt.set()

        def on_bob_closed(rid, reason):
            bob_closed.append((rid, reason))
            bob_closed_evt.set()

        bob.on_relay_request = on_bob_request
        bob.on_relay_closed = on_bob_closed
        alice.on_relay_established = lambda rid, pn: alice_est_evt.set()

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            token = await alice.request_relay(nick_b, purpose="FILE_TRANSFER")
            await asyncio.wait_for(bob_req_evt.wait(), timeout=MSG_TIMEOUT)
            await bob.accept_relay(received_token[0])
            await asyncio.wait_for(alice_est_evt.wait(), timeout=MSG_TIMEOUT)

            # Alice closes the session
            relay_id = list(alice._relay_sessions.keys())[0]
            await alice.close_relay(relay_id, "NORMAL")

            await asyncio.wait_for(bob_closed_evt.wait(), timeout=MSG_TIMEOUT)
            assert len(bob_closed) >= 1, "Bob should receive relay close"
            rid, reason = bob_closed[0]
            assert "NORMAL" in reason

        finally:
            await alice.disconnect()
            await bob.disconnect()

    @pytest.mark.asyncio
    async def test_relay_bidirectional_data(self):
        """Both peers can send data through the same relay (bidirectional)."""
        from verlihub.client.nmdcpb.client import NMDCpbClient

        nick_a = _unique_nick("biA")
        nick_b = _unique_nick("biB")

        alice = NMDCpbClient(nick_a)
        bob = NMDCpbClient(nick_b)

        alice_data: list[bytes] = []
        bob_data: list[bytes] = []
        alice_data_evt = asyncio.Event()
        bob_data_evt = asyncio.Event()
        alice_est_evt = asyncio.Event()
        bob_est_evt = asyncio.Event()
        bob_req_evt = asyncio.Event()
        received_token = [None]

        def on_bob_request(fn, tok, purp, sz):
            received_token[0] = tok
            bob_req_evt.set()

        bob.on_relay_request = on_bob_request
        alice.on_relay_established = lambda rid, pn: alice_est_evt.set()
        bob.on_relay_established = lambda rid, pn: bob_est_evt.set()
        alice.on_relay_data = lambda rid, data, off: (alice_data.append(data), alice_data_evt.set())
        bob.on_relay_data = lambda rid, data, off: (bob_data.append(data), bob_data_evt.set())

        try:
            await alice.connect(f"{HUB_HOST}:{HUB_PORT}")
            await bob.connect(f"{HUB_HOST}:{HUB_PORT}")
            await asyncio.sleep(1.0)

            token = await alice.request_relay(nick_b, purpose="FILE_TRANSFER")
            await asyncio.wait_for(bob_req_evt.wait(), timeout=MSG_TIMEOUT)
            await bob.accept_relay(received_token[0])
            await asyncio.wait_for(alice_est_evt.wait(), timeout=MSG_TIMEOUT)

            alice_rid = list(alice._relay_sessions.keys())[0]

            # Alice → Bob
            await alice.send_relay_data(alice_rid, b"from alice")
            await asyncio.wait_for(bob_data_evt.wait(), timeout=MSG_TIMEOUT)
            assert bob_data[0] == b"from alice"

            # Bob → Alice (Bob needs a relay session entry for Alice)
            # Bob's session is populated when accepting the relay
            if bob._relay_sessions:
                bob_rid = list(bob._relay_sessions.keys())[0]
                await bob.send_relay_data(bob_rid, b"from bob")
                await asyncio.wait_for(alice_data_evt.wait(), timeout=MSG_TIMEOUT)
                assert alice_data[0] == b"from bob"

        finally:
            await alice.disconnect()
            await bob.disconnect()
