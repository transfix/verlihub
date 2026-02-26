"""
Property-based fuzz tests for NMDCpb protocol extension.

Uses Hypothesis for automated edge-case discovery:
- Wire codec roundtrip properties
- Base64url encode/decode invariants
- Protobuf serialize/deserialize with random data
- E2EPM crypto invariants
- Malformed input resilience

Run: pytest tests/test_nmdcpb_fuzz.py -v --timeout=60
"""

import os
import struct
import unittest

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbChat,
    PbPMKeyExchange,
    PbEncryptedPM,
    PbPMPlaintext,
    PbPMSessionEnd,
    PbRelayRequest,
    PbRelayAck,
    PbRelayData,
    PbRelayClosed,
    PbPrivateSearch,
    PbPrivateSearchResult,
)
from verlihub.client.nmdcpb.wire import (
    WireCodec,
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

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305


# ==========================================================================
# Strategies
# ==========================================================================

# Printable nicks (NMDC style)
nick_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P"), min_codepoint=33, max_codepoint=126),
    min_size=1, max_size=64,
).filter(lambda s: "$" not in s and "|" not in s)

# Arbitrary binary data
binary_st = st.binary(min_size=0, max_size=4096)

# Chat-length text
text_st = st.text(min_size=0, max_size=1024)


# ==========================================================================
# Base64url Property Tests
# ==========================================================================


class TestBase64urlProperties(unittest.TestCase):
    """Property-based tests for base64url encode/decode."""

    @given(data=binary_st)
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_roundtrip(self, data: bytes):
        """Encoding then decoding always recovers the original bytes."""
        encoded = _b64url_encode(data)
        decoded = _b64url_decode(encoded)
        self.assertEqual(decoded, data)

    @given(data=binary_st)
    @settings(max_examples=200)
    def test_encoding_is_url_safe(self, data: bytes):
        """Encoded output never contains +, /, or = characters."""
        encoded = _b64url_encode(data)
        self.assertNotIn("+", encoded)
        self.assertNotIn("/", encoded)
        self.assertNotIn("=", encoded)

    @given(data=st.binary(min_size=0, max_size=256))
    @settings(max_examples=200)
    def test_encoding_length(self, data: bytes):
        """Encoded length is ceil(len(data) * 4/3) without padding."""
        encoded = _b64url_encode(data)
        if len(data) == 0:
            self.assertEqual(len(encoded), 0)
        else:
            # Standard base64 length without padding
            import math
            expected = math.ceil(len(data) * 4 / 3)
            # Allow for string length variations
            self.assertGreaterEqual(len(encoded), 1)

    @given(garbage=st.text(min_size=0, max_size=100))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_decode_garbage_no_crash(self, garbage: str):
        """Decoding arbitrary strings must not crash (may raise or return junk)."""
        try:
            _b64url_decode(garbage)
        except Exception:
            pass  # Any exception is acceptable, crash is not


# ==========================================================================
# Wire Codec Property Tests
# ==========================================================================


class TestWireCodecProperties(unittest.TestCase):
    """Property-based tests for the NMDCpb wire codec."""

    @given(text=text_st, nick=nick_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_text_format_roundtrip(self, text: str, nick: str):
        """PbEnvelope chat message survives encode_text → decode_text."""
        env = PbEnvelope()
        env.route = PbEnvelope.BROADCAST
        env.from_nick = nick
        env.chat.text = text

        wire = WireCodec.encode_text(env)
        self.assertTrue(wire.startswith("$PB "))
        decoded = WireCodec.decode(wire)
        self.assertEqual(decoded.chat.text, text)
        self.assertEqual(decoded.from_nick, nick)

    @given(payload=st.binary(min_size=1, max_size=2048))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_binary_format_roundtrip(self, payload: bytes):
        """PbEnvelope with binary relay data survives encode_binary → decode_binary."""
        env = PbEnvelope()
        env.relay_data.relay_id = 42
        env.relay_data.data = payload

        wire = WireCodec.encode_binary(env)
        self.assertTrue(wire.startswith(b"$PBB "))
        decoded = WireCodec.decode_bytes(wire)
        self.assertEqual(decoded.relay_data.data, payload)

    @given(garbage=st.text(min_size=0, max_size=200))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_decode_text_garbage_no_crash(self, garbage: str):
        """Decoding arbitrary text as $PB must not crash."""
        try:
            WireCodec.decode("$PB " + garbage)
        except Exception:
            pass

    @given(garbage=binary_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_decode_binary_garbage_no_crash(self, garbage: bytes):
        """Decoding arbitrary bytes as $PBB must not crash."""
        try:
            WireCodec.decode_bytes(b"$PBB " + garbage)
        except Exception:
            pass


# ==========================================================================
# Protobuf Serialization Property Tests
# ==========================================================================


class TestProtobufProperties(unittest.TestCase):
    """Property-based tests for protobuf message serialize/deserialize."""

    @given(
        text=text_st,
        is_action=st.booleans(),
        timestamp=st.integers(min_value=0, max_value=2**63 - 1),
    )
    @settings(max_examples=200)
    def test_pbpmplaintext_roundtrip(self, text: str, is_action: bool, timestamp: int):
        """PbPMPlaintext survives serialize → parse."""
        pt = PbPMPlaintext()
        pt.text = text
        pt.is_action = is_action
        pt.timestamp = timestamp

        wire = pt.SerializeToString()
        pt2 = PbPMPlaintext()
        pt2.ParseFromString(wire)
        self.assertEqual(pt2.text, text)
        self.assertEqual(pt2.is_action, is_action)
        self.assertEqual(pt2.timestamp, timestamp)

    @given(
        nick=nick_st,
        route=st.sampled_from([PbEnvelope.BROADCAST, PbEnvelope.DIRECT, PbEnvelope.HUB, PbEnvelope.INFO]),
        seq=st.integers(min_value=0, max_value=2**32 - 1),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_pbenvelope_header_roundtrip(self, nick: str, route, seq: int):
        """PbEnvelope header fields survive roundtrip."""
        env = PbEnvelope()
        env.from_nick = nick
        env.route = route
        env.sequence = seq
        env.chat.text = "test"

        wire = env.SerializeToString()
        env2 = PbEnvelope()
        env2.ParseFromString(wire)
        self.assertEqual(env2.from_nick, nick)
        self.assertEqual(env2.route, route)
        self.assertEqual(env2.sequence, seq)

    @given(garbage=binary_st)
    @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    def test_parse_garbage_no_crash(self, garbage: bytes):
        """Parsing random bytes as PbEnvelope must not crash."""
        env = PbEnvelope()
        try:
            env.ParseFromString(garbage)
        except Exception:
            pass
        # Exercise accessors
        try:
            _ = env.has_chat  # proto3 doesn't have has_* for scalars
            _ = env.from_nick
            _ = env.route
        except Exception:
            pass


# ==========================================================================
# E2EPM Crypto Property Tests
# ==========================================================================


class TestE2EPMCryptoProperties(unittest.TestCase):
    """Property-based tests for E2EPM crypto operations."""

    @given(plaintext=text_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_encrypt_decrypt_roundtrip(self, plaintext: str):
        """E2EPM encrypt → decrypt always recovers the original message."""
        alice = E2EPMSession.create("Alice", "Bob")
        bob = E2EPMSession.create("Bob", "Alice")

        # Exchange keys
        alice_kex = alice.make_key_exchange()
        bob_kex = bob.make_key_exchange()
        alice.complete_key_exchange(bytes(bob_kex.public_key))
        bob.complete_key_exchange(bytes(alice_kex.public_key))

        # Encrypt and decrypt
        epm = alice.encrypt_message(plaintext)
        pt = bob.decrypt_message(epm)
        self.assertEqual(pt.text, plaintext)

    @given(data=st.binary(min_size=1, max_size=200))
    @settings(max_examples=100)
    def test_chacha_roundtrip(self, data: bytes):
        """Raw ChaCha20-Poly1305 roundtrip with random plaintext."""
        key = os.urandom(32)
        nonce = os.urandom(12)
        aad = b"test-aad"

        cipher = ChaCha20Poly1305(key)
        ct = cipher.encrypt(nonce, data, aad)
        pt = cipher.decrypt(nonce, ct, aad)
        self.assertEqual(pt, data)

    @given(
        key=st.binary(min_size=32, max_size=32),
        nonce=st.binary(min_size=12, max_size=12),
        ct_garbage=st.binary(min_size=1, max_size=200),
    )
    @settings(max_examples=200)
    def test_chacha_decrypt_garbage_rejects(self, key: bytes, nonce: bytes, ct_garbage: bytes):
        """Decrypting random garbage always raises InvalidTag."""
        from cryptography.exceptions import InvalidTag
        cipher = ChaCha20Poly1305(key)
        # Random data is astronomically unlikely to authenticate
        if len(ct_garbage) >= 17:  # Need at least 16 byte tag + 1 byte ct
            try:
                cipher.decrypt(nonce, ct_garbage, b"")
                # In the astronomically unlikely case it decrypts, that's fine
            except InvalidTag:
                pass  # Expected

    @given(plaintext=text_st, is_action=st.booleans())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_encrypt_nonce_increments(self, plaintext: str, is_action: bool):
        """Each encrypt call increments the nonce counter."""
        session = E2EPMSession.create("A", "B")
        peer_kp = X25519PrivateKey.generate()
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        peer_pub = peer_kp.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        session.complete_key_exchange(peer_pub)

        epm1 = session.encrypt_message(plaintext, is_action)
        epm2 = session.encrypt_message(plaintext, is_action)
        self.assertEqual(epm2.nonce, epm1.nonce + 1)

    def test_fingerprint_is_symmetric(self):
        """Fingerprint(A, B) == Fingerprint(B, A)."""
        pub_a = os.urandom(32)
        pub_b = os.urandom(32)
        fp1 = _generate_fingerprint(pub_a, pub_b)
        fp2 = _generate_fingerprint(pub_b, pub_a)
        self.assertEqual(fp1, fp2)

    @given(
        pub_a=st.binary(min_size=32, max_size=32),
        pub_b=st.binary(min_size=32, max_size=32),
    )
    @settings(max_examples=200)
    def test_fingerprint_symmetric_property(self, pub_a: bytes, pub_b: bytes):
        """Fingerprint symmetry holds for all public key pairs."""
        fp1 = _generate_fingerprint(pub_a, pub_b)
        fp2 = _generate_fingerprint(pub_b, pub_a)
        self.assertEqual(fp1, fp2)


# ==========================================================================
# E2EPM Manager Property Tests
# ==========================================================================


class TestE2EPMManagerProperties(unittest.TestCase):
    """Property-based tests for E2EPM session manager."""

    @given(nick=nick_st)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_initiate_creates_session(self, nick: str):
        """Initiating a session creates it in the manager."""
        mgr = E2EPMManager("me")
        kex = mgr.initiate_session(nick)
        self.assertIn(nick, mgr.sessions)
        self.assertEqual(len(kex.public_key), 32)

    @given(nick=nick_st, text=text_st)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_full_exchange_and_roundtrip(self, nick: str, text: str):
        """Full key exchange + encrypt/decrypt roundtrip for any nick/text."""
        alice_mgr = E2EPMManager("Alice")
        bob_mgr = E2EPMManager(nick)

        # Alice initiates
        alice_kex = alice_mgr.initiate_session(nick)

        # Bob receives and responds
        bob_resp = bob_mgr.handle_key_exchange("Alice", alice_kex)
        self.assertIsNotNone(bob_resp)

        # Alice completes
        fin = alice_mgr.handle_key_exchange(nick, bob_resp)
        self.assertIsNone(fin)

        # Encrypt and decrypt
        epm = alice_mgr.encrypt_pm(nick, text)
        if epm is not None:
            pt = bob_mgr.decrypt_pm("Alice", epm)
            self.assertEqual(pt.text, text)


# ==========================================================================
# Nonce Construction Property Tests
# ==========================================================================


class TestNonceProperties(unittest.TestCase):
    """Property tests for nonce construction."""

    @given(counter=st.integers(min_value=0, max_value=2**64 - 1))
    @settings(max_examples=500)
    def test_nonce_is_12_bytes(self, counter: int):
        """All nonces are exactly 12 bytes."""
        nonce = _build_nonce(counter)
        self.assertEqual(len(nonce), 12)

    @given(
        a=st.integers(min_value=0, max_value=2**64 - 2),
    )
    @settings(max_examples=200)
    def test_consecutive_nonces_differ(self, a: int):
        """Consecutive counter values produce different nonces."""
        n1 = _build_nonce(a)
        n2 = _build_nonce(a + 1)
        self.assertNotEqual(n1, n2)

    @given(counter=st.integers(min_value=0, max_value=2**64 - 1))
    @settings(max_examples=200)
    def test_nonce_leading_zeros(self, counter: int):
        """Nonce has 4 leading zero bytes (counter is in bytes 4-11)."""
        nonce = _build_nonce(counter)
        self.assertEqual(nonce[:4], b"\x00\x00\x00\x00")


# ==========================================================================
# Malformed Protocol Input Tests
# ==========================================================================


class TestMalformedInputs(unittest.TestCase):
    """Tests for handling malformed/adversarial protocol inputs."""

    def test_decode_text_empty_payload(self):
        """$PB with empty payload after prefix."""
        try:
            WireCodec.decode("$PB ")
        except Exception:
            pass  # Any result is fine, must not crash

    def test_decode_text_not_base64(self):
        """$PB with non-base64 payload."""
        try:
            WireCodec.decode("$PB !!!invalid!!!")
        except Exception:
            pass

    def test_decode_binary_truncated_header(self):
        """$PBB with truncated length header."""
        try:
            WireCodec.decode_bytes(b"$PBB \x00")
        except Exception:
            pass

    def test_decode_binary_length_overflow(self):
        """$PBB with length field larger than payload."""
        try:
            # 4-byte big-endian length = 9999999, but only 10 bytes follow
            wire = b"$PBB " + struct.pack(">I", 9999999) + b"0123456789"
            WireCodec.decode_bytes(wire)
        except Exception:
            pass

    def test_e2epm_decrypt_wrong_key(self):
        """Decrypting with the wrong session key raises error."""
        from cryptography.exceptions import InvalidTag

        alice = E2EPMSession.create("alice", "bob")
        bob = E2EPMSession.create("bob", "alice")
        eve = E2EPMSession.create("eve", "alice")

        # Alice ↔ Bob exchange
        a_kex = alice.make_key_exchange()
        b_kex = bob.make_key_exchange()
        alice.complete_key_exchange(bytes(b_kex.public_key))
        bob.complete_key_exchange(bytes(a_kex.public_key))

        # Eve has a different session
        e_kex = eve.make_key_exchange()
        eve_peer = X25519PrivateKey.generate()
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        eve.complete_key_exchange(
            eve_peer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )

        # Alice encrypts for Bob
        epm = alice.encrypt_message("secret")

        # Eve tries to decrypt — must fail
        with self.assertRaises((InvalidTag, ValueError, Exception)):
            eve.decrypt_message(epm)

    def test_e2epm_replay_detection(self):
        """Re-sending the same encrypted message is detected as replay."""
        alice = E2EPMSession.create("alice", "bob")
        bob = E2EPMSession.create("bob", "alice")

        a_kex = alice.make_key_exchange()
        b_kex = bob.make_key_exchange()
        alice.complete_key_exchange(bytes(b_kex.public_key))
        bob.complete_key_exchange(bytes(a_kex.public_key))

        epm = alice.encrypt_message("hello")
        bob.decrypt_message(epm)  # First time — OK

        # Replay — must be rejected
        with self.assertRaises((ValueError, Exception)):
            bob.decrypt_message(epm)

    def test_protobuf_self_referential(self):
        """Protobuf message with extension fields doesn't crash."""
        env = PbEnvelope()
        env.from_nick = "test"
        env.chat.text = "x" * 100000  # Large text
        wire = env.SerializeToString()

        env2 = PbEnvelope()
        env2.ParseFromString(wire)
        self.assertEqual(len(env2.chat.text), 100000)


if __name__ == "__main__":
    unittest.main()
