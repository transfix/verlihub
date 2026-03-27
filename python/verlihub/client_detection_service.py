"""
Client detection service — enforce DC client version rules.

Client rules define allowed/disallowed DC client software and version
ranges. When a user connects, their $MyINFO tag is parsed via
NMDCProtocol::ParseTag and checked against the rule set.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.models import DCClient

_log = logging.getLogger(__name__)


class ClientDetectionCache:
    """In-memory cache of client rules for tag-based matching."""

    def __init__(self) -> None:
        self._rules: list[dict] = []

    def check(self, client_name: str, version: float) -> Optional[dict]:
        """Check if a client is banned by any enabled rule.

        Args:
            client_name: Client name from ParseTag (e.g. '++', 'StrgDC++').
            version: Client version number from ParseTag.

        Returns:
            The matching rule dict if the client should be banned, else None.
        """
        for rule in self._rules:
            if not rule["enable"]:
                continue
            # Match by tag_id (partial match) OR exact name
            tag_match = rule["tag_id"] and rule["tag_id"] in client_name
            name_match = rule["name"] and rule["name"].lower() == client_name.lower()
            if not tag_match and not name_match:
                continue
            # Version range check
            if rule["min_version"] > 0 and version < rule["min_version"]:
                continue
            if rule["max_version"] > 0 and version > rule["max_version"]:
                continue
            if rule["ban"]:
                return rule
        return None

    def load(self, rules: list[dict]) -> None:
        self._rules = list(rules)

    def clear(self) -> None:
        self._rules.clear()

    @property
    def count(self) -> int:
        return len(self._rules)


_client_cache = ClientDetectionCache()


def get_client_cache() -> ClientDetectionCache:
    return _client_cache


def _client_to_dict(c: DCClient) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "tag_id": c.tag_id,
        "min_version": c.min_version,
        "max_version": c.max_version,
        "ban": c.ban,
        "enable": c.enable,
    }


async def sync_client_cache(session: AsyncSession) -> int:
    """Load all client rules from DB into cache. Returns count."""
    result = await session.execute(select(DCClient))
    clients = result.scalars().all()
    entries = [_client_to_dict(c) for c in clients]
    _client_cache.load(entries)
    _log.info("Client detection cache loaded: %d entries", len(entries))
    return len(entries)


async def get_all_clients(session: AsyncSession) -> list[DCClient]:
    result = await session.execute(select(DCClient))
    return list(result.scalars().all())


async def get_client_by_id(session: AsyncSession, client_id: int) -> Optional[DCClient]:
    result = await session.execute(select(DCClient).where(DCClient.id == client_id))
    return result.scalar_one_or_none()


async def create_client_rule(
    session: AsyncSession,
    *,
    name: str,
    tag_id: str = "",
    min_version: float = 0.0,
    max_version: float = 0.0,
    ban: bool = False,
    enable: bool = True,
) -> DCClient:
    client = DCClient(
        name=name,
        tag_id=tag_id,
        min_version=min_version,
        max_version=max_version,
        ban=ban,
        enable=enable,
    )
    session.add(client)
    await session.commit()
    await session.refresh(client)
    _client_cache.load(
        [_client_to_dict(c) for c in await get_all_clients(session)]
    )
    _log.info("Created client rule: %s (ban=%s)", name, ban)
    return client


async def remove_client_rule(session: AsyncSession, client: DCClient) -> None:
    await session.delete(client)
    await session.commit()
    _client_cache.load(
        [_client_to_dict(c) for c in await get_all_clients(session)]
    )
    _log.info("Removed client rule: %s", client.name)


async def update_client_rule(
    session: AsyncSession,
    client: DCClient,
    **kwargs,
) -> DCClient:
    for key, value in kwargs.items():
        if hasattr(client, key):
            setattr(client, key, value)
    session.add(client)
    await session.commit()
    await session.refresh(client)
    _client_cache.load(
        [_client_to_dict(c) for c in await get_all_clients(session)]
    )
    return client
