"""
Integration test harness for NMDCpb.

Simulates two NMDCpb clients sending messages through a mock hub relay.
Tests the full message lifecycle: connect → negotiate → chat → E2EPM → disconnect.

Does NOT require a running verlihub instance — uses the hub plugin logic
directly in-process.
"""

import asyncio
import sys
import os
import time
import unittest

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbChat,
    PbStatus,
    PbPMKeyExchange,
    PbEncryptedPM,
    PbPMPlaintext,
    PbPMSessionEnd,
    PbRelayRequest,
    PbRelayAck,
    PbRelayData,
    PbRelayClosed,
    PbRelayStatus,
    PbSegmentRequest,
    PbSegmentInfo,
    PbUserQueryResult,
)
from verlihub.client.nmdcpb.wire import WireCodec, FEATURE_NMDCPB
from verlihub.client.nmdcpb.e2epm import E2EPMManager


class MockHub:
    """Simulates the NMDCpb hub plugin's routing logic.

    Maintains a set of connected users and their mailboxes.
    Includes relay session management for integration testing.
    """

    def __init__(self):
        self.users: dict[str, dict] = {}  # nick → {nmdcpb: bool, inbox: [str]}
        # Relay state
        self._relay_sessions: dict[int, dict] = {}  # relay_id → session info
        self._pending_relay: dict[str, dict] = {}    # token → pending info
        self._next_relay_id: int = 1

    def connect(self, nick: str, nmdcpb: bool = True) -> None:
        self.users[nick] = {"nmdcpb": nmdcpb, "inbox": []}

    def disconnect(self, nick: str) -> None:
        """Disconnect a user and close their relay sessions."""
        # Close relay sessions for this user
        to_close = [rid for rid, s in self._relay_sessions.items()
                     if s["user_a"] == nick or s["user_b"] == nick]
        for rid in to_close:
            sess = self._relay_sessions.pop(rid)
            peer = sess["user_b"] if sess["user_a"] == nick else sess["user_a"]
            # Notify peer
            env = WireCodec.make_envelope(route=PbEnvelope.DIRECT)
            env.relay_closed.relay_id = rid
            env.relay_closed.reason = 4  # USER_DISCONNECT
            wire = WireCodec.encode_text(env)
            self.send_to_user(wire, peer)
        # Clean up pending relays
        to_remove = [t for t, p in self._pending_relay.items()
                     if p.get("from_nick") == nick or p.get("to_nick") == nick]
        for t in to_remove:
            self._pending_relay.pop(t, None)
        self.users.pop(nick, None)

    def send_to_user(self, data: str, nick: str) -> bool:
        if nick in self.users:
            self.users[nick]["inbox"].append(data)
            return True
        return False

    def pop_inbox(self, nick: str) -> list[str]:
        """Pop all messages from a user's inbox."""
        msgs = self.users.get(nick, {}).get("inbox", [])
        if nick in self.users:
            self.users[nick]["inbox"] = []
        return msgs

    def is_nmdcpb_user(self, nick: str) -> bool:
        return self.users.get(nick, {}).get("nmdcpb", False)

    def route_message(self, sender: str, wire: str) -> None:
        """Route a NMDCpb wire message like the hub plugin would."""
        env = WireCodec.decode(wire)
        if env is None:
            return

        env.from_nick = sender  # Hub is authoritative

        route = env.route
        if route == PbEnvelope.BROADCAST:
            # Send to all NMDCpb users except sender
            new_wire = WireCodec.encode_text(env)
            for nick in self.users:
                if nick != sender and self.is_nmdcpb_user(nick):
                    self.send_to_user(new_wire, nick)
            # Translate to legacy for non-NMDCpb users
            payload = env.WhichOneof("payload")
            if payload == "chat":
                text = env.chat.text
                legacy = f"<{sender}> {text}|"
                for nick in self.users:
                    if nick != sender and not self.is_nmdcpb_user(nick):
                        self.send_to_user(legacy, nick)

        elif route == PbEnvelope.DIRECT:
            target = env.to_nick
            payload = env.WhichOneof("payload")

            # Relay message handling
            if payload == "relay_request":
                self._handle_relay_request(sender, env)
                return
            if payload == "relay_ack":
                self._handle_relay_ack(sender, env)
                return
            if payload == "relay_data":
                self._handle_relay_data(sender, env)
                return
            if payload == "relay_closed":
                self._handle_relay_closed(sender, env)
                return
            if payload == "relay_resume":
                self._handle_relay_resume(sender, env)
                return
            if payload == "segment_request":
                self._handle_segment_msg(sender, env)
                return
            if payload == "segment_info":
                self._handle_segment_msg(sender, env)
                return

            if target in self.users:
                new_wire = WireCodec.encode_text(env)
                self.send_to_user(new_wire, target)

        elif route == PbEnvelope.HUB:
            payload = env.WhichOneof("payload")
            if payload == "user_query":
                self._handle_user_query(sender, env)
                return

        elif route == PbEnvelope.ECHO:
            target = env.to_nick
            new_wire = WireCodec.encode_text(env)
            # Send to target
            if target and target in self.users:
                self.send_to_user(new_wire, target)
            # Echo back to sender
            self.send_to_user(new_wire, sender)

    # --- Relay handlers ---

    def _handle_relay_request(self, sender: str, env: PbEnvelope) -> None:
        """Validate and forward relay request to target."""
        req = env.relay_request
        target = req.target_nick or env.to_nick
        token = req.token

        if target not in self.users:
            return
        if not self.is_nmdcpb_user(target):
            return

        self._pending_relay[token] = {
            "from_nick": sender,
            "to_nick": target,
            "pubkey": bytes(req.public_key) if req.public_key else b"",
            "created_at": time.time(),
        }

        env.from_nick = sender
        wire = WireCodec.encode_text(env)
        self.send_to_user(wire, target)

    def _handle_relay_ack(self, sender: str, env: PbEnvelope) -> None:
        """Match token, assign relay_id, create session, notify both."""
        ack = env.relay_ack
        token = ack.token
        pending = self._pending_relay.pop(token, None)
        if not pending:
            return

        requester = pending["from_nick"]

        if not ack.accepted:
            fwd = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT, from_nick=sender, to_nick=requester,
            )
            fwd.relay_ack.token = token
            fwd.relay_ack.accepted = False
            fwd.relay_ack.reject_reason = ack.reject_reason
            self.send_to_user(WireCodec.encode_text(fwd), requester)
            return

        relay_id = self._next_relay_id
        self._next_relay_id += 1

        self._relay_sessions[relay_id] = {
            "user_a": requester,
            "user_b": sender,
            "token": token,
            "bytes_forwarded": 0,
        }

        # Notify requester
        ack_to_req = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick=sender, to_nick=requester,
        )
        ack_to_req.relay_ack.token = token
        ack_to_req.relay_ack.accepted = True
        ack_to_req.relay_ack.relay_id = relay_id
        if ack.public_key:
            ack_to_req.relay_ack.public_key = ack.public_key
        self.send_to_user(WireCodec.encode_text(ack_to_req), requester)

        # Notify responder
        ack_to_resp = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick=requester, to_nick=sender,
        )
        ack_to_resp.relay_ack.token = token
        ack_to_resp.relay_ack.accepted = True
        ack_to_resp.relay_ack.relay_id = relay_id
        if pending.get("pubkey"):
            ack_to_resp.relay_ack.public_key = pending["pubkey"]
        self.send_to_user(WireCodec.encode_text(ack_to_resp), sender)

    def _handle_relay_data(self, sender: str, env: PbEnvelope) -> None:
        """Forward relay data to peer."""
        rd = env.relay_data
        relay_id = rd.relay_id
        sess = self._relay_sessions.get(relay_id)
        if not sess:
            return
        if sender not in (sess["user_a"], sess["user_b"]):
            return

        peer = sess["user_b"] if sess["user_a"] == sender else sess["user_a"]
        sess["bytes_forwarded"] += len(rd.data)

        env.from_nick = sender
        env.to_nick = peer
        wire = WireCodec.encode_text(env)
        self.send_to_user(wire, peer)

    def _handle_relay_closed(self, sender: str, env: PbEnvelope) -> None:
        """Close a relay session and notify both participants."""
        rc = env.relay_closed
        relay_id = rc.relay_id
        sess = self._relay_sessions.pop(relay_id, None)
        if not sess:
            return
        # Notify both
        close_env = WireCodec.make_envelope(route=PbEnvelope.DIRECT)
        close_env.relay_closed.relay_id = relay_id
        close_env.relay_closed.reason = rc.reason
        wire = WireCodec.encode_text(close_env)
        self.send_to_user(wire, sess["user_a"])
        self.send_to_user(wire, sess["user_b"])

    def _handle_relay_resume(self, sender: str, env: PbEnvelope) -> None:
        """Forward relay resume request to the peer."""
        rr = env.relay_resume
        relay_id = rr.relay_id
        sess = self._relay_sessions.get(relay_id)
        if not sess:
            return
        if sender not in (sess["user_a"], sess["user_b"]):
            return
        peer = sess["user_b"] if sess["user_a"] == sender else sess["user_a"]
        env.from_nick = sender
        env.to_nick = peer
        wire = WireCodec.encode_text(env)
        self.send_to_user(wire, peer)

    def _handle_segment_msg(self, sender: str, env: PbEnvelope) -> None:
        """Forward segment_request / segment_info to the target peer."""
        target = env.to_nick
        if target not in self.users:
            return
        if not self.is_nmdcpb_user(target):
            return
        env.from_nick = sender
        wire = WireCodec.encode_text(env)
        self.send_to_user(wire, target)

    def _handle_user_query(self, sender: str, env: PbEnvelope) -> None:
        """Process a PbUserQuery: filter users and optionally sweep search."""
        uq = env.user_query
        query_id = uq.query_id
        max_results = uq.max_results or 100

        # Find matching NMDCpb users (exclude sender)
        matching = [nick for nick in self.users
                    if nick != sender and self.is_nmdcpb_user(nick)]

        # Feature filter (simplified — all NMDCpb users pass for now)
        if uq.feature_filter:
            matching = [n for n in matching if self.is_nmdcpb_user(n)]

        result_nicks = matching[:max_results]

        # Build response
        resp = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="", to_nick=sender,
        )
        resp.user_query_result.query_id = query_id
        resp.user_query_result.nicks.extend(result_nicks)
        resp.user_query_result.total_matching = len(matching)

        # Sweep: forward PbPrivateSearch to each match
        sweep_count = 0
        if uq.sweep and uq.HasField("search"):
            for target_nick in matching[:50]:
                search_env = WireCodec.make_envelope(
                    route=PbEnvelope.DIRECT,
                    from_nick=sender,
                    to_nick=target_nick,
                )
                search_env.private_search.CopyFrom(uq.search)
                wire = WireCodec.encode_text(search_env)
                self.send_to_user(wire, target_nick)
                sweep_count += 1
            resp.user_query_result.sweep_started = True
            resp.user_query_result.sweep_count = sweep_count

        self.send_to_user(WireCodec.encode_text(resp), sender)

class TestHubRoutingIntegration(unittest.TestCase):
    """Test NMDCpb message routing through the mock hub."""

    def setUp(self):
        self.hub = MockHub()
        self.hub.connect("Alice", nmdcpb=True)
        self.hub.connect("Bob", nmdcpb=True)

    def test_broadcast_chat(self):
        """Alice sends broadcast chat — Bob receives."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.BROADCAST,
            from_nick="Alice",
        )
        env.chat.text = "Hello everyone!"
        wire = WireCodec.encode_text(env)

        self.hub.route_message("Alice", wire)

        # Bob should have the message
        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 1)
        decoded = WireCodec.decode(bob_msgs[0])
        self.assertEqual(decoded.from_nick, "Alice")
        self.assertEqual(decoded.chat.text, "Hello everyone!")

        # Alice should NOT get her own message
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 0)

    def test_broadcast_with_legacy_user(self):
        """Broadcast reaches NMDCpb users as PB and legacy users as text."""
        self.hub.connect("Legacy", nmdcpb=False)

        env = WireCodec.make_envelope(
            route=PbEnvelope.BROADCAST,
            from_nick="Alice",
        )
        env.chat.text = "Mixed mode test"
        wire = WireCodec.encode_text(env)

        self.hub.route_message("Alice", wire)

        # Bob (NMDCpb) gets protobuf
        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 1)
        self.assertTrue(bob_msgs[0].startswith("$PB "))

        # Legacy gets translated text
        legacy_msgs = self.hub.pop_inbox("Legacy")
        self.assertEqual(len(legacy_msgs), 1)
        self.assertEqual(legacy_msgs[0], "<Alice> Mixed mode test|")

    def test_direct_message(self):
        """Alice sends direct PM to Bob."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="Bob",
        )
        env.chat.text = "Private message"
        env.chat.is_pm = True
        wire = WireCodec.encode_routed(env)

        self.hub.route_message("Alice", wire)

        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 1)
        decoded = WireCodec.decode(bob_msgs[0])
        self.assertEqual(decoded.from_nick, "Alice")
        self.assertEqual(decoded.chat.text, "Private message")

        # Alice should NOT get it
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 0)

    def test_echo_route(self):
        """ECHO sends to target + echoes back to sender (ADC E-type)."""
        # Add a third user to verify they do NOT receive the echo
        self.hub.connect("Carol", nmdcpb=True)

        env = WireCodec.make_envelope(
            route=PbEnvelope.ECHO,
            from_nick="Alice",
            to_nick="Bob",
        )
        env.chat.text = "Echo test"
        wire = WireCodec.encode_text(env)

        self.hub.route_message("Alice", wire)

        # Alice (sender) should get the echo back
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 1)

        # Bob (target) should receive the message
        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 1)

        # Carol (bystander) should NOT receive
        carol_msgs = self.hub.pop_inbox("Carol")
        self.assertEqual(len(carol_msgs), 0)

    def test_direct_to_nonexistent_user(self):
        """Direct message to offline user — silently dropped."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="OfflineUser",
        )
        env.chat.text = "Hello?"
        wire = WireCodec.encode_routed(env)

        self.hub.route_message("Alice", wire)
        # No crash, message just not delivered

    def test_multiple_users_broadcast(self):
        """Broadcast to 3 NMDCpb users."""
        self.hub.connect("Charlie", nmdcpb=True)

        env = WireCodec.make_envelope(
            route=PbEnvelope.BROADCAST,
            from_nick="Alice",
        )
        env.chat.text = "Three-way"
        wire = WireCodec.encode_text(env)

        self.hub.route_message("Alice", wire)

        bob_msgs = self.hub.pop_inbox("Bob")
        charlie_msgs = self.hub.pop_inbox("Charlie")
        self.assertEqual(len(bob_msgs), 1)
        self.assertEqual(len(charlie_msgs), 1)


class TestE2EPMIntegration(unittest.TestCase):
    """End-to-end E2EPM through the mock hub."""

    def setUp(self):
        self.hub = MockHub()
        self.hub.connect("Alice", nmdcpb=True)
        self.hub.connect("Bob", nmdcpb=True)
        self.alice_mgr = E2EPMManager("Alice")
        self.bob_mgr = E2EPMManager("Bob")

    def _send_via_hub(self, sender: str, env: PbEnvelope) -> None:
        if env.route == PbEnvelope.DIRECT and env.to_nick:
            wire = WireCodec.encode_routed(env)
        else:
            wire = WireCodec.encode_text(env)
        self.hub.route_message(sender, wire)

    def _recv_one(self, nick: str) -> PbEnvelope:
        msgs = self.hub.pop_inbox(nick)
        self.assertEqual(len(msgs), 1, f"Expected 1 msg for {nick}, got {len(msgs)}")
        return WireCodec.decode(msgs[0])

    def test_full_e2epm_flow(self):
        """Full key exchange → encrypt → decrypt → close through hub."""
        # 1. Alice initiates key exchange
        kex_a = self.alice_mgr.initiate_session("Bob")
        env_kex = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="Bob",
        )
        env_kex.pm_key_exchange.CopyFrom(kex_a)
        self._send_via_hub("Alice", env_kex)

        # 2. Bob receives key exchange
        msg = self._recv_one("Bob")
        self.assertEqual(msg.from_nick, "Alice")
        resp = self.bob_mgr.handle_key_exchange("Alice", msg.pm_key_exchange)
        self.assertIsNotNone(resp)

        # 3. Bob sends response
        env_resp = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Bob",
            to_nick="Alice",
        )
        env_resp.pm_key_exchange.CopyFrom(resp)
        self._send_via_hub("Bob", env_resp)

        # 4. Alice completes exchange
        msg2 = self._recv_one("Alice")
        self.assertIsNone(self.alice_mgr.handle_key_exchange("Bob", msg2.pm_key_exchange))

        # Both have sessions
        self.assertTrue(self.alice_mgr.has_session("Bob"))
        self.assertTrue(self.bob_mgr.has_session("Alice"))

        # Fingerprints match
        self.assertEqual(
            self.alice_mgr.get_fingerprint("Bob"),
            self.bob_mgr.get_fingerprint("Alice"),
        )

        # 5. Alice sends encrypted PM
        epm = self.alice_mgr.encrypt_pm("Bob", "Top secret!")
        env_epm = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="Bob",
        )
        env_epm.encrypted_pm.CopyFrom(epm)
        self._send_via_hub("Alice", env_epm)

        # 6. Bob decrypts
        msg3 = self._recv_one("Bob")
        pt = self.bob_mgr.decrypt_pm("Alice", msg3.encrypted_pm)
        self.assertEqual(pt.text, "Top secret!")

        # 7. Bob replies
        epm2 = self.bob_mgr.encrypt_pm("Alice", "Got it, thanks!")
        env_epm2 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Bob",
            to_nick="Alice",
        )
        env_epm2.encrypted_pm.CopyFrom(epm2)
        self._send_via_hub("Bob", env_epm2)

        msg4 = self._recv_one("Alice")
        pt2 = self.alice_mgr.decrypt_pm("Bob", msg4.encrypted_pm)
        self.assertEqual(pt2.text, "Got it, thanks!")

        # 8. Alice closes session
        end = self.alice_mgr.close_session("Bob")
        env_end = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="Bob",
        )
        env_end.pm_session_end.CopyFrom(end)
        self._send_via_hub("Alice", env_end)

        msg5 = self._recv_one("Bob")
        self.bob_mgr.handle_session_end("Alice", msg5.pm_session_end)
        self.assertFalse(self.alice_mgr.has_session("Bob"))
        self.assertFalse(self.bob_mgr.has_session("Alice"))

    def test_hub_opaque_forwarding(self):
        """Hub forwards encrypted PM without seeing plaintext."""
        # Establish session
        kex_a = self.alice_mgr.initiate_session("Bob")
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
        )
        env.pm_key_exchange.CopyFrom(kex_a)
        self._send_via_hub("Alice", env)

        msg = self._recv_one("Bob")
        resp = self.bob_mgr.handle_key_exchange("Alice", msg.pm_key_exchange)
        env2 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Bob", to_nick="Alice",
        )
        env2.pm_key_exchange.CopyFrom(resp)
        self._send_via_hub("Bob", env2)
        msg2 = self._recv_one("Alice")
        self.alice_mgr.handle_key_exchange("Bob", msg2.pm_key_exchange)

        # Send encrypted PM
        epm = self.alice_mgr.encrypt_pm("Bob", "Hub can't read this")
        env3 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
        )
        env3.encrypted_pm.CopyFrom(epm)
        self._send_via_hub("Alice", env3)

        # Verify the hub's copy of the wire data has only ciphertext
        bob_msgs = self.hub.pop_inbox("Bob")
        wire = bob_msgs[0]
        decoded_at_hub = WireCodec.decode(wire)
        # The hub sees encrypted_pm but has no keys — it can't decrypt
        self.assertEqual(decoded_at_hub.WhichOneof("payload"), "encrypted_pm")
        self.assertGreater(len(decoded_at_hub.encrypted_pm.ciphertext), 0)
        # Plaintext is NOT in the wire
        self.assertNotIn(b"Hub can't read this", wire.encode("utf-8"))

    def test_three_user_e2epm(self):
        """Alice has E2EPM sessions with both Bob and Charlie."""
        self.hub.connect("Charlie", nmdcpb=True)
        charlie_mgr = E2EPMManager("Charlie")

        # Alice ↔ Bob
        kex_ab = self.alice_mgr.initiate_session("Bob")
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
        )
        env.pm_key_exchange.CopyFrom(kex_ab)
        self._send_via_hub("Alice", env)
        msg = self._recv_one("Bob")
        resp = self.bob_mgr.handle_key_exchange("Alice", msg.pm_key_exchange)
        env2 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Bob", to_nick="Alice",
        )
        env2.pm_key_exchange.CopyFrom(resp)
        self._send_via_hub("Bob", env2)
        msg2 = self._recv_one("Alice")
        self.alice_mgr.handle_key_exchange("Bob", msg2.pm_key_exchange)

        # Alice ↔ Charlie
        kex_ac = self.alice_mgr.initiate_session("Charlie")
        env3 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Charlie",
        )
        env3.pm_key_exchange.CopyFrom(kex_ac)
        self._send_via_hub("Alice", env3)
        msg3 = self._recv_one("Charlie")
        resp2 = charlie_mgr.handle_key_exchange("Alice", msg3.pm_key_exchange)
        env4 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Charlie", to_nick="Alice",
        )
        env4.pm_key_exchange.CopyFrom(resp2)
        self._send_via_hub("Charlie", env4)
        msg4 = self._recv_one("Alice")
        self.alice_mgr.handle_key_exchange("Charlie", msg4.pm_key_exchange)

        # All sessions established
        self.assertTrue(self.alice_mgr.has_session("Bob"))
        self.assertTrue(self.alice_mgr.has_session("Charlie"))

        # Independent messages
        epm_b = self.alice_mgr.encrypt_pm("Bob", "For Bob only")
        env5 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
        )
        env5.encrypted_pm.CopyFrom(epm_b)
        self._send_via_hub("Alice", env5)
        bob_msg = self._recv_one("Bob")
        pt_b = self.bob_mgr.decrypt_pm("Alice", bob_msg.encrypted_pm)
        self.assertEqual(pt_b.text, "For Bob only")

        epm_c = self.alice_mgr.encrypt_pm("Charlie", "For Charlie only")
        env6 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Charlie",
        )
        env6.encrypted_pm.CopyFrom(epm_c)
        self._send_via_hub("Alice", env6)
        charlie_msg = self._recv_one("Charlie")
        pt_c = charlie_mgr.decrypt_pm("Alice", charlie_msg.encrypted_pm)
        self.assertEqual(pt_c.text, "For Charlie only")


# ============================================================================
# Rate limiter unit tests
# ============================================================================

class TestRateLimiter(unittest.TestCase):
    """Test the _RateBucket and _check_rate logic."""

    def setUp(self):
        # Import rate limiter internals
        from verlihub.client.nmdcpb import hub_plugin
        self._module = hub_plugin
        # Save and reset rate state
        self._saved_pb = dict(hub_plugin._rate_pb)
        self._saved_e2epm = dict(hub_plugin._rate_e2epm)
        self._saved_stats = dict(hub_plugin._stats)
        hub_plugin._rate_pb.clear()
        hub_plugin._rate_e2epm.clear()
        # Reset stats
        hub_plugin._stats["rate_limited"] = 0
        hub_plugin._stats["flood_mutes"] = 0
        # Ensure test user is registered as PB user
        hub_plugin._pb_users["RateTester"] = {"NMDCpb"}

    def tearDown(self):
        self._module._rate_pb.clear()
        self._module._rate_e2epm.clear()
        self._module._rate_pb.update(self._saved_pb)
        self._module._rate_e2epm.update(self._saved_e2epm)
        self._module._stats.update(self._saved_stats)
        self._module._pb_users.pop("RateTester", None)

    def test_bucket_allows_under_limit(self):
        from verlihub.client.nmdcpb.hub_plugin import _RateBucket
        b = _RateBucket()
        now = time.time()
        for _ in range(5):
            self.assertTrue(b.allow(now, 10.0, 10))

    def test_bucket_blocks_at_limit(self):
        from verlihub.client.nmdcpb.hub_plugin import _RateBucket
        b = _RateBucket()
        now = time.time()
        for _ in range(10):
            b.allow(now, 10.0, 10)
        self.assertFalse(b.allow(now, 10.0, 10))

    def test_bucket_window_expiry(self):
        from verlihub.client.nmdcpb.hub_plugin import _RateBucket
        b = _RateBucket()
        past = time.time() - 20.0  # 20s ago
        for _ in range(10):
            b.timestamps.append(past)
        # Now should be allowed because old timestamps are expired
        self.assertTrue(b.allow(time.time(), 10.0, 10))

    def test_bucket_mute(self):
        from verlihub.client.nmdcpb.hub_plugin import _RateBucket
        b = _RateBucket()
        now = time.time()
        b.mute(now, 60.0)
        self.assertFalse(b.allow(now + 1.0, 10.0, 100))
        # After mute duration, allowed again
        self.assertTrue(b.allow(now + 61.0, 10.0, 100))

    def test_bucket_is_idle(self):
        from verlihub.client.nmdcpb.hub_plugin import _RateBucket
        b = _RateBucket()
        now = time.time()
        self.assertTrue(b.is_idle(now, 300))
        b.timestamps.append(now)
        self.assertFalse(b.is_idle(now, 300))
        self.assertTrue(b.is_idle(now + 301, 300))

    def test_check_rate_allows_normal_traffic(self):
        m = self._module
        for _ in range(m.RATE_MAX_MESSAGES - 1):
            self.assertTrue(m._check_rate("RateTester", "pb"))
        self.assertEqual(m._stats["rate_limited"], 0)

    def test_check_rate_blocks_excess(self):
        m = self._module
        for _ in range(m.RATE_MAX_MESSAGES):
            m._check_rate("RateTester", "pb")
        self.assertFalse(m._check_rate("RateTester", "pb"))
        self.assertGreater(m._stats["rate_limited"], 0)

    def test_e2epm_rate_separate(self):
        m = self._module
        # Fill PB bucket
        for _ in range(m.RATE_MAX_MESSAGES):
            m._check_rate("RateTester", "pb")
        # E2EPM should still be allowed (separate bucket)
        self.assertTrue(m._check_rate("RateTester", "e2epm"))


# ============================================================================
# Relay session management unit tests (hub_plugin internals)
# ============================================================================

class TestRelaySessionUnit(unittest.TestCase):
    """Unit tests for hub_plugin relay session management."""

    def setUp(self):
        from verlihub.client.nmdcpb import hub_plugin
        self._mod = hub_plugin
        # Save state
        self._saved_sessions = dict(hub_plugin._relay_sessions)
        self._saved_pending = dict(hub_plugin._pending_relay)
        self._saved_next_id = hub_plugin._next_relay_id
        self._saved_stats = dict(hub_plugin._stats)
        self._saved_users = dict(hub_plugin._pb_users)
        # Reset
        hub_plugin._relay_sessions.clear()
        hub_plugin._pending_relay.clear()
        hub_plugin._next_relay_id = 1
        hub_plugin._stats["relay_sessions_created"] = 0
        hub_plugin._stats["relay_sessions_closed"] = 0
        hub_plugin._stats["relay_bytes_forwarded"] = 0
        # Register test users
        hub_plugin._pb_users["Alice"] = {"NMDCpb"}
        hub_plugin._pb_users["Bob"] = {"NMDCpb"}
        hub_plugin._pb_users["Carol"] = {"NMDCpb"}

    def tearDown(self):
        m = self._mod
        m._relay_sessions.clear()
        m._relay_sessions.update(self._saved_sessions)
        m._pending_relay.clear()
        m._pending_relay.update(self._saved_pending)
        m._next_relay_id = self._saved_next_id
        m._stats.update(self._saved_stats)
        m._pb_users.clear()
        m._pb_users.update(self._saved_users)

    def test_relay_session_class(self):
        """Test _RelaySession basic operations."""
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        s = _RelaySession(1, "Alice", "Bob", "tok123")
        self.assertEqual(s.relay_id, 1)
        self.assertEqual(s.user_a, "Alice")
        self.assertEqual(s.user_b, "Bob")
        self.assertEqual(s.token, "tok123")
        self.assertEqual(s.bytes_forwarded, 0)
        self.assertEqual(s.peer_of("Alice"), "Bob")
        self.assertEqual(s.peer_of("Bob"), "Alice")
        self.assertEqual(s.peer_of("Carol"), "")
        self.assertTrue(s.touches("Alice"))
        self.assertTrue(s.touches("Bob"))
        self.assertFalse(s.touches("Carol"))
        self.assertFalse(s.is_idle(time.time()))

    def test_relay_session_idle(self):
        """Test _RelaySession idle detection."""
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession, RELAY_IDLE_TIMEOUT_SEC
        s = _RelaySession(1, "Alice", "Bob", "tok")
        s.last_activity = time.time() - RELAY_IDLE_TIMEOUT_SEC - 1
        self.assertTrue(s.is_idle(time.time()))

    def test_user_relay_count(self):
        """Test _user_relay_count helper."""
        m = self._mod
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        m._relay_sessions[1] = _RelaySession(1, "Alice", "Bob", "t1")
        m._relay_sessions[2] = _RelaySession(2, "Alice", "Carol", "t2")
        self.assertEqual(m._user_relay_count("Alice"), 2)
        self.assertEqual(m._user_relay_count("Bob"), 1)
        self.assertEqual(m._user_relay_count("Carol"), 1)
        self.assertEqual(m._user_relay_count("Dave"), 0)

    def test_close_relay_session(self):
        """Test _close_relay_session removes session and bumps stats."""
        m = self._mod
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        m._relay_sessions[1] = _RelaySession(1, "Alice", "Bob", "t1")
        m._close_relay_session(1, reason=0, notify=False)
        self.assertNotIn(1, m._relay_sessions)
        self.assertEqual(m._stats["relay_sessions_closed"], 1)

    def test_close_relay_session_idempotent(self):
        """Closing a nonexistent session is a no-op."""
        m = self._mod
        m._close_relay_session(999, reason=0, notify=False)
        self.assertEqual(m._stats["relay_sessions_closed"], 0)

    def test_close_user_relays(self):
        """Test _close_user_relays closes all sessions for a user."""
        m = self._mod
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        m._relay_sessions[1] = _RelaySession(1, "Alice", "Bob", "t1")
        m._relay_sessions[2] = _RelaySession(2, "Alice", "Carol", "t2")
        m._relay_sessions[3] = _RelaySession(3, "Bob", "Carol", "t3")
        m._pending_relay["pend1"] = {"from_nick": "Alice", "to_nick": "Bob"}
        m._close_user_relays("Alice", reason=4)
        # Sessions 1 and 2 closed, 3 remains
        self.assertNotIn(1, m._relay_sessions)
        self.assertNotIn(2, m._relay_sessions)
        self.assertIn(3, m._relay_sessions)
        self.assertEqual(m._stats["relay_sessions_closed"], 2)
        # Pending relay also cleaned up
        self.assertNotIn("pend1", m._pending_relay)


# ============================================================================
# Relay integration tests (MockHub with multiple clients)
# ============================================================================

class TestRelayIntegration(unittest.TestCase):
    """Integration tests for relay session lifecycle through MockHub."""

    def setUp(self):
        self.hub = MockHub()
        self.hub.connect("Alice", nmdcpb=True)
        self.hub.connect("Bob", nmdcpb=True)

    def _make_relay_request(self, sender: str, target: str,
                             token: str = "test_token",
                             purpose: int = 0,
                             est_size: int = 1024) -> str:
        """Build and return a relay request wire message."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick=target,
        )
        env.relay_request.target_nick = target
        env.relay_request.token = token
        env.relay_request.purpose = purpose
        env.relay_request.estimated_size = est_size
        return WireCodec.encode_text(env)

    def _make_relay_ack(self, sender: str, target: str,
                         token: str, accepted: bool = True,
                         reject_reason: str = "") -> str:
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick=target,
        )
        env.relay_ack.token = token
        env.relay_ack.accepted = accepted
        env.relay_ack.reject_reason = reject_reason
        return WireCodec.encode_text(env)

    def _make_relay_data(self, sender: str, target: str,
                          relay_id: int, data: bytes,
                          offset: int = 0) -> str:
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick=target,
        )
        env.relay_data.relay_id = relay_id
        env.relay_data.data = data
        env.relay_data.offset = offset
        return WireCodec.encode_text(env)

    def _make_relay_close(self, sender: str, target: str,
                           relay_id: int, reason: int = 0) -> str:
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick=target,
        )
        env.relay_closed.relay_id = relay_id
        env.relay_closed.reason = reason
        return WireCodec.encode_text(env)

    def _establish_relay(self, requester: str = "Alice",
                          responder: str = "Bob",
                          token: str = "tok1") -> int:
        """Full relay handshake, returns relay_id."""
        # Request
        wire = self._make_relay_request(requester, responder, token=token)
        self.hub.route_message(requester, wire)
        # Bob receives request
        bob_msgs = self.hub.pop_inbox(responder)
        self.assertEqual(len(bob_msgs), 1)
        # Accept
        wire_ack = self._make_relay_ack(responder, requester, token=token)
        self.hub.route_message(responder, wire_ack)
        # Both receive ack with relay_id
        req_msgs = self.hub.pop_inbox(requester)
        resp_msgs = self.hub.pop_inbox(responder)
        self.assertEqual(len(req_msgs), 1)
        self.assertEqual(len(resp_msgs), 1)
        req_env = WireCodec.decode(req_msgs[0])
        resp_env = WireCodec.decode(resp_msgs[0])
        self.assertTrue(req_env.relay_ack.accepted)
        self.assertTrue(resp_env.relay_ack.accepted)
        relay_id = req_env.relay_ack.relay_id
        self.assertEqual(relay_id, resp_env.relay_ack.relay_id)
        self.assertGreater(relay_id, 0)
        return relay_id

    # --- Tests ---

    def test_relay_request_forwarded(self):
        """Relay request from Alice is forwarded to Bob."""
        wire = self._make_relay_request("Alice", "Bob", token="abc123")
        self.hub.route_message("Alice", wire)

        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 1)
        env = WireCodec.decode(bob_msgs[0])
        self.assertEqual(env.WhichOneof("payload"), "relay_request")
        self.assertEqual(env.relay_request.token, "abc123")
        self.assertEqual(env.from_nick, "Alice")

        # Alice should NOT get her own request
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 0)

    def test_relay_request_to_offline_user(self):
        """Relay request to offline user is silently dropped."""
        wire = self._make_relay_request("Alice", "OfflineUser", token="t1")
        self.hub.route_message("Alice", wire)
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 0)

    def test_relay_full_handshake(self):
        """Full relay handshake: request → accept → both get relay_id."""
        relay_id = self._establish_relay()
        self.assertEqual(relay_id, 1)
        self.assertIn(relay_id, self.hub._relay_sessions)

    def test_relay_rejection(self):
        """Bob rejects relay request, Alice gets rejection."""
        wire = self._make_relay_request("Alice", "Bob", token="rej_tok")
        self.hub.route_message("Alice", wire)
        self.hub.pop_inbox("Bob")  # Bob receives request

        # Bob rejects
        wire_ack = self._make_relay_ack("Bob", "Alice", "rej_tok",
                                         accepted=False,
                                         reject_reason="busy")
        self.hub.route_message("Bob", wire_ack)

        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 1)
        env = WireCodec.decode(alice_msgs[0])
        self.assertFalse(env.relay_ack.accepted)
        self.assertEqual(env.relay_ack.reject_reason, "busy")

        # No session created
        self.assertEqual(len(self.hub._relay_sessions), 0)

    def test_relay_data_forwarding(self):
        """Data sent by Alice is forwarded to Bob through relay."""
        relay_id = self._establish_relay()

        # Alice sends data
        wire = self._make_relay_data("Alice", "Bob", relay_id, b"Hello relay!")
        self.hub.route_message("Alice", wire)

        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 1)
        env = WireCodec.decode(bob_msgs[0])
        self.assertEqual(env.relay_data.data, b"Hello relay!")
        self.assertEqual(env.relay_data.relay_id, relay_id)

        # Alice should NOT get her own data
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 0)

    def test_relay_bidirectional_data(self):
        """Both Alice and Bob can send data through the relay."""
        relay_id = self._establish_relay()

        # Alice → Bob
        wire1 = self._make_relay_data("Alice", "Bob", relay_id, b"from_alice")
        self.hub.route_message("Alice", wire1)
        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 1)
        env1 = WireCodec.decode(bob_msgs[0])
        self.assertEqual(env1.relay_data.data, b"from_alice")

        # Bob → Alice
        wire2 = self._make_relay_data("Bob", "Alice", relay_id, b"from_bob")
        self.hub.route_message("Bob", wire2)
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 1)
        env2 = WireCodec.decode(alice_msgs[0])
        self.assertEqual(env2.relay_data.data, b"from_bob")

    def test_relay_data_tracked_bytes(self):
        """Hub tracks bytes forwarded per relay session."""
        relay_id = self._establish_relay()

        wire = self._make_relay_data("Alice", "Bob", relay_id, b"x" * 100)
        self.hub.route_message("Alice", wire)
        wire2 = self._make_relay_data("Bob", "Alice", relay_id, b"y" * 200)
        self.hub.route_message("Bob", wire2)

        self.assertEqual(self.hub._relay_sessions[relay_id]["bytes_forwarded"], 300)

    def test_relay_data_to_nonexistent_session(self):
        """Data to unknown relay_id is silently dropped."""
        wire = self._make_relay_data("Alice", "Bob", 999, b"orphan data")
        self.hub.route_message("Alice", wire)
        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 0)

    def test_relay_data_from_non_participant(self):
        """Data from non-participant is silently dropped."""
        relay_id = self._establish_relay()
        self.hub.connect("Carol", nmdcpb=True)

        wire = self._make_relay_data("Carol", "Bob", relay_id, b"intruder")
        self.hub.route_message("Carol", wire)
        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 0)

    def test_relay_close_by_sender(self):
        """Alice closes the relay, both are notified."""
        relay_id = self._establish_relay()

        wire = self._make_relay_close("Alice", "Bob", relay_id)
        self.hub.route_message("Alice", wire)

        alice_msgs = self.hub.pop_inbox("Alice")
        bob_msgs = self.hub.pop_inbox("Bob")
        # Both should get relay_closed
        self.assertEqual(len(alice_msgs), 1)
        self.assertEqual(len(bob_msgs), 1)
        env_a = WireCodec.decode(alice_msgs[0])
        env_b = WireCodec.decode(bob_msgs[0])
        self.assertEqual(env_a.relay_closed.relay_id, relay_id)
        self.assertEqual(env_b.relay_closed.relay_id, relay_id)

        # Session removed
        self.assertNotIn(relay_id, self.hub._relay_sessions)

    def test_relay_close_idempotent(self):
        """Closing an already-closed relay is a no-op."""
        relay_id = self._establish_relay()
        wire = self._make_relay_close("Alice", "Bob", relay_id)
        self.hub.route_message("Alice", wire)
        self.hub.pop_inbox("Alice")
        self.hub.pop_inbox("Bob")

        # Close again — should not crash
        self.hub.route_message("Alice", wire)

    def test_relay_disconnect_cleanup(self):
        """Disconnecting a user closes their relay sessions."""
        relay_id = self._establish_relay()

        self.hub.disconnect("Alice")

        # Relay session removed
        self.assertNotIn(relay_id, self.hub._relay_sessions)

        # Bob notified of closure
        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(bob_msgs), 1)
        env = WireCodec.decode(bob_msgs[0])
        self.assertEqual(env.relay_closed.relay_id, relay_id)
        self.assertEqual(env.relay_closed.reason, 4)  # USER_DISCONNECT

    def test_multiple_relay_sessions(self):
        """Multiple relay sessions can coexist."""
        self.hub.connect("Carol", nmdcpb=True)

        rid1 = self._establish_relay("Alice", "Bob", token="t1")
        rid2 = self._establish_relay("Alice", "Carol", token="t2")
        rid3 = self._establish_relay("Bob", "Carol", token="t3")

        self.assertNotEqual(rid1, rid2)
        self.assertNotEqual(rid2, rid3)
        self.assertEqual(len(self.hub._relay_sessions), 3)

        # Data through each session independently
        self.hub.route_message("Alice",
            self._make_relay_data("Alice", "Bob", rid1, b"data1"))
        self.hub.route_message("Alice",
            self._make_relay_data("Alice", "Carol", rid2, b"data2"))

        bob_msgs = self.hub.pop_inbox("Bob")
        carol_msgs = self.hub.pop_inbox("Carol")
        self.assertEqual(len(bob_msgs), 1)
        self.assertEqual(len(carol_msgs), 1)
        self.assertEqual(WireCodec.decode(bob_msgs[0]).relay_data.data, b"data1")
        self.assertEqual(WireCodec.decode(carol_msgs[0]).relay_data.data, b"data2")

    def test_relay_disconnect_partial_cleanup(self):
        """Disconnecting one user closes only their sessions."""
        self.hub.connect("Carol", nmdcpb=True)

        rid1 = self._establish_relay("Alice", "Bob", token="t1")
        rid2 = self._establish_relay("Bob", "Carol", token="t2")

        self.hub.disconnect("Alice")

        # rid1 closed, rid2 still active
        self.assertNotIn(rid1, self.hub._relay_sessions)
        self.assertIn(rid2, self.hub._relay_sessions)

    def test_relay_large_data_transfer(self):
        """Transfer multiple chunks of data through relay."""
        relay_id = self._establish_relay()
        chunk_size = 32768
        total_chunks = 5
        received_data = b""

        for i in range(total_chunks):
            chunk = bytes([i % 256]) * chunk_size
            wire = self._make_relay_data("Alice", "Bob", relay_id,
                                          chunk, offset=i * chunk_size)
            self.hub.route_message("Alice", wire)

            bob_msgs = self.hub.pop_inbox("Bob")
            self.assertEqual(len(bob_msgs), 1)
            env = WireCodec.decode(bob_msgs[0])
            received_data += env.relay_data.data
            self.assertEqual(env.relay_data.offset, i * chunk_size)

        self.assertEqual(len(received_data), chunk_size * total_chunks)
        self.assertEqual(
            self.hub._relay_sessions[relay_id]["bytes_forwarded"],
            chunk_size * total_chunks,
        )

    def test_relay_with_public_keys(self):
        """Relay handshake exchanges public keys."""
        # Request with public key
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
        )
        env.relay_request.target_nick = "Bob"
        env.relay_request.token = "keytok"
        env.relay_request.public_key = b"alice_pubkey_32bytes_padding_pad"
        wire = WireCodec.encode_text(env)
        self.hub.route_message("Alice", wire)

        # Bob gets the request with Alice's key
        bob_msgs = self.hub.pop_inbox("Bob")
        req_env = WireCodec.decode(bob_msgs[0])
        self.assertEqual(req_env.relay_request.public_key,
                         b"alice_pubkey_32bytes_padding_pad")

        # Bob accepts with own key
        ack_env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Bob", to_nick="Alice",
        )
        ack_env.relay_ack.token = "keytok"
        ack_env.relay_ack.accepted = True
        ack_env.relay_ack.public_key = b"bob_public_key_32bytes_padding!!"
        self.hub.route_message("Bob", WireCodec.encode_text(ack_env))

        # Alice gets Bob's key, Bob gets Alice's key
        alice_msgs = self.hub.pop_inbox("Alice")
        bob_msgs = self.hub.pop_inbox("Bob")
        a_env = WireCodec.decode(alice_msgs[0])
        b_env = WireCodec.decode(bob_msgs[0])
        self.assertEqual(a_env.relay_ack.public_key,
                         b"bob_public_key_32bytes_padding!!")
        self.assertEqual(b_env.relay_ack.public_key,
                         b"alice_pubkey_32bytes_padding_pad")

    def test_pending_relay_cleanup_on_disconnect(self):
        """Pending (unaccepted) relay requests are cleaned on disconnect."""
        wire = self._make_relay_request("Alice", "Bob", token="pend_tok")
        self.hub.route_message("Alice", wire)
        self.hub.pop_inbox("Bob")

        # Alice disconnects before Bob accepts
        self.hub.disconnect("Alice")
        self.assertNotIn("pend_tok", self.hub._pending_relay)

    def test_relay_ack_unknown_token(self):
        """Ack for unknown token is silently dropped."""
        wire = self._make_relay_ack("Bob", "Alice", "unknown_token")
        self.hub.route_message("Bob", wire)
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 0)

    def test_relay_resume_forwarded(self):
        """Resume request from Bob is forwarded to Alice through relay."""
        relay_id = self._establish_relay()

        # Send some data first
        wire = self._make_relay_data("Alice", "Bob", relay_id, b"x" * 1000)
        self.hub.route_message("Alice", wire)
        self.hub.pop_inbox("Bob")

        # Bob sends resume request
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Bob", to_nick="Alice",
        )
        env.relay_resume.relay_id = relay_id
        env.relay_resume.resume_offset = 500
        env.relay_resume.partial_sha256 = b"partial_hash_placeholder_32byte"
        self.hub.route_message("Bob", WireCodec.encode_text(env))

        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 1)
        decoded = WireCodec.decode(alice_msgs[0])
        self.assertEqual(decoded.WhichOneof("payload"), "relay_resume")
        self.assertEqual(decoded.relay_resume.relay_id, relay_id)
        self.assertEqual(decoded.relay_resume.resume_offset, 500)
        self.assertEqual(decoded.relay_resume.partial_sha256,
                         b"partial_hash_placeholder_32byte")

    def test_relay_resume_non_participant_dropped(self):
        """Resume from non-participant is silently dropped."""
        relay_id = self._establish_relay()
        self.hub.connect("Carol", nmdcpb=True)

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Carol", to_nick="Alice",
        )
        env.relay_resume.relay_id = relay_id
        env.relay_resume.resume_offset = 100
        self.hub.route_message("Carol", WireCodec.encode_text(env))

        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 0)

    def test_relay_data_with_offsets(self):
        """Data chunks carry correct offsets through relay."""
        relay_id = self._establish_relay()
        offsets = [0, 32768, 65536, 98304]

        for off in offsets:
            wire = self._make_relay_data("Alice", "Bob", relay_id,
                                          b"chunk", offset=off)
            self.hub.route_message("Alice", wire)
            bob_msgs = self.hub.pop_inbox("Bob")
            env = WireCodec.decode(bob_msgs[0])
            self.assertEqual(env.relay_data.offset, off)


# ============================================================================
# Hub plugin relay routing tests (tests the actual hub_plugin functions)
# ============================================================================

class TestHubPluginRelay(unittest.TestCase):
    """Test hub_plugin relay routing functions directly."""

    def setUp(self):
        from verlihub.client.nmdcpb import hub_plugin
        self._mod = hub_plugin
        # Save state
        self._saved = {
            "sessions": dict(hub_plugin._relay_sessions),
            "pending": dict(hub_plugin._pending_relay),
            "next_id": hub_plugin._next_relay_id,
            "stats": dict(hub_plugin._stats),
            "users": dict(hub_plugin._pb_users),
        }
        # Reset
        hub_plugin._relay_sessions.clear()
        hub_plugin._pending_relay.clear()
        hub_plugin._next_relay_id = 1
        for key in ("relay_sessions_created", "relay_sessions_closed",
                     "relay_bytes_forwarded"):
            hub_plugin._stats[key] = 0
        hub_plugin._pb_users["Alice"] = {"NMDCpb"}
        hub_plugin._pb_users["Bob"] = {"NMDCpb"}
        hub_plugin._pb_users["Carol"] = {"NMDCpb"}

    def tearDown(self):
        m = self._mod
        m._relay_sessions.clear()
        m._relay_sessions.update(self._saved["sessions"])
        m._pending_relay.clear()
        m._pending_relay.update(self._saved["pending"])
        m._next_relay_id = self._saved["next_id"]
        m._stats.update(self._saved["stats"])
        m._pb_users.clear()
        m._pb_users.update(self._saved["users"])

    def test_route_relay_request_stores_pending(self):
        """_route_relay_request stores token in _pending_relay."""
        m = self._mod
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
        )
        env.relay_request.target_nick = "Bob"
        env.relay_request.token = "test_token"
        env.relay_request.purpose = 0
        m._route_relay_request("Alice", env)

        self.assertIn("test_token", m._pending_relay)
        self.assertEqual(m._pending_relay["test_token"]["from_nick"], "Alice")
        self.assertEqual(m._pending_relay["test_token"]["to_nick"], "Bob")

    def test_route_relay_request_rejects_offline_target(self):
        """Request to offline user sends error status."""
        m = self._mod
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Ghost",
        )
        env.relay_request.target_nick = "Ghost"
        env.relay_request.token = "t1"
        m._route_relay_request("Alice", env)
        # No pending relay stored
        self.assertNotIn("t1", m._pending_relay)

    def test_route_relay_request_per_user_limit(self):
        """Relay request blocked when per-user limit reached."""
        m = self._mod
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        # Fill up Alice's sessions
        for i in range(m.RELAY_MAX_SESSIONS_PER_USER):
            m._relay_sessions[100 + i] = _RelaySession(
                100 + i, "Alice", "Bob", f"t{i}")
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Carol",
        )
        env.relay_request.target_nick = "Carol"
        env.relay_request.token = "blocked"
        m._route_relay_request("Alice", env)
        self.assertNotIn("blocked", m._pending_relay)

    def test_route_relay_ack_creates_session(self):
        """_route_relay_ack creates session and increments counter."""
        m = self._mod
        # Simulate pending request
        m._pending_relay["tok"] = {
            "from_nick": "Alice", "to_nick": "Bob",
            "purpose": 0, "pubkey": b"", "created_at": time.time(),
        }
        ack_env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Bob", to_nick="Alice",
        )
        ack_env.relay_ack.token = "tok"
        ack_env.relay_ack.accepted = True
        m._route_relay_ack("Bob", ack_env)

        self.assertEqual(len(m._relay_sessions), 1)
        sess = m._relay_sessions[1]
        self.assertEqual(sess.user_a, "Alice")
        self.assertEqual(sess.user_b, "Bob")
        self.assertEqual(m._stats["relay_sessions_created"], 1)
        self.assertNotIn("tok", m._pending_relay)

    def test_route_relay_ack_rejection(self):
        """Rejected relay ack does not create a session."""
        m = self._mod
        m._pending_relay["tok"] = {
            "from_nick": "Alice", "to_nick": "Bob",
            "purpose": 0, "pubkey": b"", "created_at": time.time(),
        }
        ack_env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Bob", to_nick="Alice",
        )
        ack_env.relay_ack.token = "tok"
        ack_env.relay_ack.accepted = False
        ack_env.relay_ack.reject_reason = "no thanks"
        m._route_relay_ack("Bob", ack_env)

        self.assertEqual(len(m._relay_sessions), 0)
        self.assertEqual(m._stats["relay_sessions_created"], 0)

    def test_forward_relay_data_updates_stats(self):
        """_forward_relay_data updates bytes_forwarded and stats."""
        m = self._mod
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        m._relay_sessions[1] = _RelaySession(1, "Alice", "Bob", "tok")

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
        )
        env.relay_data.relay_id = 1
        env.relay_data.data = b"x" * 500
        m._forward_relay_data("Alice", env)

        self.assertEqual(m._relay_sessions[1].bytes_forwarded, 500)
        self.assertEqual(m._stats["relay_bytes_forwarded"], 500)

    def test_forward_relay_data_rejects_non_participant(self):
        """Data from non-participant does not forward."""
        m = self._mod
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        m._relay_sessions[1] = _RelaySession(1, "Alice", "Bob", "tok")

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Carol", to_nick="Bob",
        )
        env.relay_data.relay_id = 1
        env.relay_data.data = b"intruder"
        m._forward_relay_data("Carol", env)

        self.assertEqual(m._relay_sessions[1].bytes_forwarded, 0)

    def test_route_relay_closed(self):
        """_route_relay_closed removes session."""
        m = self._mod
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        m._relay_sessions[1] = _RelaySession(1, "Alice", "Bob", "tok")

        env = WireCodec.make_envelope(route=PbEnvelope.DIRECT)
        env.relay_closed.relay_id = 1
        env.relay_closed.reason = 0  # NORMAL
        m._route_relay_closed("Alice", env)

        self.assertNotIn(1, m._relay_sessions)
        self.assertEqual(m._stats["relay_sessions_closed"], 1)

    def test_hubrelay_disabled(self):
        """Relay request blocked when ENABLE_HUBRELAY is False."""
        m = self._mod
        old_val = m.ENABLE_HUBRELAY
        try:
            m.ENABLE_HUBRELAY = False
            env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
            )
            env.relay_request.target_nick = "Bob"
            env.relay_request.token = "disabled_tok"
            m._route_relay_request("Alice", env)
            self.assertNotIn("disabled_tok", m._pending_relay)
        finally:
            m.ENABLE_HUBRELAY = old_val

    def test_relay_id_monotonic(self):
        """Relay IDs are assigned monotonically."""
        m = self._mod
        ids = []
        for i in range(3):
            token = f"tok_{i}"
            m._pending_relay[token] = {
                "from_nick": "Alice", "to_nick": "Bob",
                "purpose": 0, "pubkey": b"", "created_at": time.time(),
            }
            ack_env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT, from_nick="Bob", to_nick="Alice",
            )
            ack_env.relay_ack.token = token
            ack_env.relay_ack.accepted = True
            m._route_relay_ack("Bob", ack_env)
            ids.append(list(m._relay_sessions.keys())[-1])
        self.assertEqual(ids, [1, 2, 3])

    def test_forward_relay_resume(self):
        """_forward_relay_resume forwards resume to peer and touches session."""
        m = self._mod
        from verlihub.client.nmdcpb.hub_plugin import _RelaySession
        sess = _RelaySession(1, "Alice", "Bob", "tok")
        m._relay_sessions[1] = sess
        old_activity = sess.last_activity

        import time as _time
        _time.sleep(0.01)  # Tiny sleep to ensure time difference

        env = WireCodec.make_envelope(route=PbEnvelope.DIRECT)
        env.relay_resume.relay_id = 1
        env.relay_resume.resume_offset = 5000
        m._forward_relay_resume("Bob", env)

        # Session activity is updated
        self.assertGreater(sess.last_activity, old_activity)

    def test_forward_relay_resume_unknown_session(self):
        """Resume for unknown session does not crash."""
        m = self._mod
        env = WireCodec.make_envelope(route=PbEnvelope.DIRECT)
        env.relay_resume.relay_id = 999
        env.relay_resume.resume_offset = 100
        # Should not raise
        m._forward_relay_resume("Alice", env)


# ========================================================================
# Phase 3.5.2 — Segmented Multi-Source Downloads
# ========================================================================

class TestSegmentCoordinatorUnit(unittest.TestCase):
    """Unit tests for the SegmentCoordinator class."""

    def _make_coord(self, file_size=1_000_000, peers=None, segment_size=0):
        from verlihub.client.nmdcpb.relay import SegmentCoordinator
        if peers is None:
            peers = ["Alice", "Bob"]
        return SegmentCoordinator(
            file_tth="TTH_TEST",
            file_size=file_size,
            peers=peers,
            segment_size=segment_size,
        )

    def test_plan_segments_basic(self):
        """plan_segments splits file into correct number of segments."""
        coord = self._make_coord(file_size=1_000_000, peers=["A", "B"])
        segs = coord.plan_segments()
        total = sum(s.length for s in segs)
        self.assertEqual(total, 1_000_000)
        # With 2 peers each gets ~500KB, both >= MIN_SEGMENT_SIZE
        self.assertTrue(len(segs) >= 2)
        # Each segment has consecutive offsets
        for i, s in enumerate(segs):
            if i > 0:
                self.assertEqual(s.offset, segs[i - 1].offset + segs[i - 1].length)

    def test_plan_segments_round_robin(self):
        """Segments are assigned to peers in round-robin order."""
        coord = self._make_coord(file_size=3_000_000, peers=["A", "B", "C"],
                                 segment_size=500_000)
        segs = coord.plan_segments()
        self.assertEqual(len(segs), 6)
        self.assertEqual([s.peer_nick for s in segs],
                         ["A", "B", "C", "A", "B", "C"])

    def test_plan_segments_small_file(self):
        """File smaller than MIN_SEGMENT_SIZE produces one segment."""
        coord = self._make_coord(file_size=100_000, peers=["A", "B"])
        segs = coord.plan_segments()
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].length, 100_000)

    def test_plan_segments_exact_split(self):
        """File size exactly divisible by segment_size."""
        coord = self._make_coord(file_size=1_000_000, segment_size=500_000)
        segs = coord.plan_segments()
        self.assertEqual(len(segs), 2)
        self.assertTrue(all(s.length == 500_000 for s in segs))

    def test_assign_and_start_segment(self):
        """assign_segment and start_segment update state correctly."""
        from verlihub.client.nmdcpb.relay import SegmentState
        coord = self._make_coord(file_size=1_000_000, segment_size=500_000)
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=42)
        self.assertEqual(coord._segments[0].state, SegmentState.ASSIGNED)
        self.assertEqual(coord._segments[0].relay_id, 42)
        coord.start_segment(0)
        self.assertEqual(coord._segments[0].state, SegmentState.TRANSFERRING)

    def test_on_segment_data_tracks_bytes(self):
        """on_segment_data accumulates bytes_received."""
        from verlihub.client.nmdcpb.relay import SegmentState
        coord = self._make_coord(file_size=1_000_000, segment_size=500_000)
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=1)

        coord.on_segment_data(0, b"x" * 100_000)
        self.assertEqual(coord._segments[0].bytes_received, 100_000)
        self.assertEqual(coord._segments[0].state, SegmentState.TRANSFERRING)

    def test_on_segment_data_completes(self):
        """Segment completes when bytes_received >= length."""
        from verlihub.client.nmdcpb.relay import SegmentState
        coord = self._make_coord(file_size=500_000, segment_size=500_000)
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=1)

        completed = []
        coord.on_segment_complete = lambda seg: completed.append(seg.index)

        coord.on_segment_data(0, b"x" * 500_000)
        self.assertEqual(coord._segments[0].state, SegmentState.COMPLETED)
        self.assertEqual(completed, [0])

    def test_download_complete_callback(self):
        """on_download_complete fires when all segments are done."""
        from verlihub.client.nmdcpb.relay import SegmentState
        coord = self._make_coord(file_size=1_000_000, segment_size=500_000)
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=1)
        coord.assign_segment(1, "Bob", relay_id=2)

        downloads = []
        coord.on_download_complete = lambda info: downloads.append(info)

        coord.on_segment_data(0, b"x" * 500_000)
        self.assertEqual(len(downloads), 0)  # Only 1 of 2 done

        coord.on_segment_data(1, b"y" * 500_000)
        self.assertEqual(len(downloads), 1)
        self.assertTrue(downloads[0].is_complete)
        self.assertGreater(downloads[0].completed_at, 0)

    def test_fail_segment_retries(self):
        """fail_segment resets segment for retry up to MAX_RETRIES."""
        from verlihub.client.nmdcpb.relay import SegmentState, SegmentCoordinator
        coord = self._make_coord(file_size=500_000, segment_size=500_000)
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=1)

        for i in range(SegmentCoordinator.MAX_RETRIES):
            can_retry = coord.fail_segment(0)
            self.assertTrue(can_retry)
            self.assertEqual(coord._segments[0].state, SegmentState.PENDING)

        # One more failure exceeds MAX_RETRIES
        can_retry = coord.fail_segment(0)
        self.assertFalse(can_retry)
        self.assertEqual(coord._segments[0].state, SegmentState.FAILED)

    def test_fail_segment_callback(self):
        """on_segment_failed fires when retries exhausted."""
        from verlihub.client.nmdcpb.relay import SegmentCoordinator
        coord = self._make_coord(file_size=500_000, segment_size=500_000)
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=1)

        failed = []
        coord.on_segment_failed = lambda seg: failed.append(seg.index)

        for _ in range(SegmentCoordinator.MAX_RETRIES):
            coord.fail_segment(0)
        coord.fail_segment(0)
        self.assertEqual(failed, [0])

    def test_reassign_peer(self):
        """reassign_peer moves pending/assigned segments to new peer."""
        from verlihub.client.nmdcpb.relay import SegmentState
        coord = self._make_coord(file_size=2_000_000, segment_size=500_000,
                                 peers=["Alice", "Bob"])
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=1)
        coord.assign_segment(1, "Bob", relay_id=2)
        coord.start_segment(1)  # Transferring — not reassignable

        reassigned = coord.reassign_peer("Bob", "Carol")
        # Only segment 3 (peer_nick="Bob", state=PENDING) reassigned
        bob_segs = [s for s in coord._segments if s.peer_nick == "Bob"]
        # Segment 1 is TRANSFERRING so stays with Bob
        self.assertTrue(all(s.state == SegmentState.TRANSFERRING for s in bob_segs))

    def test_get_segment_by_relay(self):
        """get_segment_by_relay returns correct segment."""
        coord = self._make_coord(file_size=1_000_000, segment_size=500_000)
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=42)
        coord.assign_segment(1, "Bob", relay_id=99)

        seg = coord.get_segment_by_relay(42)
        self.assertIsNotNone(seg)
        self.assertEqual(seg.index, 0)

        seg = coord.get_segment_by_relay(99)
        self.assertEqual(seg.index, 1)

        self.assertIsNone(coord.get_segment_by_relay(777))

    def test_info_properties(self):
        """SegmentedDownloadInfo computed properties work correctly."""
        coord = self._make_coord(file_size=1_000_000, segment_size=500_000,
                                 peers=["Alice", "Bob"])
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=1)
        coord.assign_segment(1, "Bob", relay_id=2)

        self.assertEqual(coord.info.progress, 0.0)
        self.assertEqual(coord.info.bytes_received, 0)
        self.assertEqual(coord.info.completed_segments, 0)
        self.assertFalse(coord.info.is_complete)
        self.assertEqual(set(coord.info.active_peers), {"Alice", "Bob"})

        coord.on_segment_data(0, b"x" * 250_000)
        self.assertAlmostEqual(coord.info.progress, 0.25, places=2)

        coord.on_segment_data(0, b"x" * 250_000)
        self.assertEqual(coord.info.completed_segments, 1)

    def test_on_segment_data_ignores_wrong_state(self):
        """on_segment_data is a no-op for COMPLETED or FAILED segments."""
        from verlihub.client.nmdcpb.relay import SegmentState
        coord = self._make_coord(file_size=500_000, segment_size=500_000)
        coord.plan_segments()
        coord.assign_segment(0, "Alice", relay_id=1)
        coord.on_segment_data(0, b"x" * 500_000)  # complete it
        self.assertEqual(coord._segments[0].state, SegmentState.COMPLETED)
        # Further data is ignored
        coord.on_segment_data(0, b"y" * 100)
        self.assertEqual(coord._segments[0].bytes_received, 500_000)


class TestSegmentRoutingIntegration(unittest.TestCase):
    """Integration tests for segment_request / segment_info routing."""

    def setUp(self):
        self.hub = MockHub()
        self.hub.connect("Alice")
        self.hub.connect("Bob")
        self.hub.connect("Carol")
        # Legacy user
        self.hub.connect("Dave", nmdcpb=False)

    def _send_segment_request(self, sender, target, file_tth="TTH123",
                              offset=0, length=500_000, request_id="req1"):
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick=sender, to_nick=target,
        )
        env.segment_request.file_tth = file_tth
        env.segment_request.file_size = 1_000_000
        env.segment_request.segment_offset = offset
        env.segment_request.segment_length = length
        env.segment_request.request_id = request_id
        wire = WireCodec.encode_text(env)
        self.hub.route_message(sender, wire)

    def _send_segment_info(self, sender, target, request_id="req1",
                           available=True, offset=0, length=500_000):
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick=sender, to_nick=target,
        )
        env.segment_info.request_id = request_id
        env.segment_info.available = available
        env.segment_info.segment_offset = offset
        env.segment_info.segment_length = length
        env.segment_info.peer_nick = sender
        wire = WireCodec.encode_text(env)
        self.hub.route_message(sender, wire)

    def test_segment_request_routed(self):
        """segment_request reaches the target peer."""
        self._send_segment_request("Alice", "Bob")
        msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(msgs), 1)
        env = WireCodec.decode(msgs[0])
        self.assertEqual(env.WhichOneof("payload"), "segment_request")
        self.assertEqual(env.segment_request.file_tth, "TTH123")
        self.assertEqual(env.segment_request.request_id, "req1")
        self.assertEqual(env.from_nick, "Alice")

    def test_segment_info_routed(self):
        """segment_info reply reaches the requester."""
        self._send_segment_info("Bob", "Alice", available=True)
        msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(msgs), 1)
        env = WireCodec.decode(msgs[0])
        self.assertEqual(env.WhichOneof("payload"), "segment_info")
        self.assertTrue(env.segment_info.available)
        self.assertEqual(env.from_nick, "Bob")

    def test_segment_request_to_offline_user(self):
        """segment_request to offline user is silently dropped."""
        self._send_segment_request("Alice", "Offline")
        msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(msgs), 0)

    def test_segment_request_to_legacy_user(self):
        """segment_request to non-NMDCpb user is dropped."""
        self._send_segment_request("Alice", "Dave")
        msgs = self.hub.pop_inbox("Dave")
        self.assertEqual(len(msgs), 0)

    def test_multi_peer_segment_workflow(self):
        """Full multi-peer segment workflow: Alice requests from Bob and Carol."""
        # Alice asks Bob for segment 0..500K
        self._send_segment_request("Alice", "Bob", offset=0, length=500_000,
                                   request_id="seg0")
        # Alice asks Carol for segment 500K..1M
        self._send_segment_request("Alice", "Carol", offset=500_000,
                                   length=500_000, request_id="seg1")

        # Bob and Carol receive requests
        bob_msgs = self.hub.pop_inbox("Bob")
        carol_msgs = self.hub.pop_inbox("Carol")
        self.assertEqual(len(bob_msgs), 1)
        self.assertEqual(len(carol_msgs), 1)

        bob_req = WireCodec.decode(bob_msgs[0])
        self.assertEqual(bob_req.segment_request.segment_offset, 0)
        carol_req = WireCodec.decode(carol_msgs[0])
        self.assertEqual(carol_req.segment_request.segment_offset, 500_000)

        # Bob says available, Carol says not available
        self._send_segment_info("Bob", "Alice", request_id="seg0",
                                available=True, offset=0, length=500_000)
        self._send_segment_info("Carol", "Alice", request_id="seg1",
                                available=False)

        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 2)
        env0 = WireCodec.decode(alice_msgs[0])
        env1 = WireCodec.decode(alice_msgs[1])
        self.assertTrue(env0.segment_info.available)
        self.assertFalse(env1.segment_info.available)

    def test_segment_coordinator_with_routing(self):
        """SegmentCoordinator + MockHub segment routing end-to-end."""
        from verlihub.client.nmdcpb.relay import SegmentCoordinator, SegmentState

        coord = SegmentCoordinator(
            file_tth="TTH_BIG",
            file_size=1_000_000,
            peers=["Bob", "Carol"],
            segment_size=500_000,
        )
        segs = coord.plan_segments()
        self.assertEqual(len(segs), 2)

        # Alice sends segment_request to each peer
        for seg in segs:
            self._send_segment_request(
                "Alice", seg.peer_nick,
                file_tth="TTH_BIG",
                offset=seg.offset,
                length=seg.length,
                request_id=f"seg{seg.index}",
            )

        # Both peers receive their requests
        bob_msgs = self.hub.pop_inbox("Bob")
        carol_msgs = self.hub.pop_inbox("Carol")
        self.assertEqual(len(bob_msgs), 1)
        self.assertEqual(len(carol_msgs), 1)

        # Both reply available
        self._send_segment_info("Bob", "Alice", request_id="seg0",
                                available=True, offset=0, length=500_000)
        self._send_segment_info("Carol", "Alice", request_id="seg1",
                                available=True, offset=500_000, length=500_000)

        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 2)

        # Simulate assigning relay sessions
        coord.assign_segment(0, "Bob", relay_id=10)
        coord.assign_segment(1, "Carol", relay_id=11)

        # Simulate data arrival — both segments complete
        coord.on_segment_data(0, b"x" * 500_000)
        coord.on_segment_data(1, b"y" * 500_000)

        self.assertTrue(coord.info.is_complete)
        self.assertEqual(coord.info.bytes_received, 1_000_000)
        self.assertAlmostEqual(coord.info.progress, 1.0)

    def test_segment_failure_and_redistribution(self):
        """When a peer fails, segments can be reassigned."""
        from verlihub.client.nmdcpb.relay import SegmentCoordinator, SegmentState

        coord = SegmentCoordinator(
            file_tth="TTH_FAIL",
            file_size=1_500_000,
            peers=["Bob", "Carol", "Dave"],
            segment_size=500_000,
        )
        segs = coord.plan_segments()
        self.assertEqual(len(segs), 3)

        coord.assign_segment(0, "Bob", relay_id=1)
        coord.assign_segment(1, "Carol", relay_id=2)
        coord.assign_segment(2, "Dave", relay_id=3)

        # Carol disconnects — fail segment 1
        can_retry = coord.fail_segment(1)
        self.assertTrue(can_retry)
        self.assertEqual(coord._segments[1].state, SegmentState.PENDING)

        # Reassign Carol's work to Bob
        coord.assign_segment(1, "Bob", relay_id=4)
        coord.on_segment_data(0, b"x" * 500_000)
        coord.on_segment_data(1, b"y" * 500_000)
        coord.on_segment_data(2, b"z" * 500_000)

        self.assertTrue(coord.info.is_complete)


class TestHubPluginSegment(unittest.TestCase):
    """Unit tests for hub_plugin.py _forward_segment_msg."""

    def setUp(self):
        from verlihub.client.nmdcpb import hub_plugin
        self._mod = hub_plugin
        self._sent = []
        self._saved = {
            "users": dict(hub_plugin._pb_users),
        }
        hub_plugin._pb_users["Alice"] = {"NMDCpb"}
        hub_plugin._pb_users["Bob"] = {"NMDCpb"}
        hub_plugin._pb_users["Carol"] = {"NMDCpb"}

        # Patch _send_to_user at the module level so internal calls go here
        import unittest.mock as _mock
        self._patcher = _mock.patch.object(
            hub_plugin, '_send_to_user',
            side_effect=lambda data, nick: self._sent.append((nick, data)) or True,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        m = self._mod
        m._pb_users.clear()
        m._pb_users.update(self._saved["users"])

    def test_forward_segment_request(self):
        """_forward_segment_msg forwards segment_request to target."""
        m = self._mod
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Bob",
        )
        env.segment_request.file_tth = "TTH1"
        env.segment_request.segment_offset = 0
        env.segment_request.segment_length = 500_000
        env.segment_request.request_id = "r1"
        m._forward_segment_msg("Alice", env)
        self.assertEqual(len(self._sent), 1)
        self.assertEqual(self._sent[0][0], "Bob")

    def test_forward_segment_info(self):
        """_forward_segment_msg forwards segment_info reply."""
        m = self._mod
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Bob", to_nick="Alice",
        )
        env.segment_info.request_id = "r1"
        env.segment_info.available = True
        env.segment_info.peer_nick = "Bob"
        m._forward_segment_msg("Bob", env)
        self.assertEqual(len(self._sent), 1)
        self.assertEqual(self._sent[0][0], "Alice")

    def test_forward_segment_to_offline(self):
        """_forward_segment_msg drops messages to offline users."""
        m = self._mod
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="Offline",
        )
        env.segment_request.file_tth = "TTH1"
        env.segment_request.request_id = "r1"
        m._forward_segment_msg("Alice", env)
        # No message sent to "Offline" (only error status to sender)
        targets = [nick for nick, _ in self._sent]
        self.assertNotIn("Offline", targets)

    def test_forward_segment_to_non_pb_user(self):
        """_forward_segment_msg drops messages to non-PB users.

        When vh is None (test mode), _get_all_nicks() == _pb_users.keys(),
        so a user not in _pb_users is also 'offline'. We verify the message
        is dropped regardless.
        """
        m = self._mod
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick="Alice", to_nick="LegacyUser",
        )
        env.segment_request.file_tth = "TTH1"
        env.segment_request.request_id = "r1"
        m._forward_segment_msg("Alice", env)
        # No message sent to "LegacyUser" (only error status to sender)
        targets = [nick for nick, _ in self._sent]
        self.assertNotIn("LegacyUser", targets)


# ========================================================================
# Phase 3.5.3 — Stealth Hub-Wide Search
# ========================================================================

class TestStealthSearchIntegration(unittest.TestCase):
    """Integration tests for stealth user query / sweep through MockHub."""

    def setUp(self):
        self.hub = MockHub()
        self.hub.connect("Alice")
        self.hub.connect("Bob")
        self.hub.connect("Carol")
        self.hub.connect("Dave", nmdcpb=False)  # Legacy user

    def _send_user_query(self, sender, query_id="q1", feature_filter="",
                         max_results=50, sweep=False, search_query=""):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick=sender)
        env.user_query.query_id = query_id
        if feature_filter:
            env.user_query.feature_filter = feature_filter
        env.user_query.max_results = max_results
        env.user_query.sweep = sweep
        if sweep and search_query:
            env.user_query.search.query = search_query
            env.user_query.search.search_id = query_id
        wire = WireCodec.encode_text(env)
        self.hub.route_message(sender, wire)

    def test_user_query_returns_pb_users(self):
        """User query returns all NMDCpb users except the sender."""
        self._send_user_query("Alice", query_id="q1")
        msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(msgs), 1)
        env = WireCodec.decode(msgs[0])
        self.assertEqual(env.WhichOneof("payload"), "user_query_result")
        result = env.user_query_result
        self.assertEqual(result.query_id, "q1")
        self.assertIn("Bob", result.nicks)
        self.assertIn("Carol", result.nicks)
        self.assertNotIn("Alice", result.nicks)   # Sender excluded
        self.assertNotIn("Dave", result.nicks)     # Legacy excluded
        self.assertEqual(result.total_matching, 2)

    def test_user_query_max_results(self):
        """max_results limits the returned nick list."""
        self._send_user_query("Alice", query_id="q2", max_results=1)
        msgs = self.hub.pop_inbox("Alice")
        env = WireCodec.decode(msgs[0])
        result = env.user_query_result
        self.assertEqual(len(result.nicks), 1)
        self.assertEqual(result.total_matching, 2)

    def test_user_query_sweep_sends_private_search(self):
        """When sweep=True, PbPrivateSearch is sent to each matching user."""
        self._send_user_query("Alice", query_id="sweep1", sweep=True,
                              search_query="test_file.mp3")
        # Alice gets the query result
        alice_msgs = self.hub.pop_inbox("Alice")
        self.assertEqual(len(alice_msgs), 1)
        env = WireCodec.decode(alice_msgs[0])
        result = env.user_query_result
        self.assertTrue(result.sweep_started)
        self.assertEqual(result.sweep_count, 2)

        # Bob and Carol each get a PbPrivateSearch
        for peer in ("Bob", "Carol"):
            msgs = self.hub.pop_inbox(peer)
            self.assertEqual(len(msgs), 1, f"{peer} should get 1 search msg")
            env = WireCodec.decode(msgs[0])
            self.assertEqual(env.WhichOneof("payload"), "private_search")
            self.assertEqual(env.private_search.query, "test_file.mp3")
            self.assertEqual(env.from_nick, "Alice")

    def test_user_query_sweep_no_search_payload(self):
        """Sweep with no search payload does not forward anything."""
        env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alice")
        env.user_query.query_id = "s2"
        env.user_query.sweep = True  # No search field set
        wire = WireCodec.encode_text(env)
        self.hub.route_message("Alice", wire)

        alice_msgs = self.hub.pop_inbox("Alice")
        result = WireCodec.decode(alice_msgs[0]).user_query_result
        self.assertFalse(result.sweep_started)
        self.assertEqual(result.sweep_count, 0)

        # No search sent to peers
        self.assertEqual(len(self.hub.pop_inbox("Bob")), 0)
        self.assertEqual(len(self.hub.pop_inbox("Carol")), 0)

    def test_user_query_no_matching_users(self):
        """Query returns empty when no other NMDCpb users connected."""
        solo_hub = MockHub()
        solo_hub.connect("Alone")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alone")
        env.user_query.query_id = "q_empty"
        wire = WireCodec.encode_text(env)
        solo_hub.route_message("Alone", wire)
        msgs = solo_hub.pop_inbox("Alone")
        result = WireCodec.decode(msgs[0]).user_query_result
        self.assertEqual(len(result.nicks), 0)
        self.assertEqual(result.total_matching, 0)

    def test_user_query_legacy_user_excluded(self):
        """Legacy (non-NMDCpb) users are never in results or swept."""
        self._send_user_query("Alice", query_id="q_legacy", sweep=True,
                              search_query="anything")
        alice_msgs = self.hub.pop_inbox("Alice")
        result = WireCodec.decode(alice_msgs[0]).user_query_result
        self.assertNotIn("Dave", result.nicks)

        dave_msgs = self.hub.pop_inbox("Dave")
        self.assertEqual(len(dave_msgs), 0)


class TestHubPluginStealthSearch(unittest.TestCase):
    """Unit tests for hub_plugin.py _route_user_query."""

    def setUp(self):
        from verlihub.client.nmdcpb import hub_plugin
        self._mod = hub_plugin
        self._sent = []
        self._saved = {
            "users": dict(hub_plugin._pb_users),
            "stats": dict(hub_plugin._stats),
        }
        hub_plugin._pb_users["Alice"] = {"NMDCpb", "HubRelay"}
        hub_plugin._pb_users["Bob"] = {"NMDCpb"}
        hub_plugin._pb_users["Carol"] = {"NMDCpb", "HubRelay"}

        import unittest.mock as _mock
        self._patcher = _mock.patch.object(
            hub_plugin, '_send_to_user',
            side_effect=lambda data, nick: self._sent.append((nick, data)) or True,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        m = self._mod
        m._pb_users.clear()
        m._pb_users.update(self._saved["users"])
        m._stats.update(self._saved["stats"])

    def test_user_query_basic(self):
        """_route_user_query returns matching users."""
        m = self._mod
        env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alice")
        env.user_query.query_id = "uq1"
        env.user_query.max_results = 50
        m._route_user_query("Alice", env)

        # Should send result back to Alice
        alice_msgs = [(n, d) for n, d in self._sent if n == "Alice"]
        self.assertEqual(len(alice_msgs), 1)
        resp_env = WireCodec.decode(alice_msgs[0][1])
        result = resp_env.user_query_result
        self.assertEqual(result.query_id, "uq1")
        self.assertIn("Bob", result.nicks)
        self.assertIn("Carol", result.nicks)
        self.assertNotIn("Alice", result.nicks)
        self.assertEqual(result.total_matching, 2)

    def test_user_query_feature_filter(self):
        """Feature filter restricts results to users with that feature."""
        m = self._mod
        env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alice")
        env.user_query.query_id = "uq_feat"
        env.user_query.feature_filter = "HubRelay"
        m._route_user_query("Alice", env)

        alice_msgs = [(n, d) for n, d in self._sent if n == "Alice"]
        result = WireCodec.decode(alice_msgs[0][1]).user_query_result
        # Only Carol has HubRelay (Alice is sender, excluded)
        self.assertIn("Carol", result.nicks)
        self.assertNotIn("Bob", result.nicks)

    def test_user_query_sweep(self):
        """Sweep sends PbPrivateSearch to matching users."""
        m = self._mod
        env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alice")
        env.user_query.query_id = "uq_sweep"
        env.user_query.sweep = True
        env.user_query.search.query = "file.txt"
        env.user_query.search.search_id = "uq_sweep"
        m._route_user_query("Alice", env)

        # Bob and Carol should each get a private_search
        bob_msgs = [(n, d) for n, d in self._sent if n == "Bob"]
        carol_msgs = [(n, d) for n, d in self._sent if n == "Carol"]
        self.assertEqual(len(bob_msgs), 1)
        self.assertEqual(len(carol_msgs), 1)

        bob_env = WireCodec.decode(bob_msgs[0][1])
        self.assertEqual(bob_env.WhichOneof("payload"), "private_search")
        self.assertEqual(bob_env.private_search.query, "file.txt")

        # Result to Alice has sweep info
        alice_msgs = [(n, d) for n, d in self._sent if n == "Alice"]
        result = WireCodec.decode(alice_msgs[0][1]).user_query_result
        self.assertTrue(result.sweep_started)
        self.assertEqual(result.sweep_count, 2)

    def test_user_query_disabled(self):
        """Query is rejected when ENABLE_STEALTH_SEARCH is False."""
        m = self._mod
        orig = m.ENABLE_STEALTH_SEARCH
        m.ENABLE_STEALTH_SEARCH = False
        try:
            env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alice")
            env.user_query.query_id = "uq_off"
            m._route_user_query("Alice", env)
            # Should get an error status, not a query result
            alice_msgs = [(n, d) for n, d in self._sent if n == "Alice"]
            self.assertEqual(len(alice_msgs), 1)
            resp = WireCodec.decode(alice_msgs[0][1])
            self.assertEqual(resp.WhichOneof("payload"), "status")
        finally:
            m.ENABLE_STEALTH_SEARCH = orig

    def test_user_query_stats(self):
        """_route_user_query increments stealth_queries stat."""
        m = self._mod
        m._stats["stealth_queries"] = 0
        m._stats["stealth_sweeps"] = 0
        env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alice")
        env.user_query.query_id = "uq_stats"
        m._route_user_query("Alice", env)
        self.assertEqual(m._stats["stealth_queries"], 1)
        self.assertEqual(m._stats["stealth_sweeps"], 0)

        # Sweep increments both
        self._sent.clear()
        env2 = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alice")
        env2.user_query.query_id = "uq_stats2"
        env2.user_query.sweep = True
        env2.user_query.search.query = "test"
        env2.user_query.search.search_id = "uq_stats2"
        m._route_user_query("Alice", env2)
        self.assertEqual(m._stats["stealth_queries"], 2)
        self.assertEqual(m._stats["stealth_sweeps"], 1)

    def test_user_query_max_results_cap(self):
        """max_results is capped at STEALTH_MAX_RESULTS."""
        m = self._mod
        env = WireCodec.make_envelope(route=PbEnvelope.HUB, from_nick="Alice")
        env.user_query.query_id = "uq_cap"
        env.user_query.max_results = 1
        m._route_user_query("Alice", env)
        alice_msgs = [(n, d) for n, d in self._sent if n == "Alice"]
        result = WireCodec.decode(alice_msgs[0][1]).user_query_result
        self.assertEqual(len(result.nicks), 1)
        self.assertEqual(result.total_matching, 2)


if __name__ == "__main__":
    unittest.main()
