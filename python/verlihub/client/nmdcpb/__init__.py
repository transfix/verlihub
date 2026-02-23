"""
NMDCpb — NMDC Protocol Buffer Extension Client

A structured messaging layer for the NMDC Direct Connect protocol.
Provides binary-efficient, strongly-typed, versioned protocol messages
as an overlay on standard NMDC text commands.

This is an extended client that serves as a reference implementation for
and validates the hub-side implementation of the NMDCpb protobuf extension,
including end-to-end encrypted private messages and file transfers.

Wire format:
    $PB <base64url_encoded_PbEnvelope>|     (text mode)
    $PBB <length_hex>\\n<raw_bytes>|         (binary mode)
    $PBR <relay_id_hex> <length_hex>\\n<encrypted_bytes>|  (relay data)

Example:
    from verlihub.client.nmdcpb import WireCodec, NMDCpbClient, E2EPMManager

    # Async client with protobuf support
    client = NMDCpbClient("nick", "password")
    await client.connect("nmdc://hub.example.com:411")
    await client.send_pb_chat("Hello from protobuf!")
    await client.send_encrypted_pm("other_nick", "Secret message")
"""

__version__ = "0.1.0"

from verlihub.client.nmdcpb.wire import WireCodec
from verlihub.client.nmdcpb.client import NMDCpbClient
from verlihub.client.nmdcpb.e2epm import E2EPMSession, E2EPMManager

__all__ = [
    "WireCodec",
    "NMDCpbClient",
    "E2EPMSession",
    "E2EPMManager",
]
