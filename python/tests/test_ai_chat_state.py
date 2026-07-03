"""
Tests for AI chat background-task state management.

Covers:
- ChatSession.emit() buffering and WS forwarding
- ChatSession.attach_ws() / detach_ws() lifecycle
- ChatSession.replay_buffered_events()
- _run_llm_request background task completion signals
- ws_llm_chat reconnect with pending / completed tasks
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from dataclasses import dataclass

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_cfg():
    """Return a minimal LlmConfig-like object."""
    cfg = MagicMock()
    cfg.enabled = True
    cfg.base_url = "http://fake:11434/v1"
    cfg.model = "test-model"
    cfg.api_key = "sk-test"
    cfg.temperature = 0.7
    cfg.max_tokens = 512
    cfg.max_tool_rounds = 5
    cfg.min_class = 0
    cfg.admin_class = 10
    cfg.system_prompt = "You are helpful."
    return cfg


def _make_user(nick="testuser", user_class=10):
    user = MagicMock()
    user.nick = nick
    user.user_class = user_class
    return user


def _make_ws():
    """Return a mock WebSocket with async send_json."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    ws.receive_json = AsyncMock()
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# ChatSession unit tests
# ---------------------------------------------------------------------------

class TestChatSessionEmit:
    """session.emit() should buffer events AND send to WS if attached."""

    @pytest.fixture
    def session(self):
        from verlihub.api.routes.llm import ChatSession
        s = ChatSession(
            user=_make_user(),
            is_admin=True,
            llm_cfg=_make_llm_cfg(),
        )
        return s

    @pytest.mark.asyncio
    async def test_emit_buffers_event(self, session):
        """Emit should add event to the internal buffer."""
        await session.emit({"type": "thinking"})
        assert len(session._event_buffer) == 1
        assert session._event_buffer[0] == {"type": "thinking"}

    @pytest.mark.asyncio
    async def test_emit_multiple_events_buffered_in_order(self, session):
        """Multiple emits accumulate in order."""
        await session.emit({"type": "thinking"})
        await session.emit({"type": "stream_start"})
        await session.emit({"type": "stream_delta", "content": "hello"})
        assert len(session._event_buffer) == 3
        types = [e["type"] for e in session._event_buffer]
        assert types == ["thinking", "stream_start", "stream_delta"]

    @pytest.mark.asyncio
    async def test_emit_forwards_to_ws_when_attached(self, session):
        """If a WS is attached, emit should forward events to it."""
        ws = _make_ws()
        session.attach_ws(ws)
        await session.emit({"type": "thinking"})
        ws.send_json.assert_awaited_once_with({"type": "thinking"})

    @pytest.mark.asyncio
    async def test_emit_buffers_without_ws(self, session):
        """Without an attached WS, emit still buffers the event."""
        await session.emit({"type": "response", "content": "hi"})
        assert len(session._event_buffer) == 1
        # No WS means no send
        assert session._ws_ref is None

    @pytest.mark.asyncio
    async def test_emit_detaches_ws_on_send_failure(self, session):
        """If WS send fails, the reference should be cleared."""
        ws = _make_ws()
        ws.send_json.side_effect = RuntimeError("connection closed")
        session.attach_ws(ws)
        await session.emit({"type": "thinking"})
        # Event still buffered even though send failed
        assert len(session._event_buffer) == 1
        # WS ref cleared after failure
        assert session._ws_ref is None


class TestChatSessionAttachment:
    """attach_ws / detach_ws lifecycle."""

    @pytest.fixture
    def session(self):
        from verlihub.api.routes.llm import ChatSession
        return ChatSession(
            user=_make_user(),
            is_admin=True,
            llm_cfg=_make_llm_cfg(),
        )

    def test_attach_ws_sets_ref(self, session):
        ws = _make_ws()
        session.attach_ws(ws)
        assert session._ws_ref is ws

    def test_detach_ws_clears_ref(self, session):
        ws = _make_ws()
        session.attach_ws(ws)
        session.detach_ws()
        assert session._ws_ref is None

    def test_detach_without_attach_is_safe(self, session):
        session.detach_ws()
        assert session._ws_ref is None


class TestChatSessionReplay:
    """replay_buffered_events() should re-send all buffered events."""

    @pytest.fixture
    def session(self):
        from verlihub.api.routes.llm import ChatSession
        return ChatSession(
            user=_make_user(),
            is_admin=True,
            llm_cfg=_make_llm_cfg(),
        )

    @pytest.mark.asyncio
    async def test_replay_sends_all_events(self, session):
        """All buffered events should be sent on replay."""
        await session.emit({"type": "thinking"})
        await session.emit({"type": "stream_start"})
        await session.emit({"type": "stream_delta", "content": "hello"})
        await session.emit({"type": "stream_end", "content": "hello"})

        ws = _make_ws()
        await session.replay_buffered_events(ws)
        assert ws.send_json.await_count == 4

        sent = [call.args[0] for call in ws.send_json.await_args_list]
        assert sent[0] == {"type": "thinking"}
        assert sent[3] == {"type": "stream_end", "content": "hello"}

    @pytest.mark.asyncio
    async def test_replay_empty_buffer(self, session):
        """Replay with empty buffer should not call send."""
        ws = _make_ws()
        await session.replay_buffered_events(ws)
        ws.send_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clear_event_buffer(self, session):
        """clear_event_buffer() should empty the list."""
        await session.emit({"type": "thinking"})
        await session.emit({"type": "response", "content": "hi"})
        assert len(session._event_buffer) == 2
        session.clear_event_buffer()
        assert len(session._event_buffer) == 0


class TestChatSessionRequestDone:
    """_request_done event signals when processing finishes."""

    @pytest.fixture
    def session(self):
        from verlihub.api.routes.llm import ChatSession
        s = ChatSession(
            user=_make_user(),
            is_admin=True,
            llm_cfg=_make_llm_cfg(),
        )
        return s

    def test_initial_state_is_set(self, session):
        """Initially, _request_done should be set (no active request)."""
        assert session._request_done.is_set()

    def test_clear_and_set(self, session):
        """Clear and set should work correctly."""
        session._request_done.clear()
        assert not session._request_done.is_set()
        session._request_done.set()
        assert session._request_done.is_set()


# ---------------------------------------------------------------------------
# _run_llm_request tests
# ---------------------------------------------------------------------------

class TestRunLlmRequest:
    """Background task function behavior."""

    @pytest.fixture
    def session(self):
        from verlihub.api.routes.llm import ChatSession
        s = ChatSession(
            user=_make_user(),
            is_admin=True,
            llm_cfg=_make_llm_cfg(),
        )
        return s

    @pytest.mark.asyncio
    async def test_sets_request_done_on_success(self, session):
        """After successful completion, _request_done should be set."""
        from verlihub.api.routes.llm import _run_llm_request

        # Mock the OpenAI client to return a simple response
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Hello world"
        mock_resp.choices[0].message.model_dump.return_value = {
            "role": "assistant", "content": "Hello world"
        }

        # Create a mock stream that yields one chunk
        async def fake_stream():
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "Hello world"
            chunk.choices[0].delta.tool_calls = None
            yield chunk

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())

        session._request_done.clear()
        llm_cfg = _make_llm_cfg()
        user = _make_user()

        with patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_client):
            await _run_llm_request(session, "hi", user, True, llm_cfg, "test:key")

        assert session._request_done.is_set()
        assert session.pending_request is False

    @pytest.mark.asyncio
    async def test_sets_request_done_on_error(self, session):
        """Even on error, _request_done should be set."""
        from verlihub.api.routes.llm import _run_llm_request

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("connection refused")
        )

        session._request_done.clear()
        llm_cfg = _make_llm_cfg()
        user = _make_user()

        with patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_client):
            await _run_llm_request(session, "hi", user, True, llm_cfg, "test:key")

        assert session._request_done.is_set()
        assert session.pending_request is False
        # Error event should be buffered
        error_events = [e for e in session._event_buffer if e["type"] == "error"]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_emits_thinking_first(self, session):
        """First event emitted should be 'thinking'."""
        from verlihub.api.routes.llm import _run_llm_request

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("test error")
        )

        llm_cfg = _make_llm_cfg()
        user = _make_user()

        with patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_client):
            await _run_llm_request(session, "hi", user, True, llm_cfg, "test:key")

        assert session._event_buffer[0] == {"type": "thinking"}

    @pytest.mark.asyncio
    async def test_events_buffered_without_ws(self, session):
        """Events should be buffered even without an attached WS."""
        from verlihub.api.routes.llm import _run_llm_request

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("timeout")
        )

        llm_cfg = _make_llm_cfg()
        user = _make_user()
        session.detach_ws()

        with patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_client):
            await _run_llm_request(session, "hi", user, True, llm_cfg, "test:key")

        # Should have at least thinking + error
        assert len(session._event_buffer) >= 2

    @pytest.mark.asyncio
    async def test_task_continues_after_ws_detach(self, session):
        """If WS detaches mid-request, the task should still complete."""
        from verlihub.api.routes.llm import _run_llm_request

        ws = _make_ws()
        session.attach_ws(ws)

        # Simulate WS failure after first send
        call_count = 0
        async def failing_send(data):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("disconnected")

        ws.send_json = failing_send

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("test error for detach")
        )

        llm_cfg = _make_llm_cfg()
        user = _make_user()
        session._request_done.clear()

        with patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_client):
            await _run_llm_request(session, "hi", user, True, llm_cfg, "test:key")

        # Task completed despite WS failure
        assert session._request_done.is_set()
        assert session.pending_request is False
        # WS ref should be cleared after failure
        assert session._ws_ref is None


# ---------------------------------------------------------------------------
# Background task as asyncio.Task
# ---------------------------------------------------------------------------

class TestBackgroundTaskLifecycle:
    """Test that _run_llm_request works as a background asyncio.Task."""

    @pytest.fixture
    def session(self):
        from verlihub.api.routes.llm import ChatSession
        return ChatSession(
            user=_make_user(),
            is_admin=True,
            llm_cfg=_make_llm_cfg(),
        )

    @pytest.mark.asyncio
    async def test_task_runs_independently(self, session):
        """Task spawned with create_task should complete on its own."""
        from verlihub.api.routes.llm import _run_llm_request

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("backend down")
        )

        llm_cfg = _make_llm_cfg()
        user = _make_user()
        session._request_done.clear()

        with patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_client):
            task = asyncio.create_task(
                _run_llm_request(session, "test", user, True, llm_cfg, "k")
            )
            # Wait for it
            await session._request_done.wait()

        assert task.done()
        assert session.pending_request is False

    @pytest.mark.asyncio
    async def test_replay_after_task_done(self, session):
        """After task completes, buffered events can be replayed to a new WS."""
        from verlihub.api.routes.llm import _run_llm_request

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("refused")
        )

        llm_cfg = _make_llm_cfg()
        user = _make_user()

        with patch("verlihub.api.routes.llm._get_openai_client", return_value=mock_client):
            await _run_llm_request(session, "yo", user, True, llm_cfg, "k")

        # Now replay to a new WS
        ws = _make_ws()
        await session.replay_buffered_events(ws)
        assert ws.send_json.await_count == len(session._event_buffer)


# ---------------------------------------------------------------------------
# Handle actions in text signature
# ---------------------------------------------------------------------------

class TestHandleActionsSignature:
    """_handle_actions_in_text now accepts a single emit callable."""

    @pytest.mark.asyncio
    async def test_no_actions_returns_stripped(self):
        from verlihub.api.routes.llm import _handle_actions_in_text

        session = MagicMock()
        session.messages = []
        client = AsyncMock()
        cfg = _make_llm_cfg()
        user = _make_user()
        emit = AsyncMock()

        result = await _handle_actions_in_text(
            "Hello world", session, client, cfg, user, True, emit,
        )
        assert result == "Hello world"
        emit.assert_not_awaited()
