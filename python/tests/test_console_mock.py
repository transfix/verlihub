"""
Tests for verlihub.api.routes.console — command mock responses.

Covers: _get_mock_response() all branches, COMMAND_REFERENCE data.
"""
from __future__ import annotations

import pytest

from verlihub.api.routes.console import _get_mock_response, COMMAND_REFERENCE


# ======================================================================
# _get_mock_response  — all command branches
# ======================================================================


class TestGetMockResponse:

    def test_help(self):
        resp = _get_mock_response("!help")
        assert "Available commands" in resp
        assert "!hubinfo" in resp

    def test_help_with_arg(self):
        resp = _get_mock_response("!help kick")
        assert "Available commands" in resp

    def test_hubinfo(self):
        resp = _get_mock_response("!hubinfo")
        assert "Hub Information" in resp
        assert "Verlihub" in resp

    def test_ul(self):
        resp = _get_mock_response("!ul")
        assert "dashboard" in resp.lower()

    def test_ul_with_class(self):
        resp = _get_mock_response("!ul 5")
        assert "dashboard" in resp.lower()

    def test_reglist(self):
        resp = _get_mock_response("!reglist")
        assert "dashboard" in resp.lower()

    def test_banlist(self):
        resp = _get_mock_response("!banlist")
        assert "dashboard" in resp.lower()

    def test_lstplug(self):
        resp = _get_mock_response("!lstplug")
        assert "plugin" in resp.lower()

    def test_mc_broadcast(self):
        resp = _get_mock_response("!mc Hello everyone!")
        assert "Broadcast sent" in resp
        assert "Hello everyone!" in resp

    def test_topic(self):
        resp = _get_mock_response("!topic Welcome to the hub!")
        assert "Topic set to" in resp
        assert "Welcome to the hub!" in resp

    def test_reguser_with_args(self):
        resp = _get_mock_response("+reguser TestUser 3")
        assert "TestUser" in resp
        assert "registered" in resp
        assert "class 3" in resp

    def test_reguser_missing_args(self):
        resp = _get_mock_response("+reguser")
        # Without a trailing space, "+reguser" doesn't match startswith("+reguser "),
        # so it falls through to the unknown command branch
        assert "Unknown command" in resp or "Usage" in resp

    def test_unreguser_with_arg(self):
        resp = _get_mock_response("-reguser BadUser")
        assert "BadUser" in resp
        assert "unregistered" in resp

    def test_unreguser_missing_arg(self):
        resp = _get_mock_response("-reguser")
        assert "Unknown command" in resp or "Usage" in resp

    def test_kick_with_nick(self):
        resp = _get_mock_response("!kick TrollUser")
        assert "TrollUser" in resp
        assert "kicked" in resp

    def test_kick_missing_nick(self):
        resp = _get_mock_response("!kick")
        assert "Unknown command" in resp or "Usage" in resp

    def test_ban_with_args(self):
        resp = _get_mock_response("!ban TrollUser 24h spam")
        assert "TrollUser" in resp
        assert "banned" in resp
        assert "24h" in resp

    def test_ban_missing_args(self):
        resp = _get_mock_response("!ban TrollUser")
        assert "Usage" in resp

    def test_unknown_command(self):
        resp = _get_mock_response("!doesnotexist")
        assert "Unknown command" in resp
        assert "!help" in resp

    def test_case_insensitive(self):
        resp = _get_mock_response("!HUBINFO")
        assert "Hub Information" in resp


# ======================================================================
# COMMAND_REFERENCE data integrity
# ======================================================================


class TestCommandReference:

    def test_not_empty(self):
        assert len(COMMAND_REFERENCE) > 0

    def test_all_have_required_fields(self):
        for cmd in COMMAND_REFERENCE:
            assert cmd.name
            assert cmd.description
            assert cmd.usage
            assert isinstance(cmd.min_class, int)

    def test_contains_essential_commands(self):
        names = {cmd.name for cmd in COMMAND_REFERENCE}
        assert "!help" in names
        assert "!hubinfo" in names
        assert "!kick" in names
        assert "!ban" in names
        assert "+reguser" in names
