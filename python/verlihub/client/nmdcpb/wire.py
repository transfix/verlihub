"""
Wire protocol codec for NMDCpb.

Handles encoding/decoding of protobuf messages to/from NMDC wire format:
    $PB <base64url>|          — text mode (control messages)
    $PBB <length_hex>\\n<raw>|  — binary mode (bulk data)
    $PBR <relay_id> <len>\\n<data>|  — relay data
"""

from __future__ import annotations

import base64
import struct
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
    def encode_text(envelope: PbEnvelope) -> str:
        """Encode a PbEnvelope as a $PB text-mode NMDC command.

        Returns the full wire string including $PB prefix and | terminator.
        """
        raw = envelope.SerializeToString()
        b64 = _b64url_encode(raw)
        return f"{PREFIX_PB}{b64}{TERMINATOR}"

    @staticmethod
    def encode_binary(envelope: PbEnvelope) -> bytes:
        """Encode a PbEnvelope as a $PBB binary-mode NMDC command.

        Returns bytes: $PBB <length_hex>\\n<raw_protobuf>|
        """
        raw = envelope.SerializeToString()
        length_hex = format(len(raw), "x")
        header = f"{PREFIX_PBB}{length_hex}\n".encode("ascii")
        return header + raw + TERMINATOR.encode("ascii")

    @staticmethod
    def encode_relay(relay_id: int, encrypted_data: bytes) -> bytes:
        """Encode relay data as a $PBR command.

        Returns bytes: $PBR <relay_id_hex> <length_hex>\\n<data>|
        """
        relay_hex = format(relay_id, "x")
        length_hex = format(len(encrypted_data), "x")
        header = f"{PREFIX_PBR}{relay_hex} {length_hex}\n".encode("ascii")
        return header + encrypted_data + TERMINATOR.encode("ascii")

    # --- Decoding ---

    @staticmethod
    def decode(line: str | bytes) -> PbEnvelope | tuple[int, bytes] | None:
        """Decode an NMDC line into a PbEnvelope or relay data.

        Args:
            line: Raw NMDC line (with or without trailing |)

        Returns:
            PbEnvelope for $PB/$PBB messages,
            (relay_id, encrypted_data) tuple for $PBR messages,
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
            return WireCodec._decode_relay_str(line_str[len(PREFIX_PBR):])
        return None

    @staticmethod
    def decode_bytes(data: bytes) -> PbEnvelope | tuple[int, bytes] | None:
        """Decode raw bytes (needed for binary mode where payload may contain
        chars that aren't valid UTF-8).

        For $PBB and $PBR, this handles the binary payload correctly.
        """
        if data.startswith(b"$PBR "):
            return WireCodec._decode_relay_bytes(data[5:])
        elif data.startswith(b"$PBB "):
            return WireCodec._decode_binary_bytes(data[5:])
        elif data.startswith(b"$PB "):
            # Text mode — safe to decode as string
            line_str = data.decode("ascii", errors="replace")
            if line_str.endswith("|"):
                line_str = line_str[:-1]
            return WireCodec._decode_text(line_str[4:])
        return None

    @staticmethod
    def _decode_text(payload_b64: str) -> PbEnvelope:
        """Decode base64url payload into PbEnvelope."""
        raw = _b64url_decode(payload_b64)
        env = PbEnvelope()
        env.ParseFromString(raw)
        return env

    @staticmethod
    def _decode_binary_str(header_and_body: str) -> PbEnvelope:
        """Decode $PBB payload from string (works when body is valid text)."""
        newline_idx = header_and_body.index("\n")
        length = int(header_and_body[:newline_idx], 16)
        body = header_and_body[newline_idx + 1:]
        # For string representation, encode back to bytes for protobuf
        raw = body[:length].encode("latin-1")
        env = PbEnvelope()
        env.ParseFromString(raw)
        return env

    @staticmethod
    def _decode_binary_bytes(data: bytes) -> PbEnvelope:
        """Decode $PBB payload from raw bytes."""
        newline_idx = data.index(b"\n")
        length = int(data[:newline_idx].decode("ascii"), 16)
        raw = data[newline_idx + 1: newline_idx + 1 + length]
        env = PbEnvelope()
        env.ParseFromString(raw)
        return env

    @staticmethod
    def _decode_relay_str(data: str) -> tuple[int, bytes]:
        """Decode $PBR relay data from string."""
        space_idx = data.index(" ")
        relay_id = int(data[:space_idx], 16)
        rest = data[space_idx + 1:]
        newline_idx = rest.index("\n")
        length = int(rest[:newline_idx], 16)
        encrypted = rest[newline_idx + 1: newline_idx + 1 + length].encode("latin-1")
        return relay_id, encrypted

    @staticmethod
    def _decode_relay_bytes(data: bytes) -> tuple[int, bytes]:
        """Decode $PBR relay data from raw bytes."""
        space_idx = data.index(b" ")
        relay_id = int(data[:space_idx].decode("ascii"), 16)
        rest = data[space_idx + 1:]
        newline_idx = rest.index(b"\n")
        length = int(rest[:newline_idx].decode("ascii"), 16)
        encrypted = rest[newline_idx + 1: newline_idx + 1 + length]
        return relay_id, encrypted

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
