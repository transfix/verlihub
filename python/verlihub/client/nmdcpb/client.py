"""
NMDCpb client — raw TCP NMDC client with protobuf extension support.

This is a Phase 0 prototype client that connects directly to an NMDC hub
via TCP and speaks both standard NMDC and the NMDCpb extension. It's used
to validate the wire protocol design before implementing C++ support.

Usage:
    client = NMDCpbClient("nick", "password")
    await client.connect("nmdc://hub.example.com:411")

    # Send protobuf chat
    await client.send_pb_chat("Hello from protobuf!")

    # Send encrypted PM
    await client.send_encrypted_pm("target_nick", "Secret message")

    # Receive messages via callbacks
    client.on_pb_message = lambda env: print(f"PB: {env}")
    client.on_chat = lambda nick, text: print(f"<{nick}> {text}")
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Callable, Optional

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbChat,
    PbUserInfo,
    PbSearch,
    PbSearchResult,
    PbPMKeyExchange,
    PbEncryptedPM,
    PbHubInfo,
    PbStatus,
    PbRelayRequest,
    PbRelayAck,
    PbRelayData,
    PbRelayClosed,
    PbRelayStatus,
    PbPrivateSearch,
    PbPrivateSearchResult,
)
from verlihub.client.nmdcpb.wire import WireCodec, FEATURE_NMDCPB, FEATURE_HUBRELAY
from verlihub.client.nmdcpb.e2epm import E2EPMManager

log = logging.getLogger(__name__)


def _nmdc_lock_to_key(lock: str) -> str:
    """Compute the NMDC $Key from a $Lock challenge.

    Standard NMDC key computation algorithm.
    """
    key_bytes = []
    lock_bytes = [ord(c) for c in lock]
    n = len(lock_bytes)

    for i in range(1, n):
        key_bytes.append(lock_bytes[i] ^ lock_bytes[i - 1])
    key_bytes.insert(0, lock_bytes[0] ^ lock_bytes[n - 1] ^ lock_bytes[n - 2] ^ 5)

    # Nibble-swap each byte
    for i in range(len(key_bytes)):
        key_bytes[i] = ((key_bytes[i] << 4) | (key_bytes[i] >> 4)) & 0xFF

    # Escape special chars
    result = []
    for b in key_bytes:
        if b in (0, 5, 36, 96, 124, 126):
            result.append(f"/%DCN{b:03d}%/")
        else:
            result.append(chr(b))
    return "".join(result)


class NMDCpbClient:
    """Raw TCP NMDC client with NMDCpb protobuf support.

    This is a Phase 0 prototype — it implements just enough of the NMDC
    protocol to connect, log in, and exchange NMDCpb messages.
    """

    def __init__(
        self,
        nick: str,
        password: str = "",
        description: str = "NMDCpb prototype",
        share_size: int = 0,
        slots: int = 1,
    ):
        self.nick = nick
        self.password = password
        self.description = description
        self.share_size = share_size
        self.slots = slots

        # Connection state
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._logged_in = False
        self._hub_nmdcpb = False  # Hub supports NMDCpb
        self._hub_hubrelay = False  # Hub supports HubRelay
        self._recv_task: Optional[asyncio.Task] = None

        # NMDCpb state
        self.e2epm = E2EPMManager(nick)
        self._sequence = 0

        # Relay session state
        # _relay_sessions: relay_id -> {token, peer_nick, purpose, established,
        #                               bytes_sent, bytes_received, our_key, shared_key}
        self._relay_sessions: dict[int, dict] = {}
        # _pending_relay_tokens: token -> {target_nick, purpose, estimated_size, our_privkey, our_pubkey}
        self._pending_relay_tokens: dict[str, dict] = {}

        # Known users
        self.users: dict[str, dict] = {}

        # Callbacks
        self.on_chat: Optional[Callable[[str, str], None]] = None
        self.on_pm: Optional[Callable[[str, str, str], None]] = None
        self.on_pb_message: Optional[Callable[[PbEnvelope], None]] = None
        self.on_encrypted_pm: Optional[Callable[[str, str, bool], None]] = None
        self.on_e2epm_established: Optional[Callable[[str, str], None]] = None
        self.on_status: Optional[Callable[[str], None]] = None
        self.on_user_join: Optional[Callable[[str], None]] = None
        self.on_user_quit: Optional[Callable[[str], None]] = None
        self.on_connected: Optional[Callable[[], None]] = None
        self.on_disconnected: Optional[Callable[[str], None]] = None
        # Relay callbacks
        self.on_relay_request: Optional[Callable[[str, str, str, int], None]] = None  # (from_nick, token, purpose, est_size)
        self.on_relay_established: Optional[Callable[[int, str], None]] = None  # (relay_id, peer_nick)
        self.on_relay_data: Optional[Callable[[int, bytes, int], None]] = None  # (relay_id, data, offset)
        self.on_relay_closed: Optional[Callable[[int, str], None]] = None  # (relay_id, reason)
        self.on_relay_status: Optional[Callable[[PbRelayStatus], None]] = None
        # PrivateSearch callbacks
        self.on_private_search: Optional[Callable[[str, PbPrivateSearch], None]] = None  # (from_nick, search)
        self.on_private_search_result: Optional[Callable[[str, PbPrivateSearchResult], None]] = None  # (from_nick, result)

    @property
    def hub_supports_nmdcpb(self) -> bool:
        return self._hub_nmdcpb

    @property
    def hub_supports_hubrelay(self) -> bool:
        return self._hub_hubrelay

    # --- Connection ---

    async def connect(self, hub_url: str) -> None:
        """Connect to an NMDC hub.

        Args:
            hub_url: Hub URL in format nmdc://host:port or just host:port
        """
        # Parse URL
        url = hub_url.replace("nmdc://", "").replace("dchub://", "")
        if ":" in url:
            host, port_str = url.rsplit(":", 1)
            port = int(port_str.rstrip("/"))
        else:
            host = url
            port = 411  # Default NMDC port

        log.info(f"Connecting to {host}:{port}")
        self._reader, self._writer = await asyncio.open_connection(host, port)
        self._connected = True

        # Start receive loop
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        """Disconnect from the hub."""
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
        self._connected = False
        self._logged_in = False
        self.e2epm.clear_all()
        if self.on_disconnected:
            self.on_disconnected("user disconnect")

    async def _send_raw(self, data: str) -> None:
        """Send raw NMDC command (must end with |)."""
        if not self._writer:
            raise RuntimeError("Not connected")
        log.debug(f">>> {data[:200]}")
        self._writer.write(data.encode("utf-8"))
        await self._writer.drain()

    async def _send_raw_bytes(self, data: bytes) -> None:
        """Send raw bytes (for binary protobuf frames)."""
        if not self._writer:
            raise RuntimeError("Not connected")
        log.debug(f">>> [{len(data)} bytes]")
        self._writer.write(data)
        await self._writer.drain()

    # --- NMDC Protocol ---

    async def _receive_loop(self) -> None:
        """Main receive loop — reads | delimited NMDC commands."""
        buffer = b""
        try:
            while self._connected:
                data = await self._reader.read(65536)
                if not data:
                    log.info("Connection closed by hub")
                    self._connected = False
                    if self.on_disconnected:
                        self.on_disconnected("connection closed")
                    return

                buffer += data
                while b"|" in buffer:
                    line, buffer = buffer.split(b"|", 1)
                    line_str = line.decode("utf-8", errors="replace")
                    await self._handle_line(line_str)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Receive error: {e}")
            self._connected = False
            if self.on_disconnected:
                self.on_disconnected(str(e))

    async def _handle_line(self, line: str) -> None:
        """Dispatch an incoming NMDC command."""
        if not line:
            return

        log.debug(f"<<< {line[:200]}")

        # Check for NMDCpb commands first
        if WireCodec.is_nmdcpb_command(line + "|"):
            env = WireCodec.decode(line + "|")
            if env is not None:
                await self._handle_pb_envelope(env)
            return

        # Standard NMDC dispatch
        if line.startswith("$Lock "):
            await self._handle_lock(line)
        elif line.startswith("$Supports "):
            await self._handle_supports(line)
        elif line.startswith("$Hello "):
            await self._handle_hello(line)
        elif line.startswith("$HubName "):
            self._handle_hub_name(line)
        elif line.startswith("$HubTopic "):
            pass  # Ignore for prototype
        elif line.startswith("$GetPass"):
            await self._handle_getpass()
        elif line.startswith("$BadPass"):
            log.error("Bad password!")
        elif line.startswith("$ValidateDenide"):
            log.error("Nick taken!")
        elif line.startswith("$HubIsFull"):
            log.error("Hub is full!")
        elif line.startswith("$NickList "):
            self._handle_nicklist(line)
        elif line.startswith("$OpList "):
            pass  # Ignore for prototype
        elif line.startswith("$MyINFO "):
            self._handle_myinfo(line)
        elif line.startswith("$Quit "):
            nick = line[6:]
            self.users.pop(nick, None)
            if self.on_user_quit:
                self.on_user_quit(nick)
        elif line.startswith("$To: "):
            self._handle_pm(line)
        elif line.startswith("$ForceMove "):
            log.warning(f"Redirect to: {line[11:]}")
        elif line.startswith("$UserIP "):
            pass  # Ignore for prototype
        elif line.startswith("<"):
            self._handle_chat(line)
        elif line.startswith("$Search "):
            pass  # Ignore for prototype
        elif line.startswith("$SR "):
            pass  # Ignore for prototype
        elif line.startswith("$ConnectToMe "):
            pass  # Ignore for prototype
        elif line.startswith("$RevConnectToMe "):
            pass  # Ignore for prototype

    async def _handle_lock(self, line: str) -> None:
        """Handle $Lock — compute key, send $Supports + login sequence."""
        # Parse lock
        parts = line.split(" ", 2)
        lock = parts[1] if len(parts) > 1 else ""

        # Compute key
        key = _nmdc_lock_to_key(lock)

        # Send $Supports with NMDCpb and HubRelay
        features = "UserCommand NoGetINFO NoHello UserIP2 TTHSearch NMDCpb HubRelay"
        await self._send_raw(f"$Supports {features}|")

        # Send $Key
        await self._send_raw(f"$Key {key}|")

        # Send $ValidateNick
        await self._send_raw(f"$ValidateNick {self.nick}|")

    async def _handle_supports(self, line: str) -> None:
        """Handle $Supports from hub — check for NMDCpb support."""
        self._hub_nmdcpb, self._hub_hubrelay = WireCodec.check_supports(line)
        if self._hub_nmdcpb:
            log.info("Hub supports NMDCpb!")
        if self._hub_hubrelay:
            log.info("Hub supports HubRelay!")

    async def _handle_hello(self, line: str) -> None:
        """Handle $Hello — we're logged in, send $MyINFO."""
        nick = line[7:]
        if nick == self.nick:
            self._logged_in = True
            log.info(f"Logged in as {self.nick}")

            # Send $Version (required by hub login state machine)
            await self._send_raw("$Version 1,0091|")

            # Request nick list
            await self._send_raw("$GetNickList|")

            # Send MyINFO
            tag = f"<NMDCpb V:0.1.0,M:P,H:1/0/0,S:{self.slots}>"
            myinfo = (
                f"$MyINFO $ALL {self.nick} "
                f"{self.description}{tag}$ $LAN(T1)\x01${self.nick}@nmdcpb"
                f"${self.share_size}$"
            )
            await self._send_raw(myinfo + "|")

            if self.on_connected:
                self.on_connected()

    async def _handle_getpass(self) -> None:
        """Handle $GetPass — send password."""
        if self.password:
            await self._send_raw(f"$MyPass {self.password}|")
        else:
            log.error("Password required but none set!")

    def _handle_hub_name(self, line: str) -> None:
        """Handle $HubName."""
        hub_name = line[9:]
        log.info(f"Hub: {hub_name}")

    def _handle_nicklist(self, line: str) -> None:
        """Handle $NickList — populate user list."""
        nicks = line[10:].rstrip("$").split("$$")
        for nick in nicks:
            if nick and nick not in self.users:
                self.users[nick] = {}
                if self.on_user_join:
                    self.on_user_join(nick)

    def _handle_myinfo(self, line: str) -> None:
        """Handle $MyINFO — parse basic user info."""
        # $MyINFO $ALL <nick> <desc><tag>$ $<speed><status>$<email>$<share>$
        match = re.match(r"\$MyINFO \$ALL (\S+) (.+)", line)
        if match:
            nick = match.group(1)
            self.users[nick] = {"raw_info": match.group(2)}
            if self.on_user_join:
                self.on_user_join(nick)

    def _handle_chat(self, line: str) -> None:
        """Handle public chat: <nick> text."""
        match = re.match(r"<([^>]+)>\s?(.*)", line)
        if match:
            nick, text = match.group(1), match.group(2)
            if self.on_chat:
                self.on_chat(nick, text)

    def _handle_pm(self, line: str) -> None:
        """Handle $To: nick From: nick $<nick> text."""
        match = re.match(r"\$To: (\S+) From: (\S+) \$<[^>]+>\s?(.*)", line)
        if match:
            to_nick, from_nick, text = match.group(1), match.group(2), match.group(3)
            if self.on_pm:
                self.on_pm(from_nick, to_nick, text)

    # --- NMDCpb Protocol ---

    async def _handle_pb_envelope(self, env: PbEnvelope) -> None:
        """Handle a decoded PbEnvelope."""
        payload = env.WhichOneof("payload")

        if self.on_pb_message:
            self.on_pb_message(env)

        if payload == "chat":
            nick = env.from_nick
            text = env.chat.text
            if env.chat.is_pm:
                if self.on_pm:
                    self.on_pm(nick, self.nick, text)
            else:
                if self.on_chat:
                    self.on_chat(nick, text)

        elif payload == "pm_key_exchange":
            await self._handle_e2epm_key_exchange(env)

        elif payload == "encrypted_pm":
            self._handle_encrypted_pm(env)

        elif payload == "hub_info":
            log.info(f"PB Hub info: {env.hub_info.name}")

        elif payload == "status":
            msg = f"[{PbStatus.Severity.Name(env.status.severity)}] {env.status.message}"
            if self.on_status:
                self.on_status(msg)

        elif payload == "pm_session_end":
            self.e2epm.handle_session_end(env.from_nick, env.pm_session_end)

        elif payload == "relay_request":
            await self._handle_relay_request(env)

        elif payload == "relay_ack":
            await self._handle_relay_ack(env)

        elif payload == "relay_data":
            await self._handle_relay_data(env.relay_data.relay_id, env.relay_data.data)

        elif payload == "relay_closed":
            self._handle_relay_closed(env)

        elif payload == "relay_status":
            if self.on_relay_status:
                self.on_relay_status(env.relay_status)

        elif payload == "private_search":
            log.debug(f"PrivateSearch from {env.from_nick}: id={env.private_search.search_id}")
            if self.on_private_search:
                self.on_private_search(env.from_nick, env.private_search)

        elif payload == "private_search_result":
            log.debug(f"PrivateSearchResult from {env.from_nick}: id={env.private_search_result.search_id}, "
                       f"{len(env.private_search_result.results)} results")
            if self.on_private_search_result:
                self.on_private_search_result(env.from_nick, env.private_search_result)

    async def _handle_e2epm_key_exchange(self, env: PbEnvelope) -> None:
        """Handle incoming E2EPM key exchange."""
        from_nick = env.from_nick
        kex = env.pm_key_exchange
        response = self.e2epm.handle_key_exchange(from_nick, kex)

        if response is not None:
            # Send our key exchange response
            resp_env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=self.nick,
                to_nick=from_nick,
            )
            resp_env.pm_key_exchange.CopyFrom(response)
            await self._send_pb(resp_env)

        if self.e2epm.has_session(from_nick):
            fp = self.e2epm.get_fingerprint(from_nick)
            log.info(f"E2EPM session established with {from_nick} — fingerprint: {fp}")
            if self.on_e2epm_established:
                self.on_e2epm_established(from_nick, fp)

    def _handle_encrypted_pm(self, env: PbEnvelope) -> None:
        """Handle incoming encrypted PM."""
        from_nick = env.from_nick
        try:
            pt = self.e2epm.decrypt_pm(from_nick, env.encrypted_pm)
            if self.on_encrypted_pm:
                self.on_encrypted_pm(from_nick, pt.text, pt.is_action)
        except KeyError:
            log.warning(f"Encrypted PM from {from_nick} but no session exists")
        except ValueError as e:
            log.warning(f"E2EPM error from {from_nick}: {e}")
        except Exception as e:
            log.error(f"E2EPM decrypt failed from {from_nick}: {e}")

    async def _handle_relay_data(self, relay_id: int, data: bytes) -> None:
        """Handle incoming relay data chunk."""
        sess = self._relay_sessions.get(relay_id)
        if not sess:
            log.warning(f"Relay data for unknown session {relay_id}")
            return
        sess["bytes_received"] = sess.get("bytes_received", 0) + len(data)
        log.debug(f"Relay data: id={relay_id}, {len(data)} bytes (total recv: {sess['bytes_received']})")
        if self.on_relay_data:
            self.on_relay_data(relay_id, data, 0)

    async def _handle_relay_request(self, env: PbEnvelope) -> None:
        """Handle incoming relay session request from another user."""
        req = env.relay_request
        from_nick = env.from_nick
        token = req.token
        purpose = PbRelayRequest.RelayPurpose.Name(req.purpose)
        est_size = req.estimated_size

        log.info(f"Relay request from {from_nick}: token={token}, purpose={purpose}, size={est_size}")

        # Store pending request info (for auto-accept or callback)
        self._pending_relay_tokens[token] = {
            "from_nick": from_nick,
            "purpose": purpose,
            "estimated_size": est_size,
            "peer_pubkey": bytes(req.public_key) if req.public_key else b"",
        }

        if self.on_relay_request:
            self.on_relay_request(from_nick, token, purpose, est_size)

    async def _handle_relay_ack(self, env: PbEnvelope) -> None:
        """Handle relay acknowledgment from peer or hub."""
        ack = env.relay_ack
        token = ack.token
        from_nick = env.from_nick

        pending = self._pending_relay_tokens.pop(token, None)
        if not pending:
            log.warning(f"Relay ack for unknown token {token}")
            return

        if not ack.accepted:
            log.info(f"Relay rejected by {from_nick}: {ack.reject_reason}")
            return

        relay_id = ack.relay_id
        peer_nick = pending.get("target_nick", from_nick)
        purpose = pending.get("purpose", "")

        self._relay_sessions[relay_id] = {
            "token": token,
            "peer_nick": peer_nick,
            "purpose": purpose,
            "established": True,
            "bytes_sent": 0,
            "bytes_received": 0,
            "peer_pubkey": bytes(ack.public_key) if ack.public_key else b"",
        }

        log.info(f"Relay session established: id={relay_id}, peer={peer_nick}")
        if self.on_relay_established:
            self.on_relay_established(relay_id, peer_nick)

    def _handle_relay_closed(self, env: PbEnvelope) -> None:
        """Handle relay session closure notification."""
        rc = env.relay_closed
        relay_id = rc.relay_id
        reason = PbRelayClosed.CloseReason.Name(rc.reason)

        sess = self._relay_sessions.pop(relay_id, None)
        if sess:
            log.info(f"Relay session {relay_id} closed: {reason}")
        else:
            log.debug(f"Relay close for unknown session {relay_id}: {reason}")

        if self.on_relay_closed:
            self.on_relay_closed(relay_id, reason)

    # --- Relay Send Methods ---

    async def request_relay(
        self, target_nick: str, purpose: str = "FILE_TRANSFER",
        estimated_size: int = 0, public_key: bytes = b""
    ) -> str:
        """Request a relay session with a target user.

        Returns the token used for this request.
        """
        import secrets
        token = secrets.token_hex(16)

        self._pending_relay_tokens[token] = {
            "target_nick": target_nick,
            "purpose": purpose,
            "estimated_size": estimated_size,
        }

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=self.nick,
            to_nick=target_nick,
        )

        purpose_enum = PbRelayRequest.RelayPurpose.Value(purpose)
        env.relay_request.target_nick = target_nick
        env.relay_request.token = token
        env.relay_request.purpose = purpose_enum
        env.relay_request.estimated_size = estimated_size
        if public_key:
            env.relay_request.public_key = public_key

        await self._send_pb(env)
        log.info(f"Relay request sent to {target_nick}: token={token}")
        return token

    async def accept_relay(self, token: str, public_key: bytes = b"") -> None:
        """Accept a pending relay request."""
        pending = self._pending_relay_tokens.get(token)
        if not pending:
            log.warning(f"No pending relay request for token {token}")
            return

        from_nick = pending["from_nick"]

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=self.nick,
            to_nick=from_nick,
        )
        env.relay_ack.token = token
        env.relay_ack.accepted = True
        if public_key:
            env.relay_ack.public_key = public_key

        await self._send_pb(env)
        log.info(f"Relay accepted for token {token} from {from_nick}")

    async def reject_relay(self, token: str, reason: str = "") -> None:
        """Reject a pending relay request."""
        pending = self._pending_relay_tokens.pop(token, None)
        if not pending:
            return

        from_nick = pending["from_nick"]

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=self.nick,
            to_nick=from_nick,
        )
        env.relay_ack.token = token
        env.relay_ack.accepted = False
        env.relay_ack.reject_reason = reason

        await self._send_pb(env)
        log.info(f"Relay rejected for token {token}")

    async def send_relay_data(self, relay_id: int, data: bytes, offset: int = 0) -> bool:
        """Send data through an established relay session.

        Returns True if sent, False if session not found.
        """
        sess = self._relay_sessions.get(relay_id)
        if not sess or not sess.get("established"):
            log.warning(f"Cannot send relay data: session {relay_id} not established")
            return False

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=self.nick,
            to_nick=sess["peer_nick"],
        )
        env.relay_data.relay_id = relay_id
        env.relay_data.data = data
        env.relay_data.offset = offset

        await self._send_pb(env)
        sess["bytes_sent"] = sess.get("bytes_sent", 0) + len(data)
        return True

    async def close_relay(self, relay_id: int, reason: str = "NORMAL") -> None:
        """Close a relay session."""
        sess = self._relay_sessions.pop(relay_id, None)
        if not sess:
            return

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=self.nick,
            to_nick=sess["peer_nick"],
        )
        reason_enum = PbRelayClosed.CloseReason.Value(reason)
        env.relay_closed.relay_id = relay_id
        env.relay_closed.reason = reason_enum

        await self._send_pb(env)
        log.info(f"Relay session {relay_id} closed: {reason}")

    # --- Sending ---

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence

    async def _send_pb(self, env: PbEnvelope) -> None:
        """Send a PbEnvelope via appropriate wire command.

        Uses $PBR for DIRECT-routed messages, $PB for everything else.
        """
        if not env.sequence:
            env.sequence = self._next_seq()
        if not env.timestamp:
            env.timestamp = int(time.time() * 1000)

        if env.route == PbEnvelope.DIRECT and env.to_nick:
            wire = WireCodec.encode_routed(env)
        else:
            wire = WireCodec.encode_text(env)

        await self._send_raw(wire)

    async def send_pb_chat(self, text: str, is_action: bool = False) -> None:
        """Send a public chat message via protobuf.

        Falls back to legacy NMDC if hub doesn't support NMDCpb.
        """
        if self._hub_nmdcpb:
            env = WireCodec.make_envelope(
                route=PbEnvelope.BROADCAST,
                from_nick=self.nick,
            )
            env.chat.text = text
            env.chat.is_action = is_action
            await self._send_pb(env)
        else:
            # Fallback to legacy
            if is_action:
                await self._send_raw(f"<{self.nick}> /me {text}|")
            else:
                await self._send_raw(f"<{self.nick}> {text}|")

    async def send_pb_pm(self, target: str, text: str) -> None:
        """Send a private message via protobuf (unencrypted).

        Falls back to legacy NMDC if hub doesn't support NMDCpb.
        """
        if self._hub_nmdcpb:
            env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=self.nick,
                to_nick=target,
            )
            env.chat.text = text
            env.chat.is_pm = True
            env.chat.target_nick = target
            await self._send_pb(env)
        else:
            await self._send_raw(
                f"$To: {target} From: {self.nick} $<{self.nick}> {text}|"
            )

    async def send_encrypted_pm(self, target: str, text: str, is_action: bool = False) -> bool:
        """Send an E2E encrypted PM.

        If no session exists, initiates key exchange first. The message will
        be sent after the key exchange completes (on the next call).

        Returns True if the message was sent, False if key exchange is pending.
        """
        if not self._hub_nmdcpb:
            log.warning("Hub doesn't support NMDCpb — cannot send encrypted PM")
            return False

        if not self.e2epm.has_session(target):
            # Initiate key exchange
            kex = self.e2epm.initiate_session(target)
            env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=self.nick,
                to_nick=target,
            )
            env.pm_key_exchange.CopyFrom(kex)
            await self._send_pb(env)
            log.info(f"E2EPM key exchange initiated with {target}")
            return False  # Caller should retry after session established

        # Encrypt and send
        epm = self.e2epm.encrypt_pm(target, text, is_action)
        if epm:
            env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=self.nick,
                to_nick=target,
            )
            env.encrypted_pm.CopyFrom(epm)
            await self._send_pb(env)
            return True

        return False

    async def send_private_search(
        self,
        target: str,
        query: str = "",
        tth: str = "",
        file_type: int = 0,
        min_size: int = 0,
        max_size: int = 0,
        max_results: int = 10,
        extensions: list[str] | None = None,
    ) -> str:
        """Send a private search to a specific user (invisible to search spy).

        Either ``query`` or ``tth`` must be provided.  Returns the search_id
        which can be correlated with the response callback.
        """
        import uuid
        search_id = uuid.uuid4().hex[:16]

        if not self._hub_nmdcpb:
            log.warning("Hub doesn't support NMDCpb — cannot send private search")
            return ""

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=self.nick,
            to_nick=target,
        )
        ps = env.private_search
        ps.search_id = search_id
        if tth:
            ps.tth = tth
            ps.file_type = PbPrivateSearch.TTH
        else:
            ps.query = query
            ps.file_type = file_type
        ps.min_size = min_size
        ps.max_size = max_size
        ps.max_results = min(max(max_results, 1), 100)
        if extensions:
            ps.extensions.extend(extensions)

        await self._send_pb(env)
        log.info(f"PrivateSearch sent to {target}: id={search_id}, q='{query}', tth='{tth}'")
        return search_id

    async def send_private_search_result(
        self,
        target: str,
        search_id: str,
        results: list[dict],
        is_partial: bool = False,
        error: str = "",
    ) -> None:
        """Send private search results back to a requester.

        Each result dict should have keys: filename, path, size, tth,
        free_slots, total_slots, is_directory.
        """
        if not self._hub_nmdcpb:
            return

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=self.nick,
            to_nick=target,
        )
        psr = env.private_search_result
        psr.search_id = search_id
        psr.is_partial = is_partial
        if error:
            psr.error = error

        for r in results:
            item = psr.results.add()
            item.filename = r.get("filename", "")
            item.path = r.get("path", "")
            item.size = r.get("size", 0)
            item.tth = r.get("tth", "")
            item.free_slots = r.get("free_slots", 0)
            item.total_slots = r.get("total_slots", 0)
            item.is_directory = r.get("is_directory", False)

        await self._send_pb(env)
        log.debug(f"PrivateSearchResult sent to {target}: id={search_id}, {len(results)} results")

    async def send_legacy_chat(self, text: str) -> None:
        """Send a legacy NMDC public chat message."""
        await self._send_raw(f"<{self.nick}> {text}|")

    async def send_legacy_pm(self, target: str, text: str) -> None:
        """Send a legacy NMDC private message."""
        await self._send_raw(
            f"$To: {target} From: {self.nick} $<{self.nick}> {text}|"
        )
