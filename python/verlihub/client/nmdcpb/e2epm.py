"""
E2EPM — End-to-End Encrypted Private Messages for NMDCpb.

Implements:
- X25519 key exchange for session establishment
- HKDF-SHA256 key derivation
- ChaCha20-Poly1305 AEAD encryption/decryption
- Session lifecycle (establish, encrypt, decrypt, close)
- Nonce tracking and replay protection
- Key fingerprint generation for verification
- TOFU (Trust On First Use) key continuity

Cryptographic design:
    Key exchange:    X25519 (RFC 7748)
    Key derivation:  HKDF-SHA256 (RFC 5869)
    Encryption:      ChaCha20-Poly1305 (RFC 8439)

Wire flow:
    1. A→Hub→B: PbPMKeyExchange { pubkey: A_pub }
    2. B→Hub→A: PbPMKeyExchange { pubkey: B_pub }
    3. Both derive: shared_secret = X25519(my_priv, their_pub)
    4. Keys: HKDF(shared_secret, salt="nmdcpb-e2epm-v1", info=sort(A_pub,B_pub))
    5. A→Hub→B: PbEncryptedPM { nonce: 1, ciphertext: encrypt(PbPMPlaintext) }
"""

from __future__ import annotations

import hashlib
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbPMKeyExchange,
    PbEncryptedPM,
    PbPMPlaintext,
    PbPMSessionEnd,
)

# Constants
E2EPM_SALT = b"nmdcpb-e2epm-v1"
E2EPM_PROTOCOL_VERSION = 1
PUBKEY_HINT_LEN = 8  # First 8 bytes of public key as hint
NONCE_LEN = 12  # ChaCha20-Poly1305 nonce length

# Emoji fingerprint alphabet (emoji are memorable and hard to confuse)
_FINGERPRINT_EMOJIS = [
    "🍎", "🌊", "🎸", "🔑", "🌟", "🎭", "🦋", "🌈",
    "🎯", "🍀", "🔮", "🎪", "🦊", "🌙", "🎵", "🔥",
    "🍉", "⚡", "🎲", "🌺", "🦉", "🎨", "🌍", "💎",
    "🎹", "🌲", "🦁", "🎻", "🌸", "⭐", "🎬", "🌿",
]


def _generate_fingerprint(pub_a: bytes, pub_b: bytes) -> str:
    """Generate a 4-emoji fingerprint from two public keys.

    The fingerprint is deterministic: same keys always produce the same
    emojis regardless of order (we sort the keys first).
    """
    # Sort keys so fingerprint is the same on both sides
    sorted_keys = b"".join(sorted([pub_a, pub_b]))
    h = hashlib.sha256(sorted_keys).digest()
    # Use first 4 bytes to pick 4 emojis
    emojis = []
    for i in range(4):
        idx = h[i] % len(_FINGERPRINT_EMOJIS)
        emojis.append(_FINGERPRINT_EMOJIS[idx])
    return "".join(emojis)


def _derive_keys(
    shared_secret: bytes, pub_a: bytes, pub_b: bytes
) -> tuple[bytes, bytes]:
    """Derive encryption keys from X25519 shared secret.

    Returns (key_for_smaller_pubkey_holder, key_for_larger_pubkey_holder).
    Each side determines which key to use for send vs receive based on
    whether their public key is the lexicographically smaller one.
    """
    info = b"".join(sorted([pub_a, pub_b]))

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=64,  # 2 × 32-byte keys
        salt=E2EPM_SALT,
        info=info,
    )
    key_material = hkdf.derive(shared_secret)
    return key_material[:32], key_material[32:]


def _build_nonce(counter: int) -> bytes:
    """Build a 12-byte nonce from a counter: 4 zero bytes + 8-byte LE counter."""
    return b"\x00\x00\x00\x00" + struct.pack("<Q", counter)


def _build_aad(sender_nick: str, target_nick: str) -> bytes:
    """Build AAD for ChaCha20-Poly1305: 'e2epm' || sender || \\x00 || target."""
    return b"e2epm" + sender_nick.encode("utf-8") + b"\x00" + target_nick.encode("utf-8")


@dataclass
class E2EPMSession:
    """An E2EPM session with a single peer.

    Manages key exchange state, encryption/decryption, nonce tracking.
    """

    my_nick: str
    peer_nick: str
    my_private_key: X25519PrivateKey = field(repr=False)
    my_public_key_bytes: bytes = field(init=False)
    peer_public_key_bytes: Optional[bytes] = None
    shared_secret: Optional[bytes] = field(default=None, repr=False)
    send_key: Optional[bytes] = field(default=None, repr=False)
    recv_key: Optional[bytes] = field(default=None, repr=False)
    send_counter: int = 0
    recv_counter: int = 0  # Highest nonce received (for replay protection)
    established: bool = False
    fingerprint: str = ""
    created_at: float = field(default_factory=time.time)

    # Key rotation tracking
    messages_sent: int = 0
    messages_recvd: int = 0
    last_rotation: float = 0.0  # Timestamp of last rotation (0 = never)

    def __post_init__(self):
        raw = self.my_private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        object.__setattr__(self, "my_public_key_bytes", raw)

    @classmethod
    def create(cls, my_nick: str, peer_nick: str) -> E2EPMSession:
        """Create a new E2EPM session with a fresh keypair."""
        private_key = X25519PrivateKey.generate()
        return cls(my_nick=my_nick, peer_nick=peer_nick, my_private_key=private_key)

    def make_key_exchange(self) -> PbPMKeyExchange:
        """Create the PbPMKeyExchange message to send to the peer."""
        kex = PbPMKeyExchange()
        kex.target_nick = self.peer_nick
        kex.public_key = self.my_public_key_bytes
        kex.protocol_version = E2EPM_PROTOCOL_VERSION
        # Fingerprint will be set after we know the peer's key
        return kex

    def complete_key_exchange(self, peer_pubkey: bytes) -> None:
        """Complete key exchange after receiving peer's public key.

        Derives shared secret, session keys, and fingerprint.
        """
        self.peer_public_key_bytes = peer_pubkey
        peer_pub = X25519PublicKey.from_public_bytes(peer_pubkey)
        self.shared_secret = self.my_private_key.exchange(peer_pub)

        key_small, key_large = _derive_keys(
            self.shared_secret,
            self.my_public_key_bytes,
            self.peer_public_key_bytes,
        )

        # Assign send/recv keys based on which pubkey is lexicographically smaller
        if self.my_public_key_bytes < self.peer_public_key_bytes:
            self.send_key = key_small
            self.recv_key = key_large
        else:
            self.send_key = key_large
            self.recv_key = key_small

        self.fingerprint = _generate_fingerprint(
            self.my_public_key_bytes, self.peer_public_key_bytes
        )
        self.established = True
        self.messages_sent = 0
        self.messages_recvd = 0
        self.last_rotation = time.time()

    def encrypt_message(self, text: str, is_action: bool = False) -> PbEncryptedPM:
        """Encrypt a plaintext message for the peer.

        Returns a PbEncryptedPM ready to embed in a PbEnvelope.
        """
        if not self.established:
            raise RuntimeError("E2EPM session not established — key exchange incomplete")

        # Build plaintext protobuf
        pt = PbPMPlaintext()
        pt.text = text
        pt.is_action = is_action
        pt.timestamp = int(time.time() * 1000)
        plaintext_bytes = pt.SerializeToString()

        # Encrypt
        self.send_counter += 1
        nonce = _build_nonce(self.send_counter)
        aad = _build_aad(self.my_nick, self.peer_nick)
        cipher = ChaCha20Poly1305(self.send_key)
        ciphertext = cipher.encrypt(nonce, plaintext_bytes, aad)

        # Build wire message
        epm = PbEncryptedPM()
        epm.target_nick = self.peer_nick
        epm.nonce = self.send_counter
        epm.ciphertext = ciphertext
        epm.sender_pubkey_hint = self.my_public_key_bytes[:PUBKEY_HINT_LEN]

        self.messages_sent += 1

        return epm

    def decrypt_message(self, epm: PbEncryptedPM) -> PbPMPlaintext:
        """Decrypt an incoming PbEncryptedPM from the peer.

        Validates nonce ordering (replay protection) and AEAD tag.

        Raises:
            ValueError: If nonce is not greater than last received (replay)
            cryptography.exceptions.InvalidTag: If ciphertext was tampered with
        """
        if not self.established:
            raise RuntimeError("E2EPM session not established — key exchange incomplete")

        # Replay protection: nonce must be strictly increasing
        if epm.nonce <= self.recv_counter:
            raise ValueError(
                f"E2EPM replay detected: received nonce {epm.nonce} "
                f"<= last seen {self.recv_counter}"
            )

        # Decrypt
        nonce = _build_nonce(epm.nonce)
        aad = _build_aad(self.peer_nick, self.my_nick)  # Note: sender/target swapped
        cipher = ChaCha20Poly1305(self.recv_key)
        plaintext_bytes = cipher.decrypt(nonce, epm.ciphertext, aad)

        self.recv_counter = epm.nonce
        self.messages_recvd += 1

        pt = PbPMPlaintext()
        pt.ParseFromString(plaintext_bytes)
        return pt

    @property
    def pubkey_hint(self) -> bytes:
        """First 8 bytes of our public key (for sender identification)."""
        return self.my_public_key_bytes[:PUBKEY_HINT_LEN]


class E2EPMManager:
    """Manages E2EPM sessions for all peers.

    Handles session lifecycle, TOFU key continuity, and routing.
    """

    def __init__(self, my_nick: str):
        self.my_nick = my_nick
        self._sessions: dict[str, E2EPMSession] = {}  # peer_nick → session
        self._known_keys: dict[str, bytes] = {}  # peer_nick → last known pubkey (TOFU)
        self._pending_messages: dict[str, list[str]] = {}  # peer_nick → queued texts

    @property
    def sessions(self) -> dict[str, E2EPMSession]:
        """Active E2EPM sessions by peer nick."""
        return dict(self._sessions)

    def initiate_session(self, peer_nick: str) -> PbPMKeyExchange:
        """Start a new E2EPM session with a peer.

        Returns the PbPMKeyExchange message to send.
        """
        session = E2EPMSession.create(self.my_nick, peer_nick)
        self._sessions[peer_nick] = session
        return session.make_key_exchange()

    def handle_key_exchange(self, from_nick: str, kex: PbPMKeyExchange) -> Optional[PbPMKeyExchange]:
        """Handle an incoming PbPMKeyExchange.

        If we don't have a session yet, creates one and returns our key exchange.
        If we already have a pending session, completes the key exchange.

        Returns:
            PbPMKeyExchange to send back (if we're the responder), or None.
        """
        peer_pubkey = bytes(kex.public_key)

        # TOFU check
        key_warning = self._check_tofu(from_nick, peer_pubkey)

        if from_nick in self._sessions:
            session = self._sessions[from_nick]
            if not session.established:
                # We initiated, they're responding — complete exchange
                session.complete_key_exchange(peer_pubkey)
                self._known_keys[from_nick] = peer_pubkey
                return None
            else:
                # Re-keying: they want a new session
                session = E2EPMSession.create(self.my_nick, from_nick)
                self._sessions[from_nick] = session
                session.complete_key_exchange(peer_pubkey)
                self._known_keys[from_nick] = peer_pubkey
                response = session.make_key_exchange()
                return response
        else:
            # We're the responder — create session and complete exchange
            session = E2EPMSession.create(self.my_nick, from_nick)
            session.complete_key_exchange(peer_pubkey)
            self._sessions[from_nick] = session
            self._known_keys[from_nick] = peer_pubkey
            return session.make_key_exchange()

    def encrypt_pm(self, peer_nick: str, text: str, is_action: bool = False) -> Optional[PbEncryptedPM]:
        """Encrypt a PM for a peer.

        Returns PbEncryptedPM if session is established, None if pending.
        If no session exists, returns None (caller should initiate first).
        """
        session = self._sessions.get(peer_nick)
        if session and session.established:
            return session.encrypt_message(text, is_action)
        return None

    def decrypt_pm(self, from_nick: str, epm: PbEncryptedPM) -> PbPMPlaintext:
        """Decrypt an incoming encrypted PM.

        Raises:
            KeyError: If no session exists for this peer
            ValueError: If replay detected
            cryptography.exceptions.InvalidTag: If tampered
        """
        session = self._sessions.get(from_nick)
        if not session:
            raise KeyError(f"No E2EPM session with {from_nick}")
        return session.decrypt_message(epm)

    def close_session(self, peer_nick: str) -> Optional[PbPMSessionEnd]:
        """Close an E2EPM session with a peer.

        Returns PbPMSessionEnd message to send, or None if no session.
        """
        if peer_nick in self._sessions:
            del self._sessions[peer_nick]
            end = PbPMSessionEnd()
            end.target_nick = peer_nick
            end.reason = PbPMSessionEnd.NORMAL_CLOSE
            return end
        return None

    def handle_session_end(self, from_nick: str, end: PbPMSessionEnd) -> None:
        """Handle incoming PbPMSessionEnd — clean up session."""
        self._sessions.pop(from_nick, None)

    def get_fingerprint(self, peer_nick: str) -> Optional[str]:
        """Get the emoji fingerprint for a peer's session."""
        session = self._sessions.get(peer_nick)
        if session and session.established:
            return session.fingerprint
        return None

    def has_session(self, peer_nick: str) -> bool:
        """Check if we have an active session with a peer."""
        session = self._sessions.get(peer_nick)
        return session is not None and session.established

    def _check_tofu(self, peer_nick: str, new_pubkey: bytes) -> Optional[str]:
        """TOFU (Trust On First Use) key continuity check.

        Returns a warning string if the key changed unexpectedly, None otherwise.
        """
        old_key = self._known_keys.get(peer_nick)
        if old_key is not None and old_key != new_pubkey:
            return (
                f"WARNING: {peer_nick}'s public key has changed! "
                f"Old fingerprint: {_generate_fingerprint(self._sessions.get(peer_nick, E2EPMSession.create('', '')).my_public_key_bytes, old_key)}, "
                f"This could indicate a man-in-the-middle attack."
            )
        return None

    def clear_all(self) -> None:
        """Clear all sessions (e.g., on disconnect)."""
        self._sessions.clear()

    # ----- Key Rotation -----

    ROTATION_MESSAGE_THRESHOLD: int = 1000  # Rotate after N total messages
    ROTATION_TIME_THRESHOLD: float = 3600.0  # Rotate after N seconds (1 hour)

    def needs_rotation(self, peer_nick: str) -> bool:
        """Check if a session needs key rotation (message count or time threshold)."""
        session = self._sessions.get(peer_nick)
        if not session or not session.established:
            return False
        total_msgs = session.messages_sent + session.messages_recvd
        if total_msgs >= self.ROTATION_MESSAGE_THRESHOLD:
            return True
        baseline = session.last_rotation if session.last_rotation > 0 else session.created_at
        if baseline > 0 and (time.time() - baseline) >= self.ROTATION_TIME_THRESHOLD:
            return True
        return False

    def get_sessions_needing_rotation(self) -> list[str]:
        """Return list of peer nicks with sessions that need rotation."""
        result = []
        now = time.time()
        for nick, session in self._sessions.items():
            if not session.established:
                continue
            total_msgs = session.messages_sent + session.messages_recvd
            if total_msgs >= self.ROTATION_MESSAGE_THRESHOLD:
                result.append(nick)
                continue
            baseline = session.last_rotation if session.last_rotation > 0 else session.created_at
            if baseline > 0 and (now - baseline) >= self.ROTATION_TIME_THRESHOLD:
                result.append(nick)
        return result

    def rotation_stats(self, peer_nick: str) -> dict:
        """Get rotation statistics for a session.

        Returns dict with:
            messages_sent, messages_recvd, session_age, rotation_needed
        """
        session = self._sessions.get(peer_nick)
        if not session:
            return {"messages_sent": 0, "messages_recvd": 0, "session_age": 0.0, "rotation_needed": False}
        baseline = session.last_rotation if session.last_rotation > 0 else session.created_at
        age = (time.time() - baseline) if baseline > 0 else 0.0
        total_msgs = session.messages_sent + session.messages_recvd
        needed = session.established and (
            total_msgs >= self.ROTATION_MESSAGE_THRESHOLD
            or (baseline > 0 and age >= self.ROTATION_TIME_THRESHOLD)
        )
        return {
            "messages_sent": session.messages_sent,
            "messages_recvd": session.messages_recvd,
            "session_age": age,
            "rotation_needed": needed,
        }
