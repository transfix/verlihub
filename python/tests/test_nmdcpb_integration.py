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
)
from verlihub.client.nmdcpb.wire import WireCodec, FEATURE_NMDCPB
from verlihub.client.nmdcpb.e2epm import E2EPMManager


class MockHub:
    """Simulates the NMDCpb hub plugin's routing logic.

    Maintains a set of connected users and their mailboxes.
    """

    def __init__(self):
        self.users: dict[str, dict] = {}  # nick → {nmdcpb: bool, inbox: [str]}

    def connect(self, nick: str, nmdcpb: bool = True) -> None:
        self.users[nick] = {"nmdcpb": nmdcpb, "inbox": []}

    def disconnect(self, nick: str) -> None:
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
            if target in self.users:
                new_wire = WireCodec.encode_text(env)
                self.send_to_user(new_wire, target)

        elif route == PbEnvelope.ECHO:
            new_wire = WireCodec.encode_text(env)
            self.send_to_user(new_wire, sender)
            for nick in self.users:
                if nick != sender and self.is_nmdcpb_user(nick):
                    self.send_to_user(new_wire, nick)


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
        wire = WireCodec.encode_text(env)

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
        """ECHO sends to all including sender."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.ECHO,
            from_nick="Alice",
        )
        env.chat.text = "Echo test"
        wire = WireCodec.encode_text(env)

        self.hub.route_message("Alice", wire)

        # Both should receive
        alice_msgs = self.hub.pop_inbox("Alice")
        bob_msgs = self.hub.pop_inbox("Bob")
        self.assertEqual(len(alice_msgs), 1)
        self.assertEqual(len(bob_msgs), 1)

    def test_direct_to_nonexistent_user(self):
        """Direct message to offline user — silently dropped."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="OfflineUser",
        )
        env.chat.text = "Hello?"
        wire = WireCodec.encode_text(env)

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


if __name__ == "__main__":
    unittest.main()
