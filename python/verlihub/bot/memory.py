"""
Persistent memory for the hub security bot.

Uses the application's shared database (MySQL / PostgreSQL / SQLite) via
the ``BotNote`` SQLModel so there's no separate SQLite file to manage.

The bot accesses memory through LLM tool calls:

  * ``save_note(topic, content)``  — create or update a note
  * ``recall_notes(query)``        — search notes by keyword
  * ``list_notes()``               — list all saved topics
  * ``delete_note(topic)``         — remove a note

Every note records **when** it was saved and the bot's **mood** at that
time, giving the LLM a sense of temporal context and emotional history.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

log = logging.getLogger("verlihub.bot.memory")


def _relative_time(dt: datetime) -> str:
    """Human-readable relative time string (e.g. '3 hours ago')."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


class BotMemory:
    """Database-backed persistent memory for the hub bot.

    Uses the application's async database sessions (from
    ``verlihub.models.database``) so notes are stored alongside all
    other hub data — no separate SQLite file needed.
    """

    def __init__(self, mood_fn: "Any | None" = None):
        """
        Parameters
        ----------
        mood_fn:
            Optional callable that returns the current mood name
            (e.g. ``mood_engine.get_mood().name``).  When provided the
            mood is recorded alongside every saved note.
        """
        self._mood_fn = mood_fn
        log.info("Bot memory initialised (application database)")

    def _current_mood(self) -> str:
        """Return the current mood name, or empty string."""
        if self._mood_fn:
            try:
                return self._mood_fn()
            except Exception:
                pass
        return ""

    # ── Public API (called from LLM tool handlers) ───────────────────

    async def save_note(self, topic: str, content: str) -> str:
        """Create or update a note.  Returns confirmation text."""
        from verlihub.models import BotNote
        from verlihub.models.database import get_async_session

        topic = topic.strip()
        content = content.strip()
        if not topic or not content:
            return "Error: topic and content are both required."

        now = datetime.now(timezone.utc)
        mood = self._current_mood()

        try:
            async with get_async_session() as session:
                stmt = select(BotNote).where(BotNote.topic == topic)
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if existing:
                    existing.content = content
                    existing.mood = mood
                    existing.updated_at = now
                    session.add(existing)
                    return f"Updated note '{topic}'."
                else:
                    note = BotNote(
                        topic=topic,
                        content=content,
                        mood=mood,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(note)
                    return f"Saved new note '{topic}'."
        except Exception as exc:
            log.exception("Failed to save note '%s'", topic)
            return f"Error saving note: {exc}"

    async def recall_notes(self, query: str) -> str:
        """Search notes by keyword (case-insensitive LIKE).

        Returns matching notes with their content, relative timestamps,
        and the mood the bot was in when the note was saved.
        """
        from verlihub.models import BotNote
        from verlihub.models.database import get_async_session

        query = query.strip()
        if not query:
            return "Error: query is required."

        try:
            async with get_async_session() as session:
                stmt = (
                    select(BotNote)
                    .where(
                        BotNote.topic.contains(query)  # type: ignore[union-attr]
                        | BotNote.content.contains(query)  # type: ignore[union-attr]
                    )
                    .order_by(BotNote.updated_at.desc())  # type: ignore[union-attr]
                    .limit(20)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()

            if not rows:
                return f"No notes found matching '{query}'."

            parts: list[str] = []
            for note in rows:
                age = _relative_time(note.updated_at)
                mood_tag = f" [mood: {note.mood}]" if note.mood else ""
                parts.append(f"[{note.topic}] (updated {age}{mood_tag})\n{note.content}")
            return "\n---\n".join(parts)
        except Exception as exc:
            log.exception("Failed to recall notes for '%s'", query)
            return f"Error searching notes: {exc}"

    async def list_notes(self) -> str:
        """List all note topics with relative timestamps and moods."""
        from verlihub.models import BotNote
        from verlihub.models.database import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(BotNote).order_by(BotNote.updated_at.desc())  # type: ignore[union-attr]
                result = await session.execute(stmt)
                rows = result.scalars().all()

            if not rows:
                return "No notes saved yet."

            lines: list[str] = []
            for note in rows:
                age = _relative_time(note.updated_at)
                mood_tag = f" [{note.mood}]" if note.mood else ""
                lines.append(f"- {note.topic} (updated {age}{mood_tag})")
            return f"{len(lines)} notes:\n" + "\n".join(lines)
        except Exception as exc:
            log.exception("Failed to list notes")
            return f"Error listing notes: {exc}"

    async def delete_note(self, topic: str) -> str:
        """Delete a note by topic."""
        from verlihub.models import BotNote
        from verlihub.models.database import get_async_session

        topic = topic.strip()
        if not topic:
            return "Error: topic is required."

        try:
            async with get_async_session() as session:
                stmt = select(BotNote).where(BotNote.topic == topic)
                result = await session.execute(stmt)
                note = result.scalars().first()
                if note:
                    await session.delete(note)
                    return f"Deleted note '{topic}'."
                return f"No note found with topic '{topic}'."
        except Exception as exc:
            log.exception("Failed to delete note '%s'", topic)
            return f"Error deleting note: {exc}"

    async def get_context_summary(self, max_notes: int = 10) -> str:
        """Return a compact summary of recent notes for prompt injection.

        Includes relative timestamps and mood tags so the LLM has a
        sense of time and its own emotional history.
        """
        from verlihub.models import BotNote
        from verlihub.models.database import get_async_session

        try:
            async with get_async_session() as session:
                stmt = (
                    select(BotNote)
                    .order_by(BotNote.updated_at.desc())  # type: ignore[union-attr]
                    .limit(max_notes)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()

            if not rows:
                return ""

            lines: list[str] = []
            for note in rows:
                age = _relative_time(note.updated_at)
                snippet = note.content[:120]
                mood_tag = f" [{note.mood}]" if note.mood else ""
                lines.append(f"- {note.topic}: {snippet}… ({age}{mood_tag})")

            return (
                "You have the following notes saved in your memory:\n"
                + "\n".join(lines)
                + "\nUse the recall_notes tool to look up full details."
            )
        except Exception as exc:
            log.debug("Failed to get context summary: %s", exc)
            return ""


# ── Tool definitions (OpenAI function-calling format) ────────────────

def build_memory_tools() -> list[dict[str, Any]]:
    """Return tool schemas for the bot's memory capabilities."""
    return [
        {
            "type": "function",
            "function": {
                "name": "save_note",
                "description": (
                    "Save a note to your persistent memory.  Use this to "
                    "remember facts, user preferences, things you've learned, "
                    "or anything you want to recall later.  If a note with "
                    "the same topic already exists it will be updated.  "
                    "Your current mood is automatically recorded with the note."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Short topic/title for the note (e.g. 'user:alice:fav_band', 'hub:rules', 'fact:doge_origin')",
                        },
                        "content": {
                            "type": "string",
                            "description": "The content to save.",
                        },
                    },
                    "required": ["topic", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "recall_notes",
                "description": (
                    "Search your persistent memory for notes matching a "
                    "keyword.  Returns matching notes with their content, "
                    "how long ago they were saved, and the mood you were "
                    "in when you wrote them."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Keyword to search for in note topics and content.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_notes",
                "description": (
                    "List all topics saved in your persistent memory, "
                    "showing when each was last updated and what mood "
                    "you were in."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_note",
                "description": "Delete a note from your persistent memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "The topic of the note to delete.",
                        },
                    },
                    "required": ["topic"],
                },
            },
        },
    ]


async def execute_memory_tool(
    memory: BotMemory,
    fn_name: str,
    fn_args: dict[str, Any],
) -> str | None:
    """Execute a memory tool call.  Returns result string, or None if
    *fn_name* is not a memory tool."""
    if fn_name == "save_note":
        return await memory.save_note(fn_args.get("topic", ""), fn_args.get("content", ""))
    elif fn_name == "recall_notes":
        return await memory.recall_notes(fn_args.get("query", ""))
    elif fn_name == "list_notes":
        return await memory.list_notes()
    elif fn_name == "delete_note":
        return await memory.delete_note(fn_args.get("topic", ""))
    return None  # not a memory tool
