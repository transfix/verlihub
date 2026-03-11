"""Tests for verlihub.user_info — "Your information" on connect."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from verlihub.user_info import (
    STATUS_NAT,
    STATUS_TLS,
    _format_info,
    _is_truthy,
    on_user_connect,
    register,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_info(**overrides: Any) -> dict:
    """Return a minimal user-info dict with sensible defaults."""
    info: dict[str, Any] = {
        "nick": "TestUser",
        "ip": "10.0.0.1",
        "country": "US",
        "country_name": "United States",
        "city": "Dallas",
        "status_flag": 0,
    }
    info.update(overrides)
    return info


def _make_ctx(
    *,
    config: dict[str, str] | None = None,
    user_info: dict | None = None,
    send_pm_return: bool = True,
    send_to_user_return: bool = True,
) -> MagicMock:
    """Build a mock HubContext with configurable config & user-info."""
    ctx = MagicMock()
    _config: dict[str, str] = {
        "send_user_info": "1",
        "hub_security": "Hub-Security",
        "user_info_as_pm": "0",
    }
    if config:
        _config.update(config)

    def _get_config(_section: str, key: str, default: str = "") -> str:
        return _config.get(key, default)

    ctx.get_config.side_effect = _get_config
    ctx.get_user_info.return_value = user_info if user_info is not None else _make_info()
    ctx.send_pm_as.return_value = send_pm_return
    ctx.send_to_user.return_value = send_to_user_return
    ctx.events = MagicMock()
    return ctx


# ===================================================================
# _is_truthy
# ===================================================================

class TestIsTruthy:
    """Verify the helper that interprets config string booleans."""

    @pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "Yes", "on", "42"])
    def test_truthy_values(self, val: str):
        assert _is_truthy(val) is True

    @pytest.mark.parametrize("val", ["0", "false", "False", "FALSE", "no", "No", ""])
    def test_falsy_values(self, val: str):
        assert _is_truthy(val) is False


# ===================================================================
# _format_info
# ===================================================================

class TestFormatInfo:
    """Verify the multi-line info text builder."""

    def test_basic_fields(self):
        text = _format_info(_make_info())
        assert "Your information:" in text
        assert "Nick: TestUser" in text
        assert "IP: 10.0.0.1" in text
        assert "Country: US=United States" in text
        assert "City: Dallas" in text

    def test_missing_ip_shows_question_mark(self):
        text = _format_info(_make_info(ip=None))
        # dict.get('ip', '?') returns None here, but info['ip'] is None
        # Actually ip is present but None — get returns None
        info = _make_info()
        del info["ip"]
        text = _format_info(info)
        assert "IP: ?" in text

    def test_country_hidden_when_empty(self):
        text = _format_info(_make_info(country=""))
        assert "Country:" not in text

    def test_country_hidden_when_dashes(self):
        text = _format_info(_make_info(country="--"))
        assert "Country:" not in text

    def test_city_hidden_when_empty(self):
        text = _format_info(_make_info(city=""))
        assert "City:" not in text

    def test_city_hidden_when_dashes(self):
        text = _format_info(_make_info(city="--"))
        assert "City:" not in text

    def test_tls_flag(self):
        text = _format_info(_make_info(status_flag=STATUS_TLS))
        assert "Client TLS: Yes" in text
        assert "Client NAT: No" in text

    def test_nat_flag(self):
        text = _format_info(_make_info(status_flag=STATUS_NAT))
        assert "Client TLS: No" in text
        assert "Client NAT: Yes" in text

    def test_both_flags(self):
        text = _format_info(_make_info(status_flag=STATUS_TLS | STATUS_NAT))
        assert "Client TLS: Yes" in text
        assert "Client NAT: Yes" in text

    def test_no_flags(self):
        text = _format_info(_make_info(status_flag=0))
        assert "Client TLS: No" in text
        assert "Client NAT: No" in text

    def test_hub_tls_always_no(self):
        text = _format_info(_make_info())
        assert "Hub TLS: No" in text

    def test_lines_joined_with_crlf(self):
        text = _format_info(_make_info())
        assert "\r\n" in text
        # No bare \n without preceding \r
        stripped = text.replace("\r\n", "")
        assert "\n" not in stripped


# ===================================================================
# on_user_connect — delivery mode
# ===================================================================

class TestOnUserConnectDelivery:
    """Test that main-chat vs PM delivery is driven by config."""

    def test_default_sends_to_main_chat(self):
        """Default (user_info_as_pm=0) sends via send_to_user (main chat)."""
        ctx = _make_ctx()
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.send_to_user.assert_called_once()
        ctx.send_pm_as.assert_not_called()

    def test_main_chat_format(self):
        """Main-chat message is formatted as <Bot> text."""
        ctx = _make_ctx()
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        raw = ctx.send_to_user.call_args[0][1]
        assert raw.startswith("<Hub-Security> ")
        assert "Your information:" in raw

    def test_pm_mode_when_truthy(self):
        """user_info_as_pm=1 sends via send_pm_as."""
        ctx = _make_ctx(config={"user_info_as_pm": "1"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.send_pm_as.assert_called_once()
        ctx.send_to_user.assert_not_called()

    def test_pm_mode_true_string(self):
        ctx = _make_ctx(config={"user_info_as_pm": "true"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.send_pm_as.assert_called_once()

    def test_pm_mode_yes_string(self):
        ctx = _make_ctx(config={"user_info_as_pm": "yes"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.send_pm_as.assert_called_once()

    def test_pm_mode_false_string(self):
        ctx = _make_ctx(config={"user_info_as_pm": "false"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.send_to_user.assert_called_once()
        ctx.send_pm_as.assert_not_called()

    def test_pm_from_bot_nick(self):
        """PM is sent from the configured hub_security bot nick."""
        ctx = _make_ctx(config={"user_info_as_pm": "1", "hub_security": "MyBot"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        args = ctx.send_pm_as.call_args[0]
        assert args[0] == "MyBot"
        assert args[1] == "TestUser"
        assert "Your information:" in args[2]

    def test_chat_from_bot_nick(self):
        """Main-chat message uses the configured bot nick."""
        ctx = _make_ctx(config={"hub_security": "MyBot"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        raw = ctx.send_to_user.call_args[0][1]
        assert raw.startswith("<MyBot> ")


# ===================================================================
# on_user_connect — enable / disable
# ===================================================================

class TestOnUserConnectEnable:
    """Test the send_user_info on/off switch."""

    def test_disabled_zero(self):
        ctx = _make_ctx(config={"send_user_info": "0"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.get_user_info.assert_not_called()
        ctx.send_to_user.assert_not_called()
        ctx.send_pm_as.assert_not_called()

    def test_disabled_false(self):
        ctx = _make_ctx(config={"send_user_info": "false"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.get_user_info.assert_not_called()

    def test_disabled_no(self):
        ctx = _make_ctx(config={"send_user_info": "no"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.get_user_info.assert_not_called()

    def test_enabled_one(self):
        ctx = _make_ctx(config={"send_user_info": "1"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.get_user_info.assert_called_once()

    def test_enabled_true(self):
        ctx = _make_ctx(config={"send_user_info": "true"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.get_user_info.assert_called_once()

    def test_enabled_yes(self):
        ctx = _make_ctx(config={"send_user_info": "yes"})
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.get_user_info.assert_called_once()


# ===================================================================
# on_user_connect — edge cases
# ===================================================================

class TestOnUserConnectEdgeCases:
    """Edge cases and error handling."""

    def test_user_info_none_skips_send(self):
        ctx = _make_ctx(user_info=None)
        ctx.get_user_info.return_value = None
        on_user_connect(ctx, "Ghost", "10.0.0.1")
        ctx.send_to_user.assert_not_called()
        ctx.send_pm_as.assert_not_called()

    def test_user_info_none_logs_warning(self, caplog):
        ctx = _make_ctx()
        ctx.get_user_info.return_value = None
        with caplog.at_level(logging.WARNING, logger="verlihub.user_info"):
            on_user_connect(ctx, "Ghost", "10.0.0.1")
        assert "get_user_info(Ghost) returned None" in caplog.text

    def test_exception_logged_as_warning(self, caplog):
        ctx = _make_ctx()
        ctx.get_user_info.side_effect = RuntimeError("boom")
        with caplog.at_level(logging.WARNING, logger="verlihub.user_info"):
            on_user_connect(ctx, "Crasher", "10.0.0.1")
        assert "Failed to send user info to Crasher" in caplog.text
        assert "boom" in caplog.text

    def test_exception_does_not_propagate(self):
        ctx = _make_ctx()
        ctx.get_user_info.side_effect = RuntimeError("boom")
        # Must not raise
        on_user_connect(ctx, "Crasher", "10.0.0.1")

    def test_send_failure_still_succeeds(self):
        """send_to_user returning False doesn't cause an exception."""
        ctx = _make_ctx(send_to_user_return=False)
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        ctx.send_to_user.assert_called_once()

    def test_correct_nick_passed_to_get_user_info(self):
        ctx = _make_ctx()
        on_user_connect(ctx, "SpecialNick", "1.2.3.4")
        ctx.get_user_info.assert_called_once_with("SpecialNick")


# ===================================================================
# register()
# ===================================================================

class TestRegister:
    """Test handler registration on the event bus."""

    def test_registers_user_connect_event(self):
        ctx = _make_ctx()
        register(ctx)
        ctx.events.register.assert_called_once()
        args = ctx.events.register.call_args[0]
        assert args[0] == "user_connect"

    def test_registered_handler_calls_on_user_connect(self):
        """The closure passed to events.register dispatches correctly."""
        ctx = _make_ctx()
        register(ctx)
        handler = ctx.events.register.call_args[0][1]
        # Call it like the event bus would
        handler("SomeNick", "9.8.7.6")
        ctx.get_user_info.assert_called_once_with("SomeNick")

    def test_register_logs_info(self, caplog):
        ctx = _make_ctx()
        with caplog.at_level(logging.INFO, logger="verlihub.user_info"):
            register(ctx)
        assert "User-info on-connect handler registered" in caplog.text


# ===================================================================
# Integration: full message content
# ===================================================================

class TestMessageContent:
    """Verify the full message sent to the user matches expected format."""

    def test_main_chat_full_message(self):
        info = _make_info(
            nick="joe",
            ip="192.168.1.1",
            country="US",
            country_name="United States",
            city="Austin",
            status_flag=STATUS_TLS,
        )
        ctx = _make_ctx(user_info=info)
        on_user_connect(ctx, "joe", "192.168.1.1")
        raw = ctx.send_to_user.call_args[0][1]
        # Starts with the bot prefix
        assert raw.startswith("<Hub-Security> Your information:")
        assert "Nick: joe" in raw
        assert "IP: 192.168.1.1" in raw
        assert "Country: US=United States" in raw
        assert "City: Austin" in raw
        assert "Client TLS: Yes" in raw
        assert "Client NAT: No" in raw
        assert "Hub TLS: No" in raw

    def test_pm_full_message(self):
        info = _make_info(nick="joe", status_flag=STATUS_TLS | STATUS_NAT)
        ctx = _make_ctx(config={"user_info_as_pm": "1"}, user_info=info)
        on_user_connect(ctx, "joe", "10.0.0.1")
        args = ctx.send_pm_as.call_args[0]
        assert args[0] == "Hub-Security"  # from
        assert args[1] == "joe"           # to
        msg = args[2]
        assert "Client TLS: Yes" in msg
        assert "Client NAT: Yes" in msg

    def test_minimal_info_no_country_no_city(self):
        info = _make_info(country="", country_name="", city="")
        ctx = _make_ctx(user_info=info)
        on_user_connect(ctx, "TestUser", "10.0.0.1")
        raw = ctx.send_to_user.call_args[0][1]
        assert "Country:" not in raw
        assert "City:" not in raw
        # But still has the basics
        assert "Nick: TestUser" in raw
        assert "IP: 10.0.0.1" in raw
