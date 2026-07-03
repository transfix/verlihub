"""
Extended tests for verlihub CLI commands.

Covers: cmd_login, cmd_ban, text format outputs, connection errors,
error status codes (404/401), and verbose flags.
"""
import json
import pytest
from argparse import Namespace
from unittest.mock import patch, MagicMock, PropertyMock

import httpx

from verlihub.cli import (
    cmd_login,
    cmd_status,
    cmd_users,
    cmd_kick,
    cmd_ban,
    cmd_broadcast,
    cmd_command,
    cmd_config,
    get_client,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ns(**kw):
    """Build an argparse.Namespace with sensible defaults."""
    defaults = dict(api_url=None, token=None, verbose=False, format="text", command=None)
    defaults.update(kw)
    return Namespace(**defaults)


def _mock_client(method="get", status=200, json_data=None, text=""):
    """Return a mock httpx.Client context manager."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    resp.text = text

    client = MagicMock()
    setattr(client, method, MagicMock(return_value=resp))
    # Also set the other method so we don't error when the wrong one is called
    if method != "get":
        client.get = MagicMock(return_value=resp)
    if method != "post":
        client.post = MagicMock(return_value=resp)
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


def _mock_client_error(method="get"):
    """Return a mock client that raises httpx.RequestError."""
    client = MagicMock()
    err = httpx.RequestError("Connection refused")
    getattr(client, method).side_effect = err
    if method != "get":
        client.get.side_effect = err
    if method != "post":
        client.post.side_effect = err
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


# ===================================================================
# cmd_login
# ===================================================================

class TestCmdLogin:
    """Tests for the login command."""

    @patch("verlihub.cli.save_config")
    @patch("verlihub.cli.load_config", return_value={})
    @patch("verlihub.cli.get_client")
    def test_login_success(self, mock_gc, _lc, _sc):
        mock_gc.return_value = _mock_client("post", 200, {"access_token": "tok123"})
        ns = _make_ns(command="login", username="admin", password="secret")
        assert cmd_login(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_login_failure(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 401, text="bad creds")
        ns = _make_ns(command="login", username="admin", password="wrong")
        assert cmd_login(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_login_connection_error(self, mock_gc):
        mock_gc.return_value = _mock_client_error("post")
        ns = _make_ns(command="login", username="admin", password="pw")
        assert cmd_login(ns) == 1

    @patch("verlihub.cli.save_config")
    @patch("verlihub.cli.load_config", return_value={"api_url": "http://saved"})
    @patch("verlihub.cli.get_client")
    def test_login_saves_token(self, mock_gc, mock_lc, mock_sc):
        mock_gc.return_value = _mock_client("post", 200, {"access_token": "xyz"})
        ns = _make_ns(command="login", username="admin", password="pw")
        cmd_login(ns)
        mock_sc.assert_called_once()
        saved = mock_sc.call_args[0][0]
        assert saved["token"] == "xyz"


# ===================================================================
# cmd_status — text formatting branches
# ===================================================================

class TestCmdStatusText:

    @patch("verlihub.cli.get_client")
    def test_status_text_no_uptime(self, mock_gc):
        """Text output when uptime key is absent."""
        mock_gc.return_value = _mock_client("get", 200, {
            "running": False,
            "hub_name": "TestHub",
            "user_count": 0,
            "share_total": 0,
        })
        ns = _make_ns(command="status", format="text")
        assert cmd_status(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_status_json_format(self, mock_gc):
        mock_gc.return_value = _mock_client("get", 200, {
            "running": True,
            "hub_name": "Hub",
            "user_count": 5,
            "share_total": 1024,
            "uptime": 60,
        })
        ns = _make_ns(command="status", format="json")
        assert cmd_status(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_status_generic_error(self, mock_gc):
        mock_gc.return_value = _mock_client("get", 500, text="Server Error")
        ns = _make_ns(command="status")
        assert cmd_status(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_status_connection_error(self, mock_gc):
        mock_gc.return_value = _mock_client_error("get")
        ns = _make_ns(command="status")
        assert cmd_status(ns) == 1


# ===================================================================
# cmd_users — text format, empty, errors
# ===================================================================

class TestCmdUsersExtended:

    @patch("verlihub.cli.get_client")
    def test_users_text_empty(self, mock_gc):
        mock_gc.return_value = _mock_client("get", 200, [])
        ns = _make_ns(command="users", format="text")
        assert cmd_users(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_users_text_with_data(self, mock_gc):
        mock_gc.return_value = _mock_client("get", 200, [
            {"nick": "Alice", "class": 3, "share": 1024 * 1024, "ip": "10.0.0.1"},
            {"nick": "Bob", "class": 1, "share": 512, "ip": "10.0.0.2"},
        ])
        ns = _make_ns(command="users", format="text")
        assert cmd_users(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_users_401(self, mock_gc):
        mock_gc.return_value = _mock_client("get", 401)
        ns = _make_ns(command="users")
        assert cmd_users(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_users_generic_error(self, mock_gc):
        mock_gc.return_value = _mock_client("get", 500, text="err")
        ns = _make_ns(command="users")
        assert cmd_users(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_users_connection_error(self, mock_gc):
        mock_gc.return_value = _mock_client_error("get")
        ns = _make_ns(command="users")
        assert cmd_users(ns) == 1


# ===================================================================
# cmd_kick — additional status codes
# ===================================================================

class TestCmdKickExtended:

    @patch("verlihub.cli.get_client")
    def test_kick_success_no_reason(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 200)
        ns = _make_ns(command="kick", nick="baduser", reason=None)
        assert cmd_kick(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_kick_success_with_reason(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 200)
        ns = _make_ns(command="kick", nick="baduser", reason="spam")
        assert cmd_kick(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_kick_not_found(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 404)
        ns = _make_ns(command="kick", nick="ghost", reason=None)
        assert cmd_kick(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_kick_401(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 401)
        ns = _make_ns(command="kick", nick="user", reason=None)
        assert cmd_kick(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_kick_generic_error(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 500, text="err")
        ns = _make_ns(command="kick", nick="user", reason=None)
        assert cmd_kick(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_kick_connection_error(self, mock_gc):
        mock_gc.return_value = _mock_client_error("post")
        ns = _make_ns(command="kick", nick="user", reason=None)
        assert cmd_kick(ns) == 1


# ===================================================================
# cmd_ban — full coverage
# ===================================================================

class TestCmdBan:

    @patch("verlihub.cli.get_client")
    def test_ban_success_nick(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 201)
        ns = _make_ns(command="ban", nick="badnick", ip=None, reason="spam", duration="1d")
        assert cmd_ban(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_ban_success_ip(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 200)
        ns = _make_ns(command="ban", nick=None, ip="10.0.0.1", reason=None, duration=None)
        assert cmd_ban(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_ban_401(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 401)
        ns = _make_ns(command="ban", nick="x", ip=None, reason=None, duration="1d")
        assert cmd_ban(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_ban_generic_error(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 500, text="err")
        ns = _make_ns(command="ban", nick="x", ip=None, reason=None, duration="1d")
        assert cmd_ban(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_ban_connection_error(self, mock_gc):
        mock_gc.return_value = _mock_client_error("post")
        ns = _make_ns(command="ban", nick="x", ip=None, reason=None, duration="1d")
        assert cmd_ban(ns) == 1


# ===================================================================
# cmd_broadcast — additional paths
# ===================================================================

class TestCmdBroadcastExtended:

    @patch("verlihub.cli.get_client")
    def test_broadcast_401(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 401)
        ns = _make_ns(command="broadcast", message="hi")
        assert cmd_broadcast(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_broadcast_generic_error(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 500, text="err")
        ns = _make_ns(command="broadcast", message="hi")
        assert cmd_broadcast(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_broadcast_connection_error(self, mock_gc):
        mock_gc.return_value = _mock_client_error("post")
        ns = _make_ns(command="broadcast", message="hi")
        assert cmd_broadcast(ns) == 1


# ===================================================================
# cmd_command — verbose, failures, errors
# ===================================================================

class TestCmdCommandExtended:

    @patch("verlihub.cli.get_client")
    def test_command_success_verbose(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 200, {
            "success": True, "output": "result", "message": "OK"
        })
        ns = _make_ns(command="command", hub_command="!help", verbose=True)
        assert cmd_command(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_command_failure(self, mock_gc):
        """success=False in response → exit code 1."""
        mock_gc.return_value = _mock_client("post", 200, {
            "success": False, "output": "", "message": "failed"
        })
        ns = _make_ns(command="command", hub_command="!bad")
        assert cmd_command(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_command_no_output(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 200, {
            "success": True, "output": "", "message": ""
        })
        ns = _make_ns(command="command", hub_command="!noop")
        assert cmd_command(ns) == 0

    @patch("verlihub.cli.get_client")
    def test_command_401(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 401)
        ns = _make_ns(command="command", hub_command="!x")
        assert cmd_command(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_command_generic_error(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 500, text="err")
        ns = _make_ns(command="command", hub_command="!x")
        assert cmd_command(ns) == 1

    @patch("verlihub.cli.get_client")
    def test_command_connection_error(self, mock_gc):
        mock_gc.return_value = _mock_client_error("post")
        ns = _make_ns(command="command", hub_command="!x")
        assert cmd_command(ns) == 1


# ===================================================================
# cmd_config — edge cases
# ===================================================================

class TestCmdConfigExtended:

    def test_config_no_action(self, tmp_path):
        """config with no flags does nothing and returns 0."""
        with patch("verlihub.cli.CONFIG_FILE", tmp_path / "cfg.json"):
            ns = _make_ns(command="config", show=False, set_url=None, clear=False)
            assert cmd_config(ns) == 0


# ===================================================================
# get_client — token from config vs args
# ===================================================================

class TestGetClient:

    @patch("verlihub.cli.load_config", return_value={"token": "saved_tok", "api_url": "http://saved"})
    def test_client_uses_config_token(self, _lc):
        ns = _make_ns(token=None, api_url=None)
        with get_client(ns) as c:
            assert c.headers.get("authorization") == "Bearer saved_tok"

    @patch("verlihub.cli.load_config", return_value={"token": "saved"})
    def test_client_arg_token_overrides(self, _lc):
        ns = _make_ns(token="arg_tok", api_url=None)
        with get_client(ns) as c:
            assert c.headers.get("authorization") == "Bearer arg_tok"

    @patch("verlihub.cli.load_config", return_value={})
    def test_client_no_token(self, _lc):
        ns = _make_ns(token=None, api_url=None)
        with get_client(ns) as c:
            assert "authorization" not in c.headers

    @patch("verlihub.cli.load_config", return_value={})
    def test_client_custom_api_url(self, _lc):
        ns = _make_ns(token=None, api_url="http://custom:9000")
        with get_client(ns) as c:
            assert str(c.base_url) == "http://custom:9000"


# ===================================================================
# main() dispatch — aliases, unknown
# ===================================================================

class TestMainDispatch:

    @patch("verlihub.cli.get_client")
    def test_main_cmd_alias(self, mock_gc):
        """'cmd' is an alias for 'command'."""
        mock_gc.return_value = _mock_client("post", 200, {"success": True, "output": ""})
        with patch("sys.argv", ["verlihub-cli", "cmd", "!help"]):
            assert main() == 0

    @patch("verlihub.cli.get_client")
    def test_main_exec_alias(self, mock_gc):
        """'exec' is an alias for 'command'."""
        mock_gc.return_value = _mock_client("post", 200, {"success": True, "output": ""})
        with patch("sys.argv", ["verlihub-cli", "exec", "!help"]):
            assert main() == 0

    @patch("verlihub.cli.get_client")
    def test_main_login_via_main(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 401, text="fail")
        with patch("sys.argv", ["verlihub-cli", "login", "user", "pass"]):
            assert main() == 1

    @patch("verlihub.cli.get_client")
    def test_main_ban_via_main(self, mock_gc):
        mock_gc.return_value = _mock_client("post", 201)
        with patch("sys.argv", ["verlihub-cli", "ban", "--nick", "bad", "--reason", "spam"]):
            assert main() == 0
