"""
Wire protocol codec for NMDCpb.

Handles encoding/decoding of protobuf messages to/from NMDC wire format:
    $PB <nick> <base64url>|            — text mode (control messages)
    $PBB <nick> <length_hex>\n<raw>|   — binary mode (bulk data)
    $PBR <to_nick> <from_nick> <base64url>|  — routed (direct) message
"""

from __future__ import annotations

import base64
import time
from typing import Optional

from verlihub.client.nmdcpb.nmdcpb_pb2 import PbEnvelope


# NMDC command prefixes
PREFIX_PB = "$PB "
PREFIX_PBB = "$PBB "
PREFIX_PBR = "$PBR "
TERMINATOR = "|"

# $Supports feature tokens
FEATURE_NMDCPB = "NMDCpb"
FEATURE_HUBRELAY = "HubRelay"


def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding (RFC 4648 §5)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Base64url decode with optional padding recovery."""
    padding = 4 - len(s) % 4
    if padding < 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


class WireCodec:
    """Encodes and decodes NMDCpb wire protocol messages."""

    # --- Encoding ---

    @staticmethod
    def encode_text(envelope: PbEnvelope, nick: str = "") -> str:
        """Encode a PbEnvelope as a $PB text-mode NMDC command.

        Wire format: ``$PB <nick> <base64url>|``

        Args:
            envelope: The protobuf envelope to encode.
            nick: Sender nick. If empty, uses ``envelope.from_nick``.

        Returns the full wire string including $PB prefix and | terminator.
        """
        sender = nick or envelope.from_nick
        raw = envelope.SerializeToString()
        b64 = _b64url_encode(raw)
        return f"{PREFIX_PB}{sender} {b64}{TERMINATOR}"

    @staticmethod
    def encode_binary(envelope: PbEnvelope, nick: str = "") -> bytes:
        """Encode a PbEnvelope as a $PBB binary-mode NMDC command.

        Wire format: ``$PBB <nick> <length_hex>\n<raw_protobuf>|``

        Args:
            envelope: The protobuf envelope to encode.
            nick: Sender nick. If empty, uses ``envelope.from_nick``.

        Returns bytes.
        """
        sender = nick or envelope.from_nick
        raw = envelope.SerializeToString()
        length_hex = format(len(raw), "x")
        header = f"{PREFIX_PBB}{sender} {length_hex}\n".encode("ascii")
        return header + raw + TERMINATOR.encode("ascii")

    @staticmethod
    def encode_routed(envelope: PbEnvelope, from_nick: str = "",
                      to_nick: str = "") -> str:
        """Encode a PbEnvelope as a $PBR routed NMDC command.

        Wire format: ``$PBR <to_nick> <from_nick> <base64url>|``

        Args:
            envelope: The protobuf envelope to encode.
            from_nick: Sender nick. If empty, uses ``envelope.from_nick``.
            to_nick: Recipient nick. If empty, uses ``envelope.to_nick``.

        Returns the full wire string.
        """
        sender = from_nick or envelope.from_nick
        target = to_nick or envelope.to_nick
        raw = envelope.SerializeToString()
        b64 = _b64url_encode(raw)
        return f"{PREFIX_PBR}{target} {sender} {b64}{TERMINATOR}"

    # Backward-compat alias
    encode_relay = encode_routed

    # --- Decoding ---

    @staticmethod
    def decode(line: str | bytes) -> PbEnvelope | None:
        """Decode an NMDC line into a PbEnvelope.

        Args:
            line: Raw NMDC line (with or without trailing |)

        Returns:
            PbEnvelope for $PB/$PBB/$PBR messages,
            None if the line is not an NMDCpb command.
        """
        if isinstance(line, bytes):
            line_str = line.decode("utf-8", errors="replace")
        else:
            line_str = line

        # Strip trailing terminator
        if line_str.endswith(TERMINATOR):
            line_str = line_str[:-1]

        if line_str.startswith(PREFIX_PB) and not line_str.startswith(PREFIX_PBB) and not line_str.startswith(PREFIX_PBR):
            return WireCodec._decode_text(line_str[len(PREFIX_PB):])
        elif line_str.startswith(PREFIX_PBB):
            return WireCodec._decode_binary_str(line_str[len(PREFIX_PBB):])
        elif line_str.startswith(PREFIX_PBR):
            return WireCodec._decode_routed_str(line_str[len(PREFIX_PBR):])
        return None

    @staticmethod
    def decode_bytes(data: bytes) -> PbEnvelope | None:
        """Decode raw bytes (needed for binary mode where payload may contain
        chars that aren't valid UTF-8).

        For $PBB and $PBR, this handles the binary payload correctly.
        """
        # Strip trailing terminator
        if data.endswith(b"|"):
            data = data[:-1]

        if data.startswith(b"$PBR "):
            return WireCodec._decode_routed_bytes(data[5:])
        elif data.startswith(b"$PBB "):
            return WireCodec._decode_binary_bytes(data[5:])
        elif data.startswith(b"$PB ") and not data.startswith(b"$PBB ") and not data.startswith(b"$PBR "):
            # Text mode — safe to decode as string
            return WireCodec._decode_text(data[4:].decode("ascii", errors="replace"))
        return None

    # --- Internal decoders ---

    @staticmethod
    def _decode_text(nick_and_b64: str) -> PbEnvelope:
        """Decode ``<nick> <base64url>`` payload into PbEnvelope.

        The hub sends ``$PB <nick> <b64>|``, so after stripping the
        ``$PB `` prefix we receive ``<nick> <b64>``.
        """
        parts = nick_and_b64.split(" ", 1)
        if len(parts) == 2:
            wire_nick, b64 = parts
        else:
            # Fallback: entire string is b64 (e.g. legacy format)
            wire_nick, b64 = "", parts[0]

        raw = _b64url_decode(b64)
        env = PbEnvelope()
        env.ParseFromString(raw)

        # Wire nick is authoritative (hub-validated)
        if wire_nick and not env.from_nick:
            env.from_nick = wire_nick
        return env

    @staticmethod
    def _decode_binary_str(nick_header_body: str) -> PbEnvelope:
        """Decode ``<nick> <length_hex>\\n<raw>`` from $PBB string."""
        space_idx = nick_header_body.index(" ")
        wire_nick = nick_header_body[:space_idx]
        rest = nick_header_body[space_idx + 1:]
        newline_idx = rest.index("\n")
        length = int(rest[:newline_idx], 16)
        body = rest[newline_idx + 1:]
        raw = body[:length].encode("latin-1")
        env = PbEnvelope()
        env.ParseFromString(raw)
        if wire_nick and not env.from_nick:
            env.from_nick = wire_nick
        return env

    @staticmethod
    def _decode_binary_bytes(data: bytes) -> PbEnvelope:
        """Decode $PBB payload from raw bytes: ``<nick> <len_hex>\\n<raw>``."""
        space_idx = data.index(b" ")
        wire_nick = data[:space_idx].decode("ascii", errors="replace")
        rest = data[space_idx + 1:]
        newline_idx = rest.index(b"\n")
        length = int(rest[:newline_idx].decode("ascii"), 16)
        raw = rest[newline_idx + 1: newline_idx + 1 + length]
        env = PbEnvelope()
        env.ParseFromString(raw)
        if wire_nick and not env.from_nick:
            env.from_nick = wire_nick
        return env

    @staticmethod
    def _decode_routed_str(data: str) -> PbEnvelope:
        """Decode ``<to_nick> <from_nick> <base64url>`` from $PBR string."""
        parts = data.split(" ", 2)
        if len(parts) < 3:
            raise ValueError(f"Malformed $PBR payload: {data!r}")
        to_nick, from_nick, b64 = parts
        raw = _b64url_decode(b64)
        env = PbEnvelope()
        env.ParseFromString(raw)
        # Wire nicks are authoritative
        if to_nick and not env.to_nick:
            env.to_nick = to_nick
        if from_nick and not env.from_nick:
            env.from_nick = from_nick
        return env

    @staticmethod
    def _decode_routed_bytes(data: bytes) -> PbEnvelope:
        """Decode $PBR from raw bytes."""
        return WireCodec._decode_routed_str(
            data.decode("ascii", errors="replace")
        )

    # --- Helpers ---

    @staticmethod
    def is_nmdcpb_command(line: str | bytes) -> bool:
        """Check if an NMDC line is a NMDCpb command ($PB, $PBB, or $PBR)."""
        if isinstance(line, bytes):
            return (line.startswith(b"$PB ") or
                    line.startswith(b"$PBB ") or
                    line.startswith(b"$PBR "))
        return (line.startswith("$PB ") or
                line.startswith("$PBB ") or
                line.startswith("$PBR "))

    @staticmethod
    def make_envelope(
        route: PbEnvelope.RouteType = PbEnvelope.BROADCAST,
        from_nick: str = "",
        to_nick: str = "",
        features: str = "",
    ) -> PbEnvelope:
        """Create a PbEnvelope with common fields populated."""
        env = PbEnvelope()
        env.route = route
        if from_nick:
            env.from_nick = from_nick
        if to_nick:
            env.to_nick = to_nick
        if features:
            env.features = features
        env.timestamp = int(time.time() * 1000)
        return env

    @staticmethod
    def check_supports(supports_line: str) -> tuple[bool, bool]:
        """Parse a $Supports line and check for NMDCpb and HubRelay.

        Args:
            supports_line: Raw $Supports line from hub/client

        Returns:
            (has_nmdcpb, has_hubrelay) tuple
        """
        tokens = supports_line.strip().split()
        has_nmdcpb = FEATURE_NMDCPB in tokens
        has_hubrelay = FEATURE_HUBRELAY in tokens
        return has_nmdcpb, has_hubrelay

    @staticmethod
    def inject_supports(supports_line: str, nmdcpb: bool = True,
                        hubrelay: bool = False) -> str:
        """Add NMDCpb/HubRelay tokens to a $Supports line.

        Args:
            supports_line: Original $Supports line
            nmdcpb: Add NMDCpb token
            hubrelay: Add HubRelay token

        Returns:
            Modified $Supports line with added tokens
        """
        # Strip trailing | if present
        line = supports_line.rstrip("|").rstrip()
        if nmdcpb and FEATURE_NMDCPB not in line:
            line += f" {FEATURE_NMDCPB}"
        if hubrelay and FEATURE_HUBRELAY not in line:
            line += f" {FEATURE_HUBRELAY}"
        return line + "|"
