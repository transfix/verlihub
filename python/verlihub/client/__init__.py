"""
Verlihub Client Library

This module provides client classes for interacting with Verlihub hubs:

- NMDCClient: Direct NMDC protocol connection to hub
- HubClient: REST API client for remote hub management
- AsyncHubClient: Async version of REST API client

Example - NMDC Protocol (Direct Connection):
    from verlihub.client import NMDCClient
    
    # Connect directly via NMDC protocol
    with NMDCClient("localhost", 4111, "admin", "password") as client:
        client.send_chat("Hello from Python!")
        client.execute_command("!help")

Example - REST API (Remote Management):
    from verlihub.client import HubClient, AsyncHubClient
    
    # Sync client
    with HubClient("https://myhub.example.com/api/v1") as client:
        client.login("admin", "password")
        print(f"Users online: {client.get_user_count()}")
        client.send_to_all("Server announcement")
    
    # Async client
    async with AsyncHubClient("https://myhub.example.com/api/v1") as client:
        await client.login("admin", "password")
        users = await client.get_user_list()
"""
from __future__ import annotations

from verlihub.client.nmdc import NMDCClient, NMDCError, NMDCConnectionError
from verlihub.client.api import HubClient, AsyncHubClient, HubClientError

# NMDCpb extension — optional, requires protobuf + cryptography
try:
    from verlihub.client.nmdcpb import (
        WireCodec,
        NMDCpbClient,
        E2EPMSession,
        E2EPMManager,
    )

    _NMDCPB_AVAILABLE = True
except ImportError:
    _NMDCPB_AVAILABLE = False

__all__ = [
    "NMDCClient",
    "NMDCError",
    "NMDCConnectionError",
    "HubClient",
    "AsyncHubClient",
    "HubClientError",
    "build_mcp_server",
]

if _NMDCPB_AVAILABLE:
    __all__ += [
        "WireCodec",
        "NMDCpbClient",
        "E2EPMSession",
        "E2EPMManager",
    ]


def build_mcp_server(*args, **kwargs):
    """Lazy import wrapper for the MCP server builder."""
    from verlihub.client.mcp import build_mcp_server as _build
    return _build(*args, **kwargs)
