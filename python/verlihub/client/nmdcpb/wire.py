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
FEATURE_RELAYONLY = "RelayOnly"


def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding (RFC 4648 §5)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Base64url decode with optional padding recovery."""
    padding = 4 - len(s) % 4
    if padding < 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


# ---------------------------------------------------------------------------
# Protobuf wire format utilities (for opaque relay pass-through)
# ---------------------------------------------------------------------------

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf varint starting at *pos*.

    Returns (value, new_pos).  Raises ValueError on truncated input.
    """
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        shift += 7
        pos += 1
        if not (b & 0x80):
            return result, pos
    raise ValueError("Truncated varint")


def _encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts)


def _skip_field(data: bytes, pos: int, wire_type: int) -> int:
    """Skip over a protobuf field value.  Returns new position."""
    if wire_type == 0:  # VARINT
        while pos < len(data) and data[pos] & 0x80:
            pos += 1
        return pos + 1
    elif wire_type == 2:  # LEN (string, bytes, sub-message)
        length, pos = _read_varint(data, pos)
        return pos + length
    elif wire_type == 5:  # I32
        return pos + 4
    elif wire_type == 1:  # I64
        return pos + 8
    else:
        raise ValueError(f"Unknown wire type {wire_type}")


def _extract_field_raw(data: bytes, target_field: int) -> tuple[int, int] | None:
    """Find a protobuf field in serialized data.

    Returns ``(field_start, field_end)`` byte offsets for the complete
    field entry (tag + length-prefix + value) of *target_field*, or
    ``None`` if the field is not present.
    """
    pos = 0
    while pos < len(data):
        field_start = pos
        tag, pos = _read_varint(data, pos)
        field_number = tag >> 3
        wire_type = tag & 0x07
        try:
            field_end = _skip_field(data, pos, wire_type)
        except (ValueError, IndexError):
            return None
        if field_number == target_field:
            return (field_start, field_end)
        pos = field_end
    return None


def _read_submsg_varint(data: bytes, outer_field: int,
                        inner_field: int) -> int | None:
    """Read a varint from inside a sub-message field without full parse.

    Scans *data* for *outer_field* (expected wire-type LEN / sub-message),
    then scans inside it for *inner_field* (expected varint).
    Returns the varint value, or ``None`` if not found.
    """
    pos = 0
    while pos < len(data):
        tag, new_pos = _read_varint(data, pos)
        fn = tag >> 3
        wt = tag & 0x07
        if fn == outer_field and wt == 2:
            # Found the sub-message — read its length
            sub_len, sub_start = _read_varint(data, new_pos)
            sub_end = sub_start + sub_len
            # Scan inside the sub-message for inner_field
            sp = sub_start
            while sp < sub_end:
                itag, sp2 = _read_varint(data, sp)
                ifn = itag >> 3
                iwt = itag & 0x07
                if ifn == inner_field and iwt == 0:
                    val, _ = _read_varint(data, sp2)
                    return val
                try:
                    sp = _skip_field(data, sp2, iwt)
                except (ValueError, IndexError):
                    return None
            return None  # inner field not found
        try:
            pos = _skip_field(data, new_pos, wt)
        except (ValueError, IndexError):
            return None
    return None


def _submsg_data_length(data: bytes, outer_field: int,
                        data_field: int) -> int | None:
    """Read the *length* of a ``bytes`` field inside a sub-message.

    Scans for *outer_field* (sub-message), then inside it for
    *data_field* (wire-type LEN).  Returns the byte-length of the
    LEN-delimited value **without copying it**.
    """
    pos = 0
    while pos < len(data):
        tag, new_pos = _read_varint(data, pos)
        fn = tag >> 3
        wt = tag & 0x07
        if fn == outer_field and wt == 2:
            sub_len, sub_start = _read_varint(data, new_pos)
            sub_end = sub_start + sub_len
            sp = sub_start
            while sp < sub_end:
                itag, sp2 = _read_varint(data, sp)
                ifn = itag >> 3
                iwt = itag & 0x07
                if ifn == data_field and iwt == 2:
                    dlen, _ = _read_varint(data, sp2)
                    return dlen
                try:
                    sp = _skip_field(data, sp2, iwt)
                except (ValueError, IndexError):
                    return None
            return 0  # data field absent → zero length
        try:
            pos = _skip_field(data, new_pos, wt)
        except (ValueError, IndexError):
            return None
    return None


# PbEnvelope field numbers (from nmdcpb.proto)
_FIELD_ROUTE = 1          # varint
_FIELD_FROM_NICK = 2      # string (LEN)
_FIELD_TO_NICK = 3        # string (LEN)
_FIELD_TIMESTAMP = 5      # uint64 (varint)
_FIELD_RELAY_DATA = 22    # PbRelayData sub-message (LEN)
# PbRelayData inner field numbers
_FIELD_RD_RELAY_ID = 1    # uint32 (varint)
_FIELD_RD_DATA = 2        # bytes (LEN)


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

    # --- Fast-path encoding (for opaque relay forwarding) ---

    @staticmethod
    def encode_text_raw(raw_pb: bytes, nick: str) -> str:
        """Encode pre-serialized protobuf bytes as a $PB text-mode command.

        Skips ``SerializeToString()`` — use when you already have the raw
        protobuf bytes (e.g. from opaque relay forwarding).

        Args:
            raw_pb: Pre-serialized protobuf bytes.
            nick: Sender nick for the wire header.
        """
        b64 = _b64url_encode(raw_pb)
        return f"{PREFIX_PB}{nick} {b64}{TERMINATOR}"

    @staticmethod
    def build_relay_forward(raw_pb: bytes, from_nick: str,
                            to_nick: str, timestamp: int) -> str:
        """Build a forwarded relay-data wire frame using raw byte surgery.

        Takes the **original** serialized protobuf bytes of the incoming
        ``PbEnvelope`` (containing ``relay_data``), strips the old
        routing fields, and concatenates a fresh header (with new nicks)
        and the *unchanged* ``relay_data`` raw bytes.

        This avoids full ``ParseFromString`` → modify → ``SerializeToString``
        round-trip for the (potentially 64 KB) relay data payload.

        Args:
            raw_pb: Original serialized PbEnvelope bytes.
            from_nick: Hub-authoritative sender nick.
            to_nick: Peer nick (forwarding target).
            timestamp: Envelope timestamp (uint64 millis).

        Returns:
            ``$PB <to_nick> <base64url>|`` wire string, or empty string
            if the relay_data field couldn't be found.
        """
        # 1. Find the relay_data field in the original bytes
        rd_span = _extract_field_raw(raw_pb, _FIELD_RELAY_DATA)
        if rd_span is None:
            return ""
        rd_start, rd_end = rd_span
        relay_data_raw = raw_pb[rd_start:rd_end]

        # 2. Build a minimal header envelope (route + nicks + timestamp)
        header = PbEnvelope()
        header.route = PbEnvelope.DIRECT
        header.from_nick = from_nick
        header.to_nick = to_nick
        header.timestamp = timestamp
        header_bytes = header.SerializeToString()

        # 3. Concatenate — valid protobuf (last-writer-wins for scalars,
        #    sub-messages merge).  The header has no relay_data, so the
        #    relay_data_raw field is simply appended.
        full_bytes = header_bytes + relay_data_raw

        # 4. Encode as $PB text frame
        b64 = _b64url_encode(full_bytes)
        return f"{PREFIX_PB}{to_nick} {b64}{TERMINATOR}"

    @staticmethod
    def decode_relay_opaque(line: str) -> tuple[int, int, int, bytes] | None:
        """Fast-path decode for relay data forwarding.

        Performs minimal protobuf wire scanning (no ``ParseFromString``)
        to extract just the fields needed for relay validation:

        - ``route``  (PbEnvelope field 1)
        - ``relay_id``  (PbRelayData.relay_id, sub-field 1 of field 22)
        - ``data_length``  (byte-length of PbRelayData.data, sub-field 2)

        Also returns the raw protobuf bytes for use with
        :meth:`build_relay_forward`.

        Args:
            line: Raw ``$PB <nick> <base64url>|`` wire string.

        Returns:
            ``(route, relay_id, data_length, raw_pb)`` tuple, or ``None``
            if the message doesn't look like a ``$PB`` relay_data frame
            or the wire-format scan fails.
        """
        if not isinstance(line, str):
            return None
        s = line
        if s.endswith(TERMINATOR):
            s = s[:-1]

        # Only handle $PB text mode
        if not s.startswith(PREFIX_PB):
            return None
        if s.startswith(PREFIX_PBB) or s.startswith(PREFIX_PBR):
            return None

        rest = s[len(PREFIX_PB):]
        parts = rest.split(" ", 1)
        if len(parts) != 2:
            return None
        _, b64 = parts

        try:
            raw_pb = _b64url_decode(b64)
        except Exception:
            return None

        # Quick-scan: route (field 1, varint)
        route = _read_submsg_varint.__wrapped__(raw_pb) if False else None  # noqa – placeholder
        # Actually scan for route directly
        route_val = None
        pos = 0
        try:
            while pos < len(raw_pb):
                tag, new_pos = _read_varint(raw_pb, pos)
                fn = tag >> 3
                wt = tag & 0x07
                if fn == _FIELD_ROUTE and wt == 0:
                    route_val, _ = _read_varint(raw_pb, new_pos)
                pos = _skip_field(raw_pb, new_pos, wt)
        except (ValueError, IndexError):
            pass

        if route_val is None:
            # proto3 default for route is 0 (BROADCAST) — relay_data
            # should be DIRECT (1), so if route is absent it's not relay.
            return None
        if route_val != 1:  # PbEnvelope.DIRECT == 1
            return None

        # Check relay_data is present and extract relay_id / data_length
        relay_id = _read_submsg_varint(raw_pb, _FIELD_RELAY_DATA,
                                       _FIELD_RD_RELAY_ID)
        if relay_id is None:
            return None  # Not a relay_data message

        data_length = _submsg_data_length(raw_pb, _FIELD_RELAY_DATA,
                                          _FIELD_RD_DATA)
        if data_length is None:
            data_length = 0

        return (route_val, relay_id, data_length, raw_pb)

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
    def check_supports(supports_line: str) -> tuple[bool, bool, bool]:
        """Parse a $Supports line and check for NMDCpb, HubRelay, RelayOnly.

        Args:
            supports_line: Raw $Supports line from hub/client

        Returns:
            (has_nmdcpb, has_hubrelay, has_relayonly) tuple
        """
        tokens = supports_line.strip().split()
        has_nmdcpb = FEATURE_NMDCPB in tokens
        has_hubrelay = FEATURE_HUBRELAY in tokens
        has_relayonly = FEATURE_RELAYONLY in tokens
        return has_nmdcpb, has_hubrelay, has_relayonly

    @staticmethod
    def inject_supports(supports_line: str, nmdcpb: bool = True,
                        hubrelay: bool = False,
                        relayonly: bool = False) -> str:
        """Add NMDCpb/HubRelay/RelayOnly tokens to a $Supports line.

        Args:
            supports_line: Original $Supports line
            nmdcpb: Add NMDCpb token
            hubrelay: Add HubRelay token
            relayonly: Add RelayOnly token

        Returns:
            Modified $Supports line with added tokens
        """
        # Strip trailing | if present
        line = supports_line.rstrip("|").rstrip()
        if nmdcpb and FEATURE_NMDCPB not in line:
            line += f" {FEATURE_NMDCPB}"
        if hubrelay and FEATURE_HUBRELAY not in line:
            line += f" {FEATURE_HUBRELAY}"
        if relayonly and FEATURE_RELAYONLY not in line:
            line += f" {FEATURE_RELAYONLY}"
        return line + "|"
