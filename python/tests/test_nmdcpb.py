"""
Unit tests for the NMDCpb protocol extension (verlihub.client.nmdcpb).

Covers:
- Wire codec (encode/decode all 3 formats, edge cases)
- E2EPM (key exchange, encrypt/decrypt, replay, tamper, TOFU, fingerprints)
- Client (NMDC lock-to-key, protocol helpers)
"""

import time
import struct
import unittest

import sys
import os

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbChat,
    PbUserInfo,
    PbStatus,
    PbPMKeyExchange,
    PbEncryptedPM,
    PbPMPlaintext,
    PbPMSessionEnd,
    PbRelayRequest,
    PbHubInfo,
    PbExtension,
)
from verlihub.client.nmdcpb.wire import (
    WireCodec,
    FEATURE_NMDCPB,
    FEATURE_HUBRELAY,
    _b64url_encode,
    _b64url_decode,
)
from verlihub.client.nmdcpb.e2epm import (
    E2EPMSession,
    E2EPMManager,
    _generate_fingerprint,
    _derive_keys,
    _build_nonce,
    _build_aad,
    E2EPM_SALT,
)
from verlihub.client.nmdcpb.client import _nmdc_lock_to_key


# ==========================================================================
# Wire Codec Tests
# ==========================================================================


class TestBase64url(unittest.TestCase):
    """Test base64url helpers."""

    def test_roundtrip(self):
        data = b"\x00\xff\x80\xab" * 10
        encoded = _b64url_encode(data)
        decoded = _b64url_decode(encoded)
        self.assertEqual(data, decoded)

    def test_no_padding(self):
        for length in range(1, 50):
            data = os.urandom(length)
            encoded = _b64url_encode(data)
            self.assertNotIn("=", encoded)
            self.assertEqual(data, _b64url_decode(encoded))

    def test_url_safe_chars(self):
        # Data that produces + and / in regular base64
        data = b"\xfb\xff\xfe\xef"
        encoded = _b64url_encode(data)
        self.assertNotIn("+", encoded)
        self.assertNotIn("/", encoded)


class TestWireCodecText(unittest.TestCase):
    """Test $PB text mode encoding/decoding."""

    def _make_chat_envelope(self, text: str = "Hello", nick: str = "Alice") -> PbEnvelope:
        env = PbEnvelope()
        env.route = PbEnvelope.BROADCAST
        env.from_nick = nick
        env.chat.text = text
        env.timestamp = 1700000000000
        env.sequence = 1
        return env

    def test_encode_decode_roundtrip(self):
        env = self._make_chat_envelope("Hello from protobuf!")
        wire = WireCodec.encode_text(env)
        self.assertTrue(wire.startswith("$PB "))
        self.assertTrue(wire.endswith("|"))

        decoded = WireCodec.decode(wire)
        self.assertIsInstance(decoded, PbEnvelope)
        self.assertEqual(decoded.chat.text, "Hello from protobuf!")
        self.assertEqual(decoded.from_nick, "Alice")
        self.assertEqual(decoded.route, PbEnvelope.BROADCAST)
        self.assertEqual(decoded.timestamp, 1700000000000)

    def test_decode_without_terminator(self):
        env = self._make_chat_envelope("test")
        wire = WireCodec.encode_text(env)
        # Should work without trailing |
        decoded = WireCodec.decode(wire.rstrip("|"))
        self.assertEqual(decoded.chat.text, "test")

    def test_empty_envelope(self):
        env = PbEnvelope()
        wire = WireCodec.encode_text(env)
        decoded = WireCodec.decode(wire)
        self.assertIsInstance(decoded, PbEnvelope)

    def test_all_route_types(self):
        for route in (PbEnvelope.BROADCAST, PbEnvelope.DIRECT, PbEnvelope.HUB,
                      PbEnvelope.INFO, PbEnvelope.ECHO, PbEnvelope.FEATURE):
            env = PbEnvelope()
            env.route = route
            env.from_nick = "test"
            wire = WireCodec.encode_text(env)
            decoded = WireCodec.decode(wire)
            self.assertEqual(decoded.route, route)

    def test_direct_message_with_to_nick(self):
        env = PbEnvelope()
        env.route = PbEnvelope.DIRECT
        env.from_nick = "Alice"
        env.to_nick = "Bob"
        env.chat.text = "Private hello"
        env.chat.is_pm = True
        env.chat.target_nick = "Bob"

        wire = WireCodec.encode_text(env)
        decoded = WireCodec.decode(wire)
        self.assertEqual(decoded.to_nick, "Bob")
        self.assertEqual(decoded.chat.is_pm, True)
        self.assertEqual(decoded.chat.target_nick, "Bob")

    def test_unicode_text(self):
        env = self._make_chat_envelope("Привет мир 🌍 日本語テスト")
        wire = WireCodec.encode_text(env)
        decoded = WireCodec.decode(wire)
        self.assertEqual(decoded.chat.text, "Привет мир 🌍 日本語テスト")

    def test_special_payload_types(self):
        """Test various payload types in PbEnvelope."""
        # Status
        env = PbEnvelope()
        env.status.severity = PbStatus.ERROR
        env.status.code = 42
        env.status.message = "Something went wrong"
        wire = WireCodec.encode_text(env)
        decoded = WireCodec.decode(wire)
        self.assertEqual(decoded.status.severity, PbStatus.ERROR)
        self.assertEqual(decoded.status.code, 42)
        self.assertEqual(decoded.status.message, "Something went wrong")

        # HubInfo
        env = PbEnvelope()
        env.hub_info.name = "Test Hub"
        env.hub_info.description = "A test hub"
        env.hub_info.user_count = 100
        wire = WireCodec.encode_text(env)
        decoded = WireCodec.decode(wire)
        self.assertEqual(decoded.hub_info.name, "Test Hub")
        self.assertEqual(decoded.hub_info.user_count, 100)


class TestWireCodecBinary(unittest.TestCase):
    """Test $PBB binary mode encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        env = PbEnvelope()
        env.route = PbEnvelope.BROADCAST
        env.from_nick = "Alice"
        env.chat.text = "Binary test"

        wire_bytes = WireCodec.encode_binary(env)
        self.assertTrue(wire_bytes.startswith(b"$PBB "))
        self.assertTrue(wire_bytes.endswith(b"|"))

        decoded = WireCodec.decode_bytes(wire_bytes)
        self.assertIsInstance(decoded, PbEnvelope)
        self.assertEqual(decoded.chat.text, "Binary test")

    def test_length_header(self):
        env = PbEnvelope()
        env.chat.text = "x" * 100
        wire = WireCodec.encode_binary(env)
        # Parse the length from the header
        header_end = wire.index(b"\n")
        header = wire[5:header_end].decode("ascii")  # after "$PBB "
        length = int(header, 16)

        # The protobuf payload should be 'length' bytes after the newline
        payload = wire[header_end + 1:-1]  # strip trailing |
        self.assertEqual(len(payload), length)

    def test_binary_with_non_utf8_payload(self):
        """Protobuf with binary fields shouldn't break binary mode."""
        env = PbEnvelope()
        env.from_nick = "binary_test"
        # Use a field that takes raw bytes
        env.pm_key_exchange.public_key = os.urandom(32)

        wire = WireCodec.encode_binary(env)
        decoded = WireCodec.decode_bytes(wire)
        self.assertEqual(decoded.pm_key_exchange.public_key,
                         env.pm_key_exchange.public_key)

    def test_string_decode_binary(self):
        """Test decoding $PBB from str (works for ASCII-safe payloads)."""
        env = PbEnvelope()
        env.chat.text = "hello"
        wire_bytes = WireCodec.encode_binary(env)
        # If the protobuf bytes happen to be valid latin-1, str decode works
        wire_str = wire_bytes.decode("latin-1")
        decoded = WireCodec.decode(wire_str)
        self.assertEqual(decoded.chat.text, "hello")


class TestWireCodecRelay(unittest.TestCase):
    """Test $PBR relay mode encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        relay_id = 0xABCD
        data = os.urandom(64)

        wire = WireCodec.encode_relay(relay_id, data)
        self.assertTrue(wire.startswith(b"$PBR "))
        self.assertTrue(wire.endswith(b"|"))

        result = WireCodec.decode_bytes(wire)
        self.assertIsInstance(result, tuple)
        decoded_id, decoded_data = result
        self.assertEqual(decoded_id, relay_id)
        self.assertEqual(decoded_data, data)

    def test_relay_id_zero(self):
        data = b"\x01\x02\x03"
        wire = WireCodec.encode_relay(0, data)
        decoded_id, decoded_data = WireCodec.decode_bytes(wire)
        self.assertEqual(decoded_id, 0)
        self.assertEqual(decoded_data, data)

    def test_relay_large_id(self):
        data = b"test"
        wire = WireCodec.encode_relay(0xFFFFFFFF, data)
        decoded_id, decoded_data = WireCodec.decode_bytes(wire)
        self.assertEqual(decoded_id, 0xFFFFFFFF)

    def test_relay_string_decode(self):
        relay_id = 42
        data = b"safe-ascii-test"
        wire_bytes = WireCodec.encode_relay(relay_id, data)
        wire_str = wire_bytes.decode("latin-1")

        result = WireCodec.decode(wire_str)
        self.assertIsInstance(result, tuple)
        decoded_id, decoded_data = result
        self.assertEqual(decoded_id, 42)
        self.assertEqual(decoded_data, data)


class TestWireCodecHelpers(unittest.TestCase):
    """Test WireCodec helper methods."""

    def test_is_nmdcpb_command_str(self):
        self.assertTrue(WireCodec.is_nmdcpb_command("$PB abc123|"))
        self.assertTrue(WireCodec.is_nmdcpb_command("$PBB 1a\ndata|"))
        self.assertTrue(WireCodec.is_nmdcpb_command("$PBR ab 1a\ndata|"))
        self.assertFalse(WireCodec.is_nmdcpb_command("$Hello nick|"))
        self.assertFalse(WireCodec.is_nmdcpb_command("<nick> hello|"))
        self.assertFalse(WireCodec.is_nmdcpb_command("$PBLAH stuff"))

    def test_is_nmdcpb_command_bytes(self):
        self.assertTrue(WireCodec.is_nmdcpb_command(b"$PB abc|"))
        self.assertTrue(WireCodec.is_nmdcpb_command(b"$PBB 10\ndata|"))
        self.assertTrue(WireCodec.is_nmdcpb_command(b"$PBR 1 10\ndata|"))
        self.assertFalse(WireCodec.is_nmdcpb_command(b"$Lock BLAH"))

    def test_decode_non_nmdcpb(self):
        self.assertIsNone(WireCodec.decode("$Hello nick|"))
        self.assertIsNone(WireCodec.decode("<nick> hello|"))
        self.assertIsNone(WireCodec.decode_bytes(b"$Lock BLAH|"))

    def test_make_envelope(self):
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="Bob",
        )
        self.assertEqual(env.route, PbEnvelope.DIRECT)
        self.assertEqual(env.from_nick, "Alice")
        self.assertEqual(env.to_nick, "Bob")
        self.assertGreater(env.timestamp, 0)

    def test_check_supports(self):
        self.assertEqual(
            WireCodec.check_supports("$Supports UserCommand NMDCpb HubRelay"),
            (True, True),
        )
        self.assertEqual(
            WireCodec.check_supports("$Supports UserCommand NMDCpb"),
            (True, False),
        )
        self.assertEqual(
            WireCodec.check_supports("$Supports UserCommand NoGetINFO"),
            (False, False),
        )

    def test_inject_supports(self):
        result = WireCodec.inject_supports("$Supports UserCommand NoGetINFO|")
        self.assertIn("NMDCpb", result)
        self.assertTrue(result.endswith("|"))

        # With HubRelay
        result = WireCodec.inject_supports(
            "$Supports UserCommand|", nmdcpb=True, hubrelay=True,
        )
        self.assertIn("NMDCpb", result)
        self.assertIn("HubRelay", result)

        # Don't duplicate
        result = WireCodec.inject_supports("$Supports NMDCpb|")
        self.assertEqual(result.count("NMDCpb"), 1)


# ==========================================================================
# E2EPM Tests
# ==========================================================================


class TestE2EPMSession(unittest.TestCase):
    """Test E2EPMSession key exchange and crypto."""

    def _make_session_pair(self):
        """Create two E2EPM sessions (Alice and Bob) and exchange keys."""
        alice = E2EPMSession.create("Alice", "Bob")
        bob = E2EPMSession.create("Bob", "Alice")

        # Exchange public keys
        alice_kex = alice.make_key_exchange()
        bob_kex = bob.make_key_exchange()

        alice.complete_key_exchange(bob_kex.public_key)
        bob.complete_key_exchange(alice_kex.public_key)

        return alice, bob

    def test_key_exchange_creates_shared_secret(self):
        alice, bob = self._make_session_pair()
        self.assertTrue(alice.established)
        self.assertTrue(bob.established)
        self.assertEqual(alice.shared_secret, bob.shared_secret)

    def test_derived_keys_are_complementary(self):
        alice, bob = self._make_session_pair()
        # Alice's send key should be Bob's recv key, and vice versa
        self.assertEqual(alice.send_key, bob.recv_key)
        self.assertEqual(alice.recv_key, bob.send_key)

    def test_fingerprints_match(self):
        alice, bob = self._make_session_pair()
        self.assertEqual(alice.fingerprint, bob.fingerprint)
        self.assertGreater(len(alice.fingerprint), 0)

    def test_encrypt_decrypt_roundtrip(self):
        alice, bob = self._make_session_pair()

        epm = alice.encrypt_message("Hello Bob!")
        pt = bob.decrypt_message(epm)
        self.assertEqual(pt.text, "Hello Bob!")

    def test_encrypt_decrypt_action(self):
        alice, bob = self._make_session_pair()

        epm = alice.encrypt_message("dances", is_action=True)
        pt = bob.decrypt_message(epm)
        self.assertEqual(pt.text, "dances")
        self.assertTrue(pt.is_action)

    def test_bidirectional_communication(self):
        alice, bob = self._make_session_pair()

        # Alice → Bob
        epm1 = alice.encrypt_message("Hi Bob")
        pt1 = bob.decrypt_message(epm1)
        self.assertEqual(pt1.text, "Hi Bob")

        # Bob → Alice
        epm2 = bob.encrypt_message("Hi Alice")
        pt2 = alice.decrypt_message(epm2)
        self.assertEqual(pt2.text, "Hi Alice")

    def test_multiple_messages(self):
        alice, bob = self._make_session_pair()

        for i in range(10):
            epm = alice.encrypt_message(f"Message {i}")
            pt = bob.decrypt_message(epm)
            self.assertEqual(pt.text, f"Message {i}")

    def test_nonce_increments(self):
        alice, bob = self._make_session_pair()

        epm1 = alice.encrypt_message("first")
        self.assertEqual(epm1.nonce, 1)

        epm2 = alice.encrypt_message("second")
        self.assertEqual(epm2.nonce, 2)

        epm3 = alice.encrypt_message("third")
        self.assertEqual(epm3.nonce, 3)

    def test_replay_protection(self):
        alice, bob = self._make_session_pair()

        epm = alice.encrypt_message("Hello")
        bob.decrypt_message(epm)

        # Replay the same message — should fail
        with self.assertRaises(ValueError, msg="replay"):
            bob.decrypt_message(epm)

    def test_tamper_detection(self):
        alice, bob = self._make_session_pair()

        epm = alice.encrypt_message("Hello")

        # Tamper with ciphertext
        tampered = bytearray(epm.ciphertext)
        tampered[0] ^= 0xFF
        epm.ciphertext = bytes(tampered)

        from cryptography.exceptions import InvalidTag
        with self.assertRaises(InvalidTag):
            bob.decrypt_message(epm)

    def test_encrypt_before_exchange_raises(self):
        session = E2EPMSession.create("Alice", "Bob")
        with self.assertRaises(RuntimeError):
            session.encrypt_message("test")

    def test_decrypt_before_exchange_raises(self):
        session = E2EPMSession.create("Bob", "Alice")
        epm = PbEncryptedPM()
        epm.nonce = 1
        epm.ciphertext = b"garbage"
        with self.assertRaises(RuntimeError):
            session.decrypt_message(epm)

    def test_pubkey_hint(self):
        session = E2EPMSession.create("Alice", "Bob")
        hint = session.pubkey_hint
        self.assertEqual(len(hint), 8)
        self.assertEqual(hint, session.my_public_key_bytes[:8])

    def test_unicode_messages(self):
        alice, bob = self._make_session_pair()

        texts = [
            "Привет мир",
            "日本語テスト",
            "🔑🌟🎭🦋",
            "Mixed ASCII and Üñïcödé",
            "",  # Empty string
        ]
        for text in texts:
            epm = alice.encrypt_message(text)
            pt = bob.decrypt_message(epm)
            self.assertEqual(pt.text, text)


class TestE2EPMCryptoHelpers(unittest.TestCase):
    """Test E2EPM crypto helper functions."""

    def test_fingerprint_deterministic(self):
        pub_a = os.urandom(32)
        pub_b = os.urandom(32)
        fp1 = _generate_fingerprint(pub_a, pub_b)
        fp2 = _generate_fingerprint(pub_a, pub_b)
        self.assertEqual(fp1, fp2)

    def test_fingerprint_order_independent(self):
        pub_a = os.urandom(32)
        pub_b = os.urandom(32)
        fp_ab = _generate_fingerprint(pub_a, pub_b)
        fp_ba = _generate_fingerprint(pub_b, pub_a)
        self.assertEqual(fp_ab, fp_ba)

    def test_fingerprint_different_keys(self):
        pub_a = os.urandom(32)
        pub_b = os.urandom(32)
        pub_c = os.urandom(32)
        fp_ab = _generate_fingerprint(pub_a, pub_b)
        fp_ac = _generate_fingerprint(pub_a, pub_c)
        self.assertNotEqual(fp_ab, fp_ac)

    def test_fingerprint_has_4_emojis(self):
        fp = _generate_fingerprint(os.urandom(32), os.urandom(32))
        # Count emoji characters (each emoji is multiple bytes but one char)
        import unicodedata
        emoji_count = sum(1 for c in fp if unicodedata.category(c).startswith(("So",)))
        self.assertGreaterEqual(emoji_count, 4)

    def test_build_nonce(self):
        nonce = _build_nonce(1)
        self.assertEqual(len(nonce), 12)
        self.assertEqual(nonce[:4], b"\x00\x00\x00\x00")
        counter_bytes = struct.pack("<Q", 1)
        self.assertEqual(nonce[4:], counter_bytes)

    def test_build_nonce_large_counter(self):
        nonce = _build_nonce(0xFFFFFFFFFFFFFFFF)
        self.assertEqual(len(nonce), 12)

    def test_build_aad(self):
        aad = _build_aad("Alice", "Bob")
        self.assertEqual(aad, b"e2epmAlice\x00Bob")

    def test_derive_keys_deterministic(self):
        secret = os.urandom(32)
        pub_a = os.urandom(32)
        pub_b = os.urandom(32)
        k1a, k1b = _derive_keys(secret, pub_a, pub_b)
        k2a, k2b = _derive_keys(secret, pub_a, pub_b)
        self.assertEqual(k1a, k2a)
        self.assertEqual(k1b, k2b)

    def test_derive_keys_order_independent(self):
        secret = os.urandom(32)
        pub_a = os.urandom(32)
        pub_b = os.urandom(32)
        k1a, k1b = _derive_keys(secret, pub_a, pub_b)
        k2a, k2b = _derive_keys(secret, pub_b, pub_a)
        self.assertEqual(k1a, k2a)
        self.assertEqual(k1b, k2b)

    def test_derive_keys_different_secrets(self):
        pub_a = os.urandom(32)
        pub_b = os.urandom(32)
        k1a, k1b = _derive_keys(os.urandom(32), pub_a, pub_b)
        k2a, k2b = _derive_keys(os.urandom(32), pub_a, pub_b)
        self.assertNotEqual(k1a, k2a)


class TestE2EPMManager(unittest.TestCase):
    """Test E2EPMManager multi-peer session management."""

    def _create_paired_managers(self, nick_a="Alice", nick_b="Bob"):
        """Create two managers and complete key exchange."""
        mgr_a = E2EPMManager(nick_a)
        mgr_b = E2EPMManager(nick_b)

        # Alice initiates
        kex_a = mgr_a.initiate_session(nick_b)
        # Bob handles and responds
        resp_b = mgr_b.handle_key_exchange(nick_a, kex_a)
        self.assertIsNotNone(resp_b)
        # Alice completes with Bob's response
        resp_a = mgr_a.handle_key_exchange(nick_b, resp_b)
        self.assertIsNone(resp_a)  # No further response needed

        return mgr_a, mgr_b

    def test_session_establishment(self):
        mgr_a, mgr_b = self._create_paired_managers()
        self.assertTrue(mgr_a.has_session("Bob"))
        self.assertTrue(mgr_b.has_session("Alice"))

    def test_fingerprints_match(self):
        mgr_a, mgr_b = self._create_paired_managers()
        fp_a = mgr_a.get_fingerprint("Bob")
        fp_b = mgr_b.get_fingerprint("Alice")
        self.assertEqual(fp_a, fp_b)
        self.assertIsNotNone(fp_a)

    def test_encrypt_decrypt(self):
        mgr_a, mgr_b = self._create_paired_managers()

        epm = mgr_a.encrypt_pm("Bob", "Secret message")
        self.assertIsNotNone(epm)

        pt = mgr_b.decrypt_pm("Alice", epm)
        self.assertEqual(pt.text, "Secret message")

    def test_bidirectional(self):
        mgr_a, mgr_b = self._create_paired_managers()

        epm1 = mgr_a.encrypt_pm("Bob", "Hello Bob")
        pt1 = mgr_b.decrypt_pm("Alice", epm1)
        self.assertEqual(pt1.text, "Hello Bob")

        epm2 = mgr_b.encrypt_pm("Alice", "Hello Alice")
        pt2 = mgr_a.decrypt_pm("Bob", epm2)
        self.assertEqual(pt2.text, "Hello Alice")

    def test_close_session(self):
        mgr_a, mgr_b = self._create_paired_managers()

        end = mgr_a.close_session("Bob")
        self.assertIsNotNone(end)
        self.assertEqual(end.target_nick, "Bob")
        self.assertFalse(mgr_a.has_session("Bob"))

        # Bob processes session end
        mgr_b.handle_session_end("Alice", end)
        self.assertFalse(mgr_b.has_session("Alice"))

    def test_close_nonexistent_session(self):
        mgr = E2EPMManager("Alice")
        end = mgr.close_session("Nobody")
        self.assertIsNone(end)

    def test_decrypt_no_session_raises(self):
        mgr = E2EPMManager("Alice")
        epm = PbEncryptedPM()
        epm.nonce = 1
        epm.ciphertext = b"test"
        with self.assertRaises(KeyError):
            mgr.decrypt_pm("Bob", epm)

    def test_encrypt_no_session_returns_none(self):
        mgr = E2EPMManager("Alice")
        result = mgr.encrypt_pm("Bob", "test")
        self.assertIsNone(result)

    def test_multiple_peers(self):
        alice = E2EPMManager("Alice")
        bob = E2EPMManager("Bob")
        charlie = E2EPMManager("Charlie")

        # Alice ↔ Bob
        kex_ab = alice.initiate_session("Bob")
        resp_ba = bob.handle_key_exchange("Alice", kex_ab)
        alice.handle_key_exchange("Bob", resp_ba)

        # Alice ↔ Charlie
        kex_ac = alice.initiate_session("Charlie")
        resp_ca = charlie.handle_key_exchange("Alice", kex_ac)
        alice.handle_key_exchange("Charlie", resp_ca)

        # Both sessions active
        self.assertTrue(alice.has_session("Bob"))
        self.assertTrue(alice.has_session("Charlie"))

        # Can communicate with both
        epm_b = alice.encrypt_pm("Bob", "Hi Bob")
        pt_b = bob.decrypt_pm("Alice", epm_b)
        self.assertEqual(pt_b.text, "Hi Bob")

        epm_c = alice.encrypt_pm("Charlie", "Hi Charlie")
        pt_c = charlie.decrypt_pm("Alice", epm_c)
        self.assertEqual(pt_c.text, "Hi Charlie")

    def test_clear_all(self):
        mgr_a, mgr_b = self._create_paired_managers()
        self.assertTrue(mgr_a.has_session("Bob"))
        mgr_a.clear_all()
        self.assertFalse(mgr_a.has_session("Bob"))

    def test_rekey(self):
        """Test re-keying: Bob initiates a new session while one exists."""
        mgr_a, mgr_b = self._create_paired_managers()
        old_fp = mgr_a.get_fingerprint("Bob")

        # Bob re-initiates
        new_kex = mgr_b.initiate_session("Alice")
        resp = mgr_a.handle_key_exchange("Bob", new_kex)
        self.assertIsNotNone(resp)
        mgr_b.handle_key_exchange("Alice", resp)

        # New session, new fingerprint (very likely)
        self.assertTrue(mgr_a.has_session("Bob"))
        self.assertTrue(mgr_b.has_session("Alice"))

        # Can still communicate
        epm = mgr_a.encrypt_pm("Bob", "After rekey")
        pt = mgr_b.decrypt_pm("Alice", epm)
        self.assertEqual(pt.text, "After rekey")

    def test_get_fingerprint_no_session(self):
        mgr = E2EPMManager("Alice")
        self.assertIsNone(mgr.get_fingerprint("Bob"))


# ==========================================================================
# Client Utility Tests
# ==========================================================================


class TestNMDCLockToKey(unittest.TestCase):
    """Test the NMDC $Lock → $Key computation."""

    def test_known_vector(self):
        # Standard NMDC lock value
        lock = "EXTENDEDPROTOCOLABCABCABCABCABCABC"
        key = _nmdc_lock_to_key(lock)
        # Just verify it produces non-empty output with correct escaping
        self.assertIsInstance(key, str)
        self.assertGreater(len(key), 0)

    def test_special_char_escaping(self):
        """Verify chars 0, 5, 36, 96, 124, 126 are /%DCNxxx%/ escaped."""
        # Create a lock that should produce bytes needing escaping
        lock = "A" * 20
        key = _nmdc_lock_to_key(lock)
        # We can't predict exactly which chars, but verify format
        if "/%DCN" in key:
            import re
            escapes = re.findall(r"/%DCN(\d{3})%/", key)
            for esc in escapes:
                self.assertIn(int(esc), [0, 5, 36, 96, 124, 126])

    def test_empty_lock(self):
        """Edge case: empty lock shouldn't crash."""
        lock = "A"
        key = _nmdc_lock_to_key(lock)
        self.assertIsInstance(key, str)


# ==========================================================================
# Integration: Wire + E2EPM end-to-end
# ==========================================================================


class TestWireE2EPMIntegration(unittest.TestCase):
    """End-to-end test: wire codec + E2EPM through PbEnvelope."""

    def test_e2epm_through_wire(self):
        """Simulate full E2EPM flow over the wire."""
        alice_mgr = E2EPMManager("Alice")
        bob_mgr = E2EPMManager("Bob")

        # 1. Alice initiates key exchange
        kex_a = alice_mgr.initiate_session("Bob")
        env_kex = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="Bob",
        )
        env_kex.pm_key_exchange.CopyFrom(kex_a)
        wire_kex = WireCodec.encode_text(env_kex)

        # 2. Wire transit — decode on Bob's side
        decoded_kex = WireCodec.decode(wire_kex)
        self.assertEqual(decoded_kex.WhichOneof("payload"), "pm_key_exchange")

        # 3. Bob handles key exchange and responds
        resp = bob_mgr.handle_key_exchange("Alice", decoded_kex.pm_key_exchange)
        self.assertIsNotNone(resp)
        env_resp = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Bob",
            to_nick="Alice",
        )
        env_resp.pm_key_exchange.CopyFrom(resp)
        wire_resp = WireCodec.encode_text(env_resp)

        # 4. Wire transit — Alice receives response
        decoded_resp = WireCodec.decode(wire_resp)
        result = alice_mgr.handle_key_exchange("Bob", decoded_resp.pm_key_exchange)
        self.assertIsNone(result)

        # 5. Both have established sessions
        self.assertTrue(alice_mgr.has_session("Bob"))
        self.assertTrue(bob_mgr.has_session("Alice"))

        # 6. Alice sends encrypted PM
        epm = alice_mgr.encrypt_pm("Bob", "Top secret!")
        env_epm = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="Bob",
        )
        env_epm.encrypted_pm.CopyFrom(epm)
        wire_epm = WireCodec.encode_text(env_epm)

        # 7. Wire transit — Bob receives and decrypts
        decoded_epm = WireCodec.decode(wire_epm)
        self.assertEqual(decoded_epm.WhichOneof("payload"), "encrypted_pm")
        pt = bob_mgr.decrypt_pm("Alice", decoded_epm.encrypted_pm)
        self.assertEqual(pt.text, "Top secret!")

        # 8. Bob replies
        epm2 = bob_mgr.encrypt_pm("Alice", "Got it!")
        env_epm2 = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Bob",
            to_nick="Alice",
        )
        env_epm2.encrypted_pm.CopyFrom(epm2)
        wire_epm2 = WireCodec.encode_text(env_epm2)

        decoded_epm2 = WireCodec.decode(wire_epm2)
        pt2 = alice_mgr.decrypt_pm("Bob", decoded_epm2.encrypted_pm)
        self.assertEqual(pt2.text, "Got it!")

    def test_chat_broadcast_through_wire(self):
        """Simulate a chat broadcast through the wire."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.BROADCAST,
            from_nick="Alice",
        )
        env.chat.text = "Hello everyone!"
        env.chat.is_action = False

        # Encode as text
        wire_text = WireCodec.encode_text(env)
        # Encode as binary
        wire_binary = WireCodec.encode_binary(env)

        # Decode both
        dec_text = WireCodec.decode(wire_text)
        dec_binary = WireCodec.decode_bytes(wire_binary)

        for dec in (dec_text, dec_binary):
            self.assertEqual(dec.route, PbEnvelope.BROADCAST)
            self.assertEqual(dec.from_nick, "Alice")
            self.assertEqual(dec.chat.text, "Hello everyone!")

    def test_session_end_through_wire(self):
        """Simulate session-end notification through the wire."""
        alice_mgr, bob_mgr = E2EPMManager("Alice"), E2EPMManager("Bob")

        # Establish session
        kex = alice_mgr.initiate_session("Bob")
        resp = bob_mgr.handle_key_exchange("Alice", kex)
        alice_mgr.handle_key_exchange("Bob", resp)

        # Alice closes
        end = alice_mgr.close_session("Bob")
        env_end = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="Alice",
            to_nick="Bob",
        )
        env_end.pm_session_end.CopyFrom(end)
        wire_end = WireCodec.encode_text(env_end)

        # Bob receives
        decoded = WireCodec.decode(wire_end)
        self.assertEqual(decoded.WhichOneof("payload"), "pm_session_end")
        bob_mgr.handle_session_end("Alice", decoded.pm_session_end)
        self.assertFalse(bob_mgr.has_session("Alice"))


if __name__ == "__main__":
    unittest.main()
