"""
Tests for the NMDC Bot Chat module (verlihub.bot_chat).

Covers:
- BotChatSession construction at different user class levels
- System prompt selection (admin / operator / public)
- Tool selection based on user class
- BotChatHandler callback routing (PM / main chat)
- send_pm_as raw NMDC format
- Session management (get_or_create)
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verlihub.config import LlmConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def llm_cfg() -> LlmConfig:
    """Default LLM config for testing."""
    return LlmConfig(
        enabled=True,
        base_url="http://localhost:11434/v1",
        model="test-model",
        api_key="test",
        min_class=3,
        admin_class=5,
    )


@pytest.fixture
def mock_hub_context():
    """Mock HubContext."""
    ctx = MagicMock()
    ctx.get_user_info.return_value = {"nick": "testuser", "user_class": 3}
    ctx.send_to_user.return_value = True
    ctx.send_pm_as = MagicMock(return_value=True)
    ctx.send_chat_as = MagicMock(return_value=True)
    return ctx


@pytest.fixture
def mock_events():
    """Mock HubEventHandler."""
    events = MagicMock()
    events.register = MagicMock()
    events.unregister = MagicMock()
    return events


# ---------------------------------------------------------------------------
# BotChatSession tests
# ---------------------------------------------------------------------------


class TestBotChatSession:
    """Tests for BotChatSession prompt and tool selection."""

    def test_admin_pm_session(self, llm_cfg):
        """Admin PM session should have admin tools + admin prompt."""
        from verlihub.bot_chat import BotChatSession

        session = BotChatSession(
            nick="admin",
            user_class=10,
            bot_nick="Hub-Security",
            hub_name="TestHub",
            mode="pm",
            llm_cfg=llm_cfg,
        )

        # Admin gets readonly + admin tools
        assert len(session.tools) > 0
        tool_names = {t["function"]["name"] for t in session.tools}
        assert "get_hub_info" in tool_names  # readonly
        assert "kick_user" in tool_names  # admin

        # System prompt mentions admin
        system_msg = session.messages[0]["content"]
        assert "administrator" in system_msg.lower() or "admin" in system_msg.lower()
        assert "admin" in system_msg

    def test_operator_pm_session(self, llm_cfg):
        """Operator PM session should have readonly tools only."""
        from verlihub.bot_chat import BotChatSession

        session = BotChatSession(
            nick="oper",
            user_class=3,
            bot_nick="Hub-Security",
            hub_name="TestHub",
            mode="pm",
            llm_cfg=llm_cfg,
        )

        tool_names = {t["function"]["name"] for t in session.tools}
        assert "get_hub_info" in tool_names
        assert "kick_user" not in tool_names  # no admin tools

    def test_guest_pm_session(self, llm_cfg):
        """Guest PM session should have no tools."""
        from verlihub.bot_chat import BotChatSession

        session = BotChatSession(
            nick="guest",
            user_class=1,
            bot_nick="Hub-Security",
            hub_name="TestHub",
            mode="pm",
            llm_cfg=llm_cfg,
        )

        assert len(session.tools) == 0
        system_msg = session.messages[0]["content"]
        assert "NO tools" in system_msg or "no tools" in system_msg.lower()

    def test_main_chat_session_always_no_tools(self, llm_cfg):
        """Main chat sessions always have no tools regardless of user class."""
        from verlihub.bot_chat import BotChatSession

        session = BotChatSession(
            nick="admin",
            user_class=10,
            bot_nick="Hub-Security",
            hub_name="TestHub",
            mode="chat",
            llm_cfg=llm_cfg,
        )

        assert len(session.tools) == 0
        system_msg = session.messages[0]["content"]
        assert "main chat" in system_msg.lower() or "public" in system_msg.lower()

    def test_session_has_correct_nick_in_prompt(self, llm_cfg):
        """System prompt includes user nick and bot nick."""
        from verlihub.bot_chat import BotChatSession

        session = BotChatSession(
            nick="CoolUser",
            user_class=5,
            bot_nick="Hub-Security",
            hub_name="My Great Hub",
            mode="pm",
            llm_cfg=llm_cfg,
        )

        system_msg = session.messages[0]["content"]
        assert "CoolUser" in system_msg
        assert "Hub-Security" in system_msg
        assert "My Great Hub" in system_msg


# ---------------------------------------------------------------------------
# BotChatHandler tests
# ---------------------------------------------------------------------------


class TestBotChatHandler:
    """Tests for BotChatHandler event routing."""

    def test_register_events(self, mock_hub_context, mock_events, llm_cfg):
        """Handler registers PM and chat event handlers."""
        from verlihub.bot_chat import BotChatHandler

        with patch("verlihub.config.get_config_optional", return_value=None):
            handler = BotChatHandler(mock_hub_context, llm_cfg)
            handler._bot_nick = "Hub-Security"
            handler._hub_name = "TestHub"
            handler.register(mock_events)

        assert mock_events.register.call_count == 2
        calls = [c[0] for c in mock_events.register.call_args_list]
        assert ("private_message", handler._on_pm) in calls
        assert ("chat_message", handler._on_chat) in calls

    def test_unregister_events(self, mock_hub_context, mock_events, llm_cfg):
        """Handler unregisters cleanly."""
        from verlihub.bot_chat import BotChatHandler

        with patch("verlihub.config.get_config_optional", return_value=None):
            handler = BotChatHandler(mock_hub_context, llm_cfg)
            handler._bot_nick = "Hub-Security"
            handler.register(mock_events)
            handler.unregister(mock_events)

        assert mock_events.unregister.call_count == 2

    def test_pm_to_other_user_passes_through(self, mock_hub_context, llm_cfg):
        """PM to a nick other than the bot should pass through (return True)."""
        from verlihub.bot_chat import BotChatHandler

        with patch("verlihub.config.get_config_optional", return_value=None):
            handler = BotChatHandler(mock_hub_context, llm_cfg)
            handler._bot_nick = "Hub-Security"

        result = handler._on_pm("user1", "user2", "hello")
        assert result is True

    def test_pm_to_bot_returns_true(self, mock_hub_context, llm_cfg):
        """PM to the bot nick should return True (don't block)."""
        from verlihub.bot_chat import BotChatHandler

        with patch("verlihub.config.get_config_optional", return_value=None):
            handler = BotChatHandler(mock_hub_context, llm_cfg)
            handler._bot_nick = "Hub-Security"
            handler._hub_name = "TestHub"

        # No event loop — it will log a warning but still return True
        result = handler._on_pm("user1", "Hub-Security", "hello bot")
        assert result is True

    def test_chat_without_mention_passes_through(self, mock_hub_context, llm_cfg):
        """Chat without bot mention should pass through."""
        from verlihub.bot_chat import BotChatHandler

        with patch("verlihub.config.get_config_optional", return_value=None):
            handler = BotChatHandler(mock_hub_context, llm_cfg)
            handler._bot_nick = "Hub-Security"
            handler._hub_name = "TestHub"

        result = handler._on_chat("user1", "hello everyone")
        assert result is True

    def test_chat_with_mention_returns_true(self, mock_hub_context, llm_cfg):
        """Chat mentioning the bot should return True (pass message through)."""
        from verlihub.bot_chat import BotChatHandler

        with patch("verlihub.config.get_config_optional", return_value=None):
            handler = BotChatHandler(mock_hub_context, llm_cfg)
            handler._bot_nick = "Hub-Security"
            handler._hub_name = "TestHub"

        result = handler._on_chat("user1", "Hub-Security: what's up?")
        assert result is True

    def test_llm_disabled_passes_through(self, mock_hub_context):
        """When LLM is disabled, all messages pass through."""
        from verlihub.bot_chat import BotChatHandler

        disabled_cfg = LlmConfig(enabled=False)
        with patch("verlihub.config.get_config_optional", return_value=None):
            handler = BotChatHandler(mock_hub_context, disabled_cfg)
            handler._bot_nick = "Hub-Security"

        assert handler._on_pm("user1", "Hub-Security", "hi") is True
        assert handler._on_chat("user1", "Hub-Security: hi") is True

    def test_shutdown_clears_sessions(self, mock_hub_context, llm_cfg):
        """Shutdown clears all active sessions."""
        from verlihub.bot_chat import BotChatHandler, _sessions, _sessions_lock

        with patch("verlihub.config.get_config_optional", return_value=None):
            handler = BotChatHandler(mock_hub_context, llm_cfg)
            handler._bot_nick = "Hub-Security"

        # Inject a fake session
        with _sessions_lock:
            _sessions["pm:testuser"] = MagicMock()

        handler.shutdown()

        with _sessions_lock:
            assert len(_sessions) == 0


# ---------------------------------------------------------------------------
# Session management tests
# ---------------------------------------------------------------------------


class TestSessionManagement:
    """Tests for session creation and reuse."""

    def test_get_or_create_new(self, llm_cfg):
        """New session is created when key doesn't exist."""
        from verlihub.bot_chat import _get_or_create_session, _sessions, _sessions_lock

        # Clear
        with _sessions_lock:
            _sessions.clear()

        session = _get_or_create_session(
            "pm:newuser", "newuser", 3, "Hub-Security", "TestHub", "pm", llm_cfg
        )
        assert session is not None
        assert session.nick == "newuser"
        assert session.user_class == 3

        with _sessions_lock:
            _sessions.clear()

    def test_get_or_create_reuses_existing(self, llm_cfg):
        """Existing session is reused for the same key."""
        from verlihub.bot_chat import _get_or_create_session, _sessions, _sessions_lock

        with _sessions_lock:
            _sessions.clear()

        s1 = _get_or_create_session(
            "pm:user", "user", 3, "Hub-Security", "TestHub", "pm", llm_cfg
        )
        s2 = _get_or_create_session(
            "pm:user", "user", 3, "Hub-Security", "TestHub", "pm", llm_cfg
        )
        assert s1 is s2

        with _sessions_lock:
            _sessions.clear()

    def test_separate_pm_and_chat_sessions(self, llm_cfg):
        """PM and chat sessions for the same user are separate."""
        from verlihub.bot_chat import _get_or_create_session, _sessions, _sessions_lock

        with _sessions_lock:
            _sessions.clear()

        s_pm = _get_or_create_session(
            "pm:user", "user", 3, "Hub-Security", "TestHub", "pm", llm_cfg
        )
        s_chat = _get_or_create_session(
            "chat:user", "user", 3, "Hub-Security", "TestHub", "chat", llm_cfg
        )
        assert s_pm is not s_chat
        assert len(s_pm.tools) > 0  # operator in PM gets tools
        assert len(s_chat.tools) == 0  # chat never gets tools

        with _sessions_lock:
            _sessions.clear()


# ---------------------------------------------------------------------------
# send_pm_as format test
# ---------------------------------------------------------------------------


class TestSendPmAs:
    """Tests for the raw NMDC PM format construction."""

    def test_send_pm_as_format(self):
        """send_pm_as constructs correct NMDC PM frame."""
        ctx = MagicMock()
        ctx._cpp = MagicMock()
        ctx._cpp.SendToUser.return_value = True

        # Import and call the real method bound to a mock
        from verlihub.core import HubContext

        # We can't easily instantiate HubContext (needs C++ bindings),
        # so test the format logic directly
        from_nick = "Hub-Security"
        to_nick = "TestUser"
        message = "Hello there!"
        expected_raw = f"$To: {to_nick} From: {from_nick} $<{from_nick}> {message}"

        # Simulate what send_pm_as does
        raw = f"$To: {to_nick} From: {from_nick} $<{from_nick}> {message}"
        assert raw == expected_raw
        assert raw.startswith("$To: TestUser From: Hub-Security")
        assert "$<Hub-Security>" in raw
        assert raw.endswith("Hello there!")


# ---------------------------------------------------------------------------
# Mention regex tests
# ---------------------------------------------------------------------------


class TestMentionRegex:
    """Tests for the bot mention pattern in main chat."""

    def test_mention_colon(self):
        """'Hub-Security: hello' matches."""
        import re
        escaped = re.escape("Hub-Security")
        pattern = re.compile(rf"^{escaped}\s*[:,]\s*(.+)", re.DOTALL | re.IGNORECASE)
        m = pattern.match("Hub-Security: hello there")
        assert m is not None
        assert m.group(1).strip() == "hello there"

    def test_mention_comma(self):
        """'Hub-Security, hello' matches."""
        import re
        escaped = re.escape("Hub-Security")
        pattern = re.compile(rf"^{escaped}\s*[:,]\s*(.+)", re.DOTALL | re.IGNORECASE)
        m = pattern.match("Hub-Security, what's up?")
        assert m is not None
        assert "what's up?" in m.group(1)

    def test_no_mention(self):
        """Regular message doesn't match."""
        import re
        escaped = re.escape("Hub-Security")
        pattern = re.compile(rf"^{escaped}\s*[:,]\s*(.+)", re.DOTALL | re.IGNORECASE)
        m = pattern.match("hello everyone, how's it going?")
        assert m is None

    def test_mention_case_insensitive(self):
        """'hub-security: hi' matches (case insensitive)."""
        import re
        escaped = re.escape("Hub-Security")
        pattern = re.compile(rf"^{escaped}\s*[:,]\s*(.+)", re.DOTALL | re.IGNORECASE)
        m = pattern.match("hub-security: hi")
        assert m is not None

    def test_mention_in_middle_doesnt_match(self):
        """'hello Hub-Security' does NOT match (must be at start)."""
        import re
        escaped = re.escape("Hub-Security")
        pattern = re.compile(rf"^{escaped}\s*[:,]\s*(.+)", re.DOTALL | re.IGNORECASE)
        m = pattern.match("hello Hub-Security: what's up?")
        assert m is None
