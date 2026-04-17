"""
Tests for the persistent bot memory module (verlihub.bot_memory).

Covers:
- BotMemory CRUD operations (save, recall, list, delete, context summary)
- Mood recording on notes
- Relative time formatting
- execute_memory_tool dispatcher
- Edge cases (empty inputs, missing notes, duplicate topics)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verlihub.bot.memory import BotMemory, _relative_time, build_memory_tools, execute_memory_tool


# ---------------------------------------------------------------------------
# _relative_time helper
# ---------------------------------------------------------------------------

class TestRelativeTime:
    """Tests for the _relative_time() helper."""

    def test_just_now(self):
        now = datetime.now(timezone.utc)
        assert _relative_time(now) == "just now"

    def test_minutes(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert _relative_time(dt) == "5m ago"

    def test_hours(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        assert _relative_time(dt) == "3h ago"

    def test_days(self):
        dt = datetime.now(timezone.utc) - timedelta(days=7)
        assert _relative_time(dt) == "7d ago"

    def test_months(self):
        dt = datetime.now(timezone.utc) - timedelta(days=60)
        assert _relative_time(dt) == "2mo ago"

    def test_years(self):
        dt = datetime.now(timezone.utc) - timedelta(days=400)
        assert _relative_time(dt) == "1y ago"

    def test_naive_datetime(self):
        """Naive datetime should be treated as UTC."""
        dt = datetime.utcnow() - timedelta(hours=2)
        assert _relative_time(dt) == "2h ago"


# ---------------------------------------------------------------------------
# Async fixtures with in-memory SQLite backend
# ---------------------------------------------------------------------------

@pytest.fixture
async def memory_db():
    """Set up an in-memory SQLite database with the BotNote table.

    Patches ``get_async_session`` so BotMemory methods use this session.
    """
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlmodel import SQLModel

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    # Create all SQLModel tables (including BotNote)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def _mock_get_async_session():
        async with async_session_factory() as session:
            async with session.begin():
                yield session

    with patch("verlihub.models.database.get_async_session", _mock_get_async_session):
        yield engine

    await engine.dispose()


@pytest.fixture
def memory() -> BotMemory:
    """BotMemory instance without mood function."""
    return BotMemory()


@pytest.fixture
def memory_with_mood() -> BotMemory:
    """BotMemory instance with a mood function."""
    return BotMemory(mood_fn=lambda: "cheerful")


# ---------------------------------------------------------------------------
# BotMemory.save_note
# ---------------------------------------------------------------------------

class TestSaveNote:

    @pytest.mark.asyncio
    async def test_save_new_note(self, memory_db, memory):
        result = await memory.save_note("greeting", "Hello world!")
        assert "Saved new note" in result
        assert "'greeting'" in result

    @pytest.mark.asyncio
    async def test_update_existing_note(self, memory_db, memory):
        await memory.save_note("topic1", "First version")
        result = await memory.save_note("topic1", "Updated version")
        assert "Updated note" in result

    @pytest.mark.asyncio
    async def test_save_empty_topic(self, memory_db, memory):
        result = await memory.save_note("", "content")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_save_empty_content(self, memory_db, memory):
        result = await memory.save_note("topic", "")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_save_records_mood(self, memory_db, memory_with_mood):
        await memory_with_mood.save_note("vibe", "feeling good")
        result = await memory_with_mood.recall_notes("vibe")
        assert "cheerful" in result

    @pytest.mark.asyncio
    async def test_save_without_mood(self, memory_db, memory):
        await memory.save_note("plain", "no mood")
        result = await memory.recall_notes("plain")
        assert "plain" in result
        # No mood tag should appear
        assert "[mood:" not in result


# ---------------------------------------------------------------------------
# BotMemory.recall_notes
# ---------------------------------------------------------------------------

class TestRecallNotes:

    @pytest.mark.asyncio
    async def test_recall_matching(self, memory_db, memory):
        await memory.save_note("user:alice", "Loves jazz music")
        result = await memory.recall_notes("alice")
        assert "jazz" in result.lower()

    @pytest.mark.asyncio
    async def test_recall_no_match(self, memory_db, memory):
        result = await memory.recall_notes("nonexistent")
        assert "No notes found" in result

    @pytest.mark.asyncio
    async def test_recall_empty_query(self, memory_db, memory):
        result = await memory.recall_notes("")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_recall_content_search(self, memory_db, memory):
        """Search should match content, not just topic."""
        await memory.save_note("misc", "Python is a great language")
        result = await memory.recall_notes("Python")
        assert "great language" in result

    @pytest.mark.asyncio
    async def test_recall_shows_relative_time(self, memory_db, memory):
        await memory.save_note("recent", "saved just now")
        result = await memory.recall_notes("recent")
        assert "just now" in result or "m ago" in result


# ---------------------------------------------------------------------------
# BotMemory.list_notes
# ---------------------------------------------------------------------------

class TestListNotes:

    @pytest.mark.asyncio
    async def test_list_empty(self, memory_db, memory):
        result = await memory.list_notes()
        assert "No notes" in result

    @pytest.mark.asyncio
    async def test_list_multiple(self, memory_db, memory):
        await memory.save_note("topic1", "content1")
        await memory.save_note("topic2", "content2")
        result = await memory.list_notes()
        assert "2 notes" in result
        assert "topic1" in result
        assert "topic2" in result

    @pytest.mark.asyncio
    async def test_list_shows_mood(self, memory_db, memory_with_mood):
        await memory_with_mood.save_note("happy_note", "yay")
        result = await memory_with_mood.list_notes()
        assert "cheerful" in result


# ---------------------------------------------------------------------------
# BotMemory.delete_note
# ---------------------------------------------------------------------------

class TestDeleteNote:

    @pytest.mark.asyncio
    async def test_delete_existing(self, memory_db, memory):
        await memory.save_note("to_delete", "bye bye")
        result = await memory.delete_note("to_delete")
        assert "Deleted" in result
        # Verify it's gone
        result = await memory.list_notes()
        assert "No notes" in result

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, memory_db, memory):
        result = await memory.delete_note("nope")
        assert "No note found" in result

    @pytest.mark.asyncio
    async def test_delete_empty_topic(self, memory_db, memory):
        result = await memory.delete_note("")
        assert "Error" in result


# ---------------------------------------------------------------------------
# BotMemory.get_context_summary
# ---------------------------------------------------------------------------

class TestContextSummary:

    @pytest.mark.asyncio
    async def test_empty_summary(self, memory_db, memory):
        result = await memory.get_context_summary()
        assert result == ""

    @pytest.mark.asyncio
    async def test_summary_includes_notes(self, memory_db, memory):
        await memory.save_note("rule1", "No spamming allowed in the hub")
        result = await memory.get_context_summary()
        assert "rule1" in result
        assert "No spamming" in result
        assert "recall_notes" in result  # prompts towards the tool

    @pytest.mark.asyncio
    async def test_summary_limit(self, memory_db, memory):
        """Context summary should respect the max_notes limit."""
        for i in range(15):
            await memory.save_note(f"n{i}", f"Note number {i}")
        result = await memory.get_context_summary(max_notes=5)
        # Should have exactly 5 note lines
        lines = [l for l in result.split("\n") if l.startswith("- ")]
        assert len(lines) == 5


# ---------------------------------------------------------------------------
# BotMemory._current_mood
# ---------------------------------------------------------------------------

class TestCurrentMood:

    def test_mood_with_function(self):
        mem = BotMemory(mood_fn=lambda: "happy")
        assert mem._current_mood() == "happy"

    def test_mood_without_function(self):
        mem = BotMemory()
        assert mem._current_mood() == ""

    def test_mood_function_exception(self):
        def broken():
            raise RuntimeError("oops")
        mem = BotMemory(mood_fn=broken)
        assert mem._current_mood() == ""


# ---------------------------------------------------------------------------
# build_memory_tools
# ---------------------------------------------------------------------------

class TestBuildMemoryTools:

    def test_returns_four_tools(self):
        tools = build_memory_tools()
        assert len(tools) == 4

    def test_tool_names(self):
        tools = build_memory_tools()
        names = {t["function"]["name"] for t in tools}
        assert names == {"save_note", "recall_notes", "list_notes", "delete_note"}

    def test_tool_format(self):
        tools = build_memory_tools()
        for tool in tools:
            assert tool["type"] == "function"
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]


# ---------------------------------------------------------------------------
# execute_memory_tool dispatcher
# ---------------------------------------------------------------------------

class TestExecuteMemoryTool:

    @pytest.mark.asyncio
    async def test_save_note_dispatch(self, memory_db, memory):
        result = await execute_memory_tool(memory, "save_note", {"topic": "t", "content": "c"})
        assert result is not None
        assert "Saved" in result

    @pytest.mark.asyncio
    async def test_recall_notes_dispatch(self, memory_db, memory):
        await memory.save_note("x", "y")
        result = await execute_memory_tool(memory, "recall_notes", {"query": "x"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_list_notes_dispatch(self, memory_db, memory):
        result = await execute_memory_tool(memory, "list_notes", {})
        assert result is not None

    @pytest.mark.asyncio
    async def test_delete_note_dispatch(self, memory_db, memory):
        result = await execute_memory_tool(memory, "delete_note", {"topic": "nope"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_unknown_tool(self, memory_db, memory):
        result = await execute_memory_tool(memory, "unknown_tool", {})
        assert result is None


# ---------------------------------------------------------------------------
# Integration: full lifecycle
# ---------------------------------------------------------------------------

class TestMemoryLifecycle:

    @pytest.mark.asyncio
    async def test_save_recall_update_delete(self, memory_db, memory_with_mood):
        """Full CRUD lifecycle."""
        mem = memory_with_mood

        # Save
        r = await mem.save_note("user:bob", "Bob prefers Linux")
        assert "Saved" in r

        # Recall
        r = await mem.recall_notes("bob")
        assert "Linux" in r
        assert "cheerful" in r

        # Update
        r = await mem.save_note("user:bob", "Bob now uses FreeBSD")
        assert "Updated" in r

        # Recall updated
        r = await mem.recall_notes("bob")
        assert "FreeBSD" in r

        # List
        r = await mem.list_notes()
        assert "1 note" in r

        # Delete
        r = await mem.delete_note("user:bob")
        assert "Deleted" in r

        # Verify empty
        r = await mem.list_notes()
        assert "No notes" in r
