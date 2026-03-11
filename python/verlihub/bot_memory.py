"""
Persistent memory for the hub security bot.

Provides a lightweight SQLite-backed note store so the bot can save facts,
observations, and reminders that survive restarts.  The bot accesses memory
through LLM tool calls:

  * ``save_note(topic, content)``  — create or update a note
  * ``recall_notes(query)``        — search notes by keyword
  * ``list_notes()``               — list all saved topics
  * ``delete_note(topic)``         — remove a note

The database is a single file that can live next to the config or in a
Docker volume.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("verlihub.bot_memory")


class BotMemory:
    """SQLite-backed persistent memory for the hub bot.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Created if it doesn't exist.
        Defaults to ``bot_memory.db`` in the current directory.
    """

    def __init__(self, db_path: str = "bot_memory.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._ensure_schema()
        log.info("Bot memory initialised: %s", self._db_path)

    # ── Public API (called from LLM tool handlers) ───────────────────

    def save_note(self, topic: str, content: str) -> str:
        """Create or update a note.  Returns confirmation text."""
        topic = topic.strip()
        content = content.strip()
        if not topic or not content:
            return "Error: topic and content are both required."

        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM bot_notes WHERE topic = ?", (topic,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE bot_notes SET content = ?, updated_at = ? WHERE topic = ?",
                    (content, now, topic),
                )
                return f"Updated note '{topic}'."
            else:
                conn.execute(
                    "INSERT INTO bot_notes (topic, content, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (topic, content, now, now),
                )
                return f"Saved new note '{topic}'."

    def recall_notes(self, query: str) -> str:
        """Search notes by keyword (case-insensitive LIKE).

        Returns matching notes as a formatted string, or a "no results"
        message.
        """
        query = query.strip()
        if not query:
            return "Error: query is required."

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT topic, content, updated_at FROM bot_notes "
                "WHERE topic LIKE ? OR content LIKE ? "
                "ORDER BY updated_at DESC LIMIT 20",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()

        if not rows:
            return f"No notes found matching '{query}'."

        parts: list[str] = []
        for topic, content, updated in rows:
            parts.append(f"[{topic}] ({updated})\n{content}")
        return "\n---\n".join(parts)

    def list_notes(self) -> str:
        """List all note topics with last-updated timestamps."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT topic, updated_at FROM bot_notes ORDER BY updated_at DESC"
            ).fetchall()

        if not rows:
            return "No notes saved yet."

        lines = [f"- {topic} (updated {updated})" for topic, updated in rows]
        return f"{len(lines)} notes:\n" + "\n".join(lines)

    def delete_note(self, topic: str) -> str:
        """Delete a note by topic."""
        topic = topic.strip()
        if not topic:
            return "Error: topic is required."

        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM bot_notes WHERE topic = ?", (topic,))
        if cur.rowcount > 0:
            return f"Deleted note '{topic}'."
        return f"No note found with topic '{topic}'."

    def get_context_summary(self, max_notes: int = 10) -> str:
        """Return a compact summary of recent notes for prompt injection.

        This gives the LLM awareness of what it has stored without
        consuming too many tokens.
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT topic, substr(content, 1, 120), updated_at "
                "FROM bot_notes ORDER BY updated_at DESC LIMIT ?",
                (max_notes,),
            ).fetchall()

        if not rows:
            return ""

        lines = [f"- {topic}: {snippet}…" for topic, snippet, _ in rows]
        return (
            "You have the following notes saved in your memory:\n"
            + "\n".join(lines)
            + "\nUse the recall_notes tool to look up full details."
        )

    # ── Internals ────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_notes (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic      TEXT    NOT NULL UNIQUE,
                    content    TEXT    NOT NULL,
                    created_at TEXT    NOT NULL,
                    updated_at TEXT    NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_notes_topic
                ON bot_notes(topic)
            """)


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
                    "the same topic already exists it will be updated."
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
                    "keyword.  Returns matching notes with their content."
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
                    "List all topics saved in your persistent memory."
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
        return memory.save_note(fn_args.get("topic", ""), fn_args.get("content", ""))
    elif fn_name == "recall_notes":
        return memory.recall_notes(fn_args.get("query", ""))
    elif fn_name == "list_notes":
        return memory.list_notes()
    elif fn_name == "delete_note":
        return memory.delete_note(fn_args.get("topic", ""))
    return None  # not a memory tool
