"""
Trigger service — custom auto-response commands.

Triggers are DB-stored command→response pairs that fire when a user
types the matching command in main chat. Supports class restrictions
and configurable delivery mode (main chat, PM, or execute-as-command).

The service maintains an in-memory cache of active triggers for fast
matching, synced from the database on startup.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.models import Trigger, TriggerFlags

_log = logging.getLogger(__name__)


class TriggerCache:
    """
    In-memory cache of triggers keyed by command string for O(1) lookup.
    """

    def __init__(self) -> None:
        # command (lower) -> Trigger-like dict
        self._cache: dict[str, dict] = {}

    def match(self, text: str, user_class: int) -> Optional[dict]:
        """Match text against triggers, respecting class restrictions.

        Args:
            text: Chat message text (the leading command word is matched).
            user_class: Caller's class level.

        Returns:
            Trigger dict if matched, else None.
        """
        # Extract the first word as the command token
        cmd = text.strip().split(None, 1)[0].lower() if text.strip() else ""
        entry = self._cache.get(cmd)
        if entry is None:
            return None
        if user_class < entry["min_class"] or user_class > entry["max_class"]:
            return None
        return entry

    def load(self, triggers: list[dict]) -> None:
        """Replace cache with a list of trigger dicts."""
        self._cache.clear()
        for t in triggers:
            key = t["command"].strip().lower()
            if key:
                self._cache[key] = t

    def add(self, trigger: dict) -> None:
        key = trigger["command"].strip().lower()
        if key:
            self._cache[key] = trigger

    def remove(self, command: str) -> None:
        self._cache.pop(command.strip().lower(), None)

    def clear(self) -> None:
        self._cache.clear()

    @property
    def count(self) -> int:
        return len(self._cache)


# Module-level singleton
_trigger_cache = TriggerCache()


def get_trigger_cache() -> TriggerCache:
    return _trigger_cache


def _trigger_to_dict(t: Trigger) -> dict:
    return {
        "id": t.id,
        "command": t.command,
        "send_as": t.send_as,
        "def": t.def_,
        "min_class": t.min_class,
        "max_class": t.max_class,
        "flags": t.flags,
        "seconds": t.seconds,
    }


async def sync_trigger_cache(session: AsyncSession) -> int:
    """Load all triggers from DB into cache. Returns count."""
    result = await session.execute(select(Trigger))
    triggers = result.scalars().all()
    entries = [_trigger_to_dict(t) for t in triggers]
    _trigger_cache.load(entries)
    _log.info("Trigger cache loaded: %d entries", len(entries))
    return len(entries)


async def get_all_triggers(session: AsyncSession) -> list[Trigger]:
    result = await session.execute(select(Trigger))
    return list(result.scalars().all())


async def get_trigger_by_id(session: AsyncSession, trigger_id: int) -> Optional[Trigger]:
    result = await session.execute(select(Trigger).where(Trigger.id == trigger_id))
    return result.scalar_one_or_none()


async def create_trigger(
    session: AsyncSession,
    *,
    command: str,
    response: str,
    send_as: str = "",
    min_class: int = 1,
    max_class: int = 10,
    flags: int = TriggerFlags.SEND_MAIN,
    seconds: int = 0,
) -> Trigger:
    trigger = Trigger(
        command=command,
        def_=response,
        send_as=send_as,
        min_class=min_class,
        max_class=max_class,
        flags=flags,
        seconds=seconds,
    )
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)
    _trigger_cache.add(_trigger_to_dict(trigger))
    _log.info("Created trigger: %s -> %s", command, response[:50])
    return trigger


async def remove_trigger(session: AsyncSession, trigger: Trigger) -> None:
    cmd = trigger.command
    await session.delete(trigger)
    await session.commit()
    _trigger_cache.remove(cmd)
    _log.info("Removed trigger: %s", cmd)


async def update_trigger(
    session: AsyncSession,
    trigger: Trigger,
    **kwargs,
) -> Trigger:
    for key, value in kwargs.items():
        if hasattr(trigger, key):
            setattr(trigger, key, value)
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)
    _trigger_cache.add(_trigger_to_dict(trigger))
    return trigger


def process_chat_trigger(nick: str, message: str, user_class: int, hub_ctx) -> Optional[str]:
    """Check if a chat message matches a trigger and return the response.

    This is called synchronously from the OnChatMessage callback.
    Returns the formatted response string, or None if no match.

    The caller is responsible for sending the response via hub_ctx.
    """
    match = _trigger_cache.match(message, user_class)
    if match is None:
        return None

    response = match["def"]
    # Simple variable substitution
    response = response.replace("%[nick]", nick)
    response = response.replace("%[CFG:hub_name]", hub_ctx.hub_name if hub_ctx else "Hub")

    return response
