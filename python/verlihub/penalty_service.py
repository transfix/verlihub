"""
Penalty service — temporary per-user restrictions.

Provides enforcement of chat gag, PM ban, search ban, CTM ban, etc.
Penalties have expiry times and are checked from the Python event
callback layer (OnChatMessage, OnSearch, OnPrivateMessage).

The service maintains an in-memory cache of active penalties keyed
by nick for fast lookup, synced from the database on startup and
updated as penalties are created/removed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.models import Penalty, PenaltyType

_log = logging.getLogger(__name__)


class PenaltyCache:
    """
    In-memory cache of active penalties for fast enforcement.

    Maps nick -> set of active PenaltyType flags.
    Must be refreshed periodically to expire stale entries.
    """

    def __init__(self) -> None:
        # nick -> bitmask of active penalty types
        self._cache: dict[str, int] = {}

    def is_gagged(self, nick: str) -> bool:
        """Check if user is gagged (cannot chat)."""
        return bool(self._cache.get(nick, 0) & PenaltyType.GAG)

    def is_pm_banned(self, nick: str) -> bool:
        """Check if user is PM-banned."""
        return bool(self._cache.get(nick, 0) & PenaltyType.NO_PM)

    def is_search_banned(self, nick: str) -> bool:
        """Check if user is search-banned."""
        return bool(self._cache.get(nick, 0) & PenaltyType.NO_SEARCH)

    def is_ctm_banned(self, nick: str) -> bool:
        """Check if user cannot do file transfers."""
        return bool(self._cache.get(nick, 0) & PenaltyType.NO_CTM)

    def has_penalty(self, nick: str, penalty_type: int) -> bool:
        """Check if user has a specific penalty flag."""
        return bool(self._cache.get(nick, 0) & penalty_type)

    def add(self, nick: str, penalty_type: int) -> None:
        """Add penalty flags for a user."""
        self._cache[nick] = self._cache.get(nick, 0) | penalty_type

    def remove(self, nick: str, penalty_type: int) -> None:
        """Remove penalty flags for a user."""
        if nick in self._cache:
            self._cache[nick] &= ~penalty_type
            if self._cache[nick] == 0:
                del self._cache[nick]

    def remove_all(self, nick: str) -> None:
        """Remove all penalties for a user."""
        self._cache.pop(nick, None)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()

    def load(self, penalties: list[tuple[str, int]]) -> None:
        """
        Load penalties from a list of (nick, penalty_type) tuples.
        Replaces the current cache.
        """
        self._cache.clear()
        for nick, ptype in penalties:
            self._cache[nick] = self._cache.get(nick, 0) | ptype


# Module-level singleton
_penalty_cache = PenaltyCache()


def get_penalty_cache() -> PenaltyCache:
    """Get the module-level penalty cache singleton."""
    return _penalty_cache


async def sync_penalty_cache(session: AsyncSession) -> int:
    """
    Load all active penalties from the database into the cache.

    Should be called at hub startup.

    Returns:
        Number of active penalties loaded
    """
    now = datetime.now(timezone.utc)
    query = select(Penalty).where(
        (Penalty.date_end.is_(None)) | (Penalty.date_end > now),
    )
    result = await session.execute(query)
    penalties = result.scalars().all()

    entries = [(p.nick, p.penalty_type) for p in penalties]
    _penalty_cache.load(entries)

    _log.info("Synced %d active penalties to cache", len(entries))
    return len(entries)


async def add_penalty(
    session: AsyncSession,
    *,
    nick: str,
    penalty_type: int = PenaltyType.GAG,
    reason: str = "",
    op_nick: str = "",
    duration_minutes: Optional[int] = None,
    ip: str = "",
) -> Penalty:
    """
    Add a penalty for a user.

    Args:
        session: Database session
        nick: User's nickname
        penalty_type: PenaltyType flags (can be combined with |)
        reason: Reason for the penalty
        op_nick: Operator who applied it
        duration_minutes: Duration in minutes (None = permanent)
        ip: User's IP (for logging)

    Returns:
        The created Penalty object
    """
    now = datetime.now(timezone.utc)
    date_end = None
    if duration_minutes is not None:
        date_end = now + timedelta(minutes=duration_minutes)

    penalty = Penalty(
        nick=nick,
        ip=ip,
        penalty_type=penalty_type,
        reason=reason,
        op_nick=op_nick,
        date_start=now,
        date_end=date_end,
    )

    session.add(penalty)
    await session.commit()
    await session.refresh(penalty)

    # Update in-memory cache
    _penalty_cache.add(nick, penalty_type)

    _log.info("Added penalty type=%d for %s by %s: %s", penalty_type, nick, op_nick, reason)
    return penalty


async def remove_penalty(
    session: AsyncSession,
    penalty: Penalty,
) -> None:
    """Remove a specific penalty."""
    nick = penalty.nick
    ptype = penalty.penalty_type

    await session.delete(penalty)
    await session.commit()

    # Recompute cache for this nick (may have other active penalties)
    now = datetime.now(timezone.utc)
    query = select(Penalty).where(
        Penalty.nick == nick,
        (Penalty.date_end.is_(None)) | (Penalty.date_end > now),
    )
    result = await session.execute(query)
    remaining = result.scalars().all()

    _penalty_cache.remove_all(nick)
    for p in remaining:
        _penalty_cache.add(nick, p.penalty_type)


async def remove_penalties_for_nick(
    session: AsyncSession,
    nick: str,
) -> int:
    """
    Remove all penalties for a user.

    Returns:
        Number of penalties removed
    """
    query = select(Penalty).where(Penalty.nick == nick)
    result = await session.execute(query)
    penalties = result.scalars().all()

    for p in penalties:
        await session.delete(p)

    if penalties:
        await session.commit()

    _penalty_cache.remove_all(nick)
    return len(penalties)


async def get_active_penalties(
    session: AsyncSession,
    nick: Optional[str] = None,
) -> list[Penalty]:
    """
    Get active penalties, optionally filtered by nick.

    Args:
        session: Database session
        nick: Filter by nick (None = all)

    Returns:
        List of active Penalty objects
    """
    now = datetime.now(timezone.utc)
    query = select(Penalty).where(
        (Penalty.date_end.is_(None)) | (Penalty.date_end > now),
    )
    if nick:
        query = query.where(Penalty.nick == nick)

    result = await session.execute(query)
    return list(result.scalars().all())


async def cleanup_expired(session: AsyncSession) -> int:
    """
    Remove expired penalties from the database and refresh cache.

    Should be called periodically (e.g. from a timer).

    Returns:
        Number of expired penalties cleaned up
    """
    now = datetime.now(timezone.utc)
    query = select(Penalty).where(
        Penalty.date_end.isnot(None),
        Penalty.date_end <= now,
    )
    result = await session.execute(query)
    expired = result.scalars().all()

    nicks_affected = set()
    for p in expired:
        nicks_affected.add(p.nick)
        await session.delete(p)

    if expired:
        await session.commit()

    # Refresh cache for affected nicks
    for nick in nicks_affected:
        _penalty_cache.remove_all(nick)
        remaining_q = select(Penalty).where(
            Penalty.nick == nick,
            (Penalty.date_end.is_(None)) | (Penalty.date_end > now),
        )
        r = await session.execute(remaining_q)
        for p in r.scalars().all():
            _penalty_cache.add(nick, p.penalty_type)

    if expired:
        _log.info("Cleaned up %d expired penalties", len(expired))
    return len(expired)
