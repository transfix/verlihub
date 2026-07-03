"""
Redirect service — redirect rules for user connections.

Redirect rules route users to alternative hub addresses based on
configurable flags (hub full, share too low, kicked, etc.).
Each rule maps a flag bitmask to a destination address.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.models import Redirect

_log = logging.getLogger(__name__)


class RedirectCache:
    """In-memory cache of redirect rules for fast flag-based lookup."""

    def __init__(self) -> None:
        self._rules: list[dict] = []

    def match(self, flag: int) -> Optional[str]:
        """Find first enabled redirect whose flag matches.

        Args:
            flag: Redirect reason bitmask.

        Returns:
            Destination address string, or None.
        """
        for rule in self._rules:
            if not rule["enable"]:
                continue
            if rule["flag"] & flag:
                return rule["address"]
        return None

    def load(self, rules: list[dict]) -> None:
        self._rules = list(rules)

    def clear(self) -> None:
        self._rules.clear()

    @property
    def count(self) -> int:
        return len(self._rules)


_redirect_cache = RedirectCache()


def get_redirect_cache() -> RedirectCache:
    return _redirect_cache


def _redirect_to_dict(r: Redirect) -> dict:
    return {
        "id": r.id,
        "address": r.address,
        "flag": r.flag,
        "enable": r.enable,
    }


async def sync_redirect_cache(session: AsyncSession) -> int:
    """Load all redirects from DB into cache. Returns count."""
    result = await session.execute(select(Redirect))
    redirects = result.scalars().all()
    entries = [_redirect_to_dict(r) for r in redirects]
    _redirect_cache.load(entries)
    _log.info("Redirect cache loaded: %d entries", len(entries))
    return len(entries)


async def get_all_redirects(session: AsyncSession) -> list[Redirect]:
    result = await session.execute(select(Redirect))
    return list(result.scalars().all())


async def get_redirect_by_id(session: AsyncSession, redirect_id: int) -> Optional[Redirect]:
    result = await session.execute(select(Redirect).where(Redirect.id == redirect_id))
    return result.scalar_one_or_none()


async def create_redirect(
    session: AsyncSession,
    *,
    address: str,
    flag: int = 0,
    enable: bool = True,
) -> Redirect:
    redirect = Redirect(address=address, flag=flag, enable=enable)
    session.add(redirect)
    await session.commit()
    await session.refresh(redirect)
    _redirect_cache.load(
        [_redirect_to_dict(r) for r in await get_all_redirects(session)]
    )
    _log.info("Created redirect: flag=%d -> %s", flag, address)
    return redirect


async def remove_redirect(session: AsyncSession, redirect: Redirect) -> None:
    await session.delete(redirect)
    await session.commit()
    _redirect_cache.load(
        [_redirect_to_dict(r) for r in await get_all_redirects(session)]
    )
    _log.info("Removed redirect: %s", redirect.address)


async def update_redirect(
    session: AsyncSession,
    redirect: Redirect,
    **kwargs,
) -> Redirect:
    for key, value in kwargs.items():
        if hasattr(redirect, key):
            setattr(redirect, key, value)
    session.add(redirect)
    await session.commit()
    await session.refresh(redirect)
    _redirect_cache.load(
        [_redirect_to_dict(r) for r in await get_all_redirects(session)]
    )
    return redirect
