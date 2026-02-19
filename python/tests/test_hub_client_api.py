"""
Comprehensive tests for verlihub.client.api module.

Tests HubClient and AsyncHubClient with mocked HTTP responses to achieve
high code coverage without requiring a running hub.
"""
import asyncio
import pytest
from unittest import mock
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from verlihub.client.api import (
    HubClient,
    AsyncHubClient,
    HubClientConfig,
    HubClientError,
    AuthenticationError,
    PermissionError as ClientPermissionError,
    APIError,
)


# =============================================================================
# Helpers
# =============================================================================


def _json_response(data: Any, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx JSON response."""
    import json
    content = json.dumps(data).encode()
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://test"),
    )


def _text_response(text: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx text response."""
    return httpx.Response(
        status_code=status_code,
        content=text.encode(),
        headers={"content-type": "text/plain"},
        request=httpx.Request("GET", "http://test"),
    )


def _error_response(status_code: int, text: str = "error") -> httpx.Response:
    """Create a mock httpx error response."""
    return httpx.Response(
        status_code=status_code,
        content=text.encode(),
        headers={"content-type": "text/plain"},
        request=httpx.Request("GET", "http://test"),
    )


# =============================================================================
# HubClientConfig Tests
# =============================================================================


class TestHubClientConfig:
    """Tests for the HubClientConfig dataclass."""

    def test_default_values(self):
        config = HubClientConfig(base_url="http://localhost:8000/api/v1")
        assert config.base_url == "http://localhost:8000/api/v1"
        assert config.timeout == 30.0
        assert config.verify_ssl is True
        assert config.max_retries == 3
        assert config.retry_delay == 1.0

    def test_custom_values(self):
        config = HubClientConfig(
            base_url="https://hub.example.com",
            timeout=60.0,
            verify_ssl=False,
            max_retries=5,
            retry_delay=2.0,
        )
        assert config.timeout == 60.0
        assert config.verify_ssl is False
        assert config.max_retries == 5
        assert config.retry_delay == 2.0


# =============================================================================
# APIError Tests
# =============================================================================


class TestAPIError:
    """Tests for the APIError exception."""

    def test_basic(self):
        err = APIError("something failed")
        assert str(err) == "something failed"
        assert err.status_code == 0
        assert err.response == ""

    def test_with_details(self):
        err = APIError("not found", status_code=404, response='{"detail":"not found"}')
        assert err.status_code == 404
        assert err.response == '{"detail":"not found"}'
        assert isinstance(err, HubClientError)


# =============================================================================
# HubClient — Authentication
# =============================================================================


class TestHubClientLogin:
    """Tests for HubClient.login() with mocked HTTP."""

    def test_login_success(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({
                "access_token": "jwt_token_123",
                "expires_in": 3600,
                "user_class": 5,
            })
            with mock.patch.object(client._client, "post", return_value=mock_resp):
                result = client.login("admin", "password")

            assert result is True
            assert client.is_authenticated
            assert client.user_class == 5
            assert client._token == "jwt_token_123"

    def test_login_sets_token_expiry(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({
                "access_token": "tok",
                "expires_in": 7200,
            })
            before = datetime.now(timezone.utc)
            with mock.patch.object(client._client, "post", return_value=mock_resp):
                client.login("user", "pass")
            after = datetime.now(timezone.utc)

            assert client._token_expires is not None
            assert client._token_expires >= before + timedelta(seconds=7199)
            assert client._token_expires <= after + timedelta(seconds=7201)

    def test_login_default_expires_in(self):
        """Test login uses default 3600s when expires_in not in response."""
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"access_token": "tok"})
            with mock.patch.object(client._client, "post", return_value=mock_resp):
                client.login("user", "pass")
            assert client._token_expires is not None

    def test_login_invalid_credentials(self):
        with HubClient("http://test/api/v1") as client:
            resp = _error_response(401, "Unauthorized")
            with mock.patch.object(
                client._client, "post",
                side_effect=httpx.HTTPStatusError(
                    "401", request=httpx.Request("POST", "/auth/login"), response=resp
                ),
            ):
                with pytest.raises(AuthenticationError, match="Invalid credentials"):
                    client.login("bad", "creds")

    def test_login_server_error(self):
        with HubClient("http://test/api/v1") as client:
            resp = _error_response(500, "Internal Server Error")
            with mock.patch.object(
                client._client, "post",
                side_effect=httpx.HTTPStatusError(
                    "500", request=httpx.Request("POST", "/auth/login"), response=resp
                ),
            ):
                with pytest.raises(HubClientError, match="Login failed"):
                    client.login("user", "pass")


class TestHubClientIsAuthenticated:
    """Tests for is_authenticated property edge cases."""

    def test_expired_token(self):
        with HubClient("http://test/api/v1") as client:
            client._token = "expired_tok"
            client._token_expires = datetime.now(timezone.utc) - timedelta(hours=1)
            assert not client.is_authenticated

    def test_no_expiry_set(self):
        with HubClient("http://test/api/v1") as client:
            client._token = "tok"
            client._token_expires = None
            # No expiry means it's valid
            assert client.is_authenticated


# =============================================================================
# HubClient — _request() method
# =============================================================================


class TestHubClientRequest:
    """Tests for HubClient._request() internal method."""

    def test_json_response(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"key": "value"})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                result = client._request("GET", "/test")
            assert result == {"key": "value"}

    def test_text_response(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _text_response("plain text")
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                result = client._request("GET", "/test")
            assert result == "plain text"

    def test_401_raises_auth_error(self):
        with HubClient("http://test/api/v1") as client:
            resp = _error_response(401)
            with mock.patch.object(
                client._client, "request",
                side_effect=httpx.HTTPStatusError(
                    "401", request=httpx.Request("GET", "/"), response=resp
                ),
            ):
                with pytest.raises(AuthenticationError):
                    client._request("GET", "/test")

    def test_403_raises_permission_error(self):
        with HubClient("http://test/api/v1") as client:
            resp = _error_response(403)
            with mock.patch.object(
                client._client, "request",
                side_effect=httpx.HTTPStatusError(
                    "403", request=httpx.Request("GET", "/"), response=resp
                ),
            ):
                with pytest.raises(ClientPermissionError):
                    client._request("GET", "/test")

    def test_other_http_error_raises_api_error(self):
        with HubClient("http://test/api/v1") as client:
            resp = _error_response(500, "Internal Server Error")
            with mock.patch.object(
                client._client, "request",
                side_effect=httpx.HTTPStatusError(
                    "500", request=httpx.Request("GET", "/"), response=resp
                ),
            ):
                with pytest.raises(APIError) as exc_info:
                    client._request("GET", "/test")
                assert exc_info.value.status_code == 500

    def test_includes_auth_headers(self):
        with HubClient("http://test/api/v1") as client:
            client._token = "my_token"
            client._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)

            mock_resp = _json_response({"ok": True})
            with mock.patch.object(client._client, "request", return_value=mock_resp) as mock_req:
                client._request("GET", "/test")

            call_kwargs = mock_req.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert headers.get("Authorization") == "Bearer my_token"

    def test_merges_extra_headers(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"ok": True})
            with mock.patch.object(client._client, "request", return_value=mock_resp) as mock_req:
                client._request("GET", "/test", headers={"X-Custom": "val"})

            call_kwargs = mock_req.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert headers.get("X-Custom") == "val"


# =============================================================================
# HubClient — Hub Lifecycle Methods
# =============================================================================


class TestHubClientLifecycle:
    """Tests for hub lifecycle methods (start, stop, restart, is_running, reload)."""

    def test_start_success(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"status": "started"})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                assert client.start(port=4111) is True

    def test_start_failure(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"status": "failed"})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                assert client.start() is False

    def test_stop_success(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"status": "stopped"})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                assert client.stop() is True

    def test_restart_calls_stop_then_start(self):
        with HubClient("http://test/api/v1") as client:
            responses = [
                _json_response({"status": "stopped"}),
                _json_response({"status": "started"}),
            ]
            with mock.patch.object(client._client, "request", side_effect=responses):
                assert client.restart(port=4111) is True

    def test_is_running_true(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"is_running": True})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                assert client.is_running is True

    def test_is_running_false_on_error(self):
        with HubClient("http://test/api/v1") as client:
            resp = _error_response(503)
            with mock.patch.object(
                client._client, "request",
                side_effect=httpx.HTTPStatusError(
                    "503", request=httpx.Request("GET", "/"), response=resp
                ),
            ):
                assert client.is_running is False

    def test_reload_config(self):
        with HubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"status": "ok"})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                assert client.reload_config() is True


# =============================================================================
# HubClient — Hub Info Methods
# =============================================================================


class TestHubClientInfo:
    """Tests for hub info methods."""

    def test_get_hub_info(self):
        with HubClient("http://test/api/v1") as client:
            data = {"hub_name": "Test Hub", "topic": "Welcome"}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.get_hub_info()
            assert result["hub_name"] == "Test Hub"

    def test_get_hub_name(self):
        with HubClient("http://test/api/v1") as client:
            data = {"hub_name": "My Hub", "topic": "Hi"}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                assert client.get_hub_name() == "My Hub"

    def test_get_hub_name_missing(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(client._client, "request", return_value=_json_response({})):
                assert client.get_hub_name() == ""

    def test_get_hub_topic(self):
        with HubClient("http://test/api/v1") as client:
            data = {"hub_name": "Hub", "topic": "DC++"}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                assert client.get_hub_topic() == "DC++"

    def test_set_hub_topic(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(client._client, "request", return_value=_json_response({"status": "ok"})):
                assert client.set_hub_topic("New Topic") is True

    def test_get_total_share(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"total_share": 1073741824}),
            ):
                assert client.get_total_share() == 1073741824

    def test_get_hub_stats(self):
        with HubClient("http://test/api/v1") as client:
            data = {"user_count": 42, "total_share": 0}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.get_hub_stats()
            assert result["user_count"] == 42


# =============================================================================
# HubClient — Statistics / Monitoring Methods
# =============================================================================


class TestHubClientStatistics:
    """Tests for statistics & monitoring methods."""

    def test_get_statistics(self):
        with HubClient("http://test/api/v1") as client:
            data = {"users_online": 10, "total_share": 5000}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.get_statistics()
            assert result["users_online"] == 10

    def test_get_geo_distribution(self):
        with HubClient("http://test/api/v1") as client:
            data = {"total_countries": 3, "distribution": []}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.get_geo_distribution()
            assert result["total_countries"] == 3

    def test_get_share_stats(self):
        with HubClient("http://test/api/v1") as client:
            data = {"total": 1000, "average": 500, "median": 400, "max": 900, "min": 100}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.get_share_stats()
            assert result["total"] == 1000

    def test_get_operators(self):
        with HubClient("http://test/api/v1") as client:
            data = [{"nick": "Admin", "user_class": 5}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.get_operators()
            assert len(result) == 1
            assert result[0]["nick"] == "Admin"

    def test_get_bots(self):
        with HubClient("http://test/api/v1") as client:
            data = [{"nick": "HubBot", "description": "test"}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.get_bots()
            assert result[0]["nick"] == "HubBot"

    def test_get_detailed_users_with_params(self):
        with HubClient("http://test/api/v1") as client:
            data = [{"nick": "User1", "is_clone": False}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)) as m:
                result = client.get_detailed_users(limit=10, offset=5)
            assert len(result) == 1
            # Verify params were passed
            call_kwargs = m.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert params["limit"] == 10
            assert params["offset"] == 5

    def test_get_detailed_users_no_params(self):
        with HubClient("http://test/api/v1") as client:
            data = []
            with mock.patch.object(client._client, "request", return_value=_json_response(data)) as m:
                result = client.get_detailed_users()
            assert result == []
            # params should be None when no limit/offset
            call_kwargs = m.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert params is None

    def test_health_check(self):
        with HubClient("http://test/api/v1") as client:
            data = {"status": "healthy", "hub_running": True}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.health_check()
            assert result["status"] == "healthy"


# =============================================================================
# HubClient — User Operations
# =============================================================================


class TestHubClientUserOps:
    """Tests for user operation methods."""

    def test_get_user_count(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"user_count": 42}),
            ):
                assert client.get_user_count() == 42

    def test_get_user_list(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"users": ["Alice", "Bob"]}),
            ):
                result = client.get_user_list()
            assert result == ["Alice", "Bob"]

    def test_get_user_info(self):
        with HubClient("http://test/api/v1") as client:
            data = {"nick": "Alice", "share": 1024}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.get_user_info("Alice")
            assert result["nick"] == "Alice"

    def test_kick_user(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "kicked"}),
            ):
                assert client.kick_user("Admin", "Spammer", "Flooding") is True

    def test_kick_user_failure(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "not_found"}),
            ):
                assert client.kick_user("Admin", "Ghost", "N/A") is False

    def test_drop_user(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "dropped"}),
            ):
                assert client.drop_user("BadUser") is True

    def test_redirect_user(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "redirected"}),
            ):
                assert client.redirect_user("User1", "dchub://other.hub", "Moved") is True


# =============================================================================
# HubClient — Messaging
# =============================================================================


class TestHubClientMessaging:
    """Tests for messaging methods."""

    def test_send_to_user(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "sent"}),
            ):
                assert client.send_to_user("Alice", "Hello!") is True

    def test_send_to_all(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "ok"}),
            ):
                assert client.send_to_all("Announcement") is True

    def test_send_to_class(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "sent"}),
            ):
                assert client.send_to_class("VIP message", 2, 5) is True


# =============================================================================
# HubClient — Registered Users
# =============================================================================


class TestHubClientRegisteredUsers:
    """Tests for registered user methods."""

    def test_get_registered_users(self):
        with HubClient("http://test/api/v1") as client:
            data = [{"nick": "User1"}, {"nick": "User2"}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)) as m:
                result = client.get_registered_users(limit=50, offset=10)
            assert len(result) == 2
            call_kwargs = m.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert params["limit"] == 50
            assert params["offset"] == 10

    def test_get_registered_users_with_class_filter(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(client._client, "request", return_value=_json_response([])) as m:
                client.get_registered_users(class_filter=3)
            call_kwargs = m.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert params["class"] == 3

    def test_register_user(self):
        with HubClient("http://test/api/v1") as client:
            data = {"nick": "NewUser", "user_class": 1}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.register_user("NewUser", "pass123", user_class=1)
            assert result["nick"] == "NewUser"

    def test_delete_registration(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "deleted"}),
            ):
                assert client.delete_registration("OldUser") is True

    def test_update_user(self):
        with HubClient("http://test/api/v1") as client:
            data = {"nick": "User1", "user_class": 3}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.update_user("User1", user_class=3)
            assert result["user_class"] == 3


# =============================================================================
# HubClient — Ban Management
# =============================================================================


class TestHubClientBans:
    """Tests for ban management methods."""

    def test_get_bans(self):
        with HubClient("http://test/api/v1") as client:
            data = [{"id": 1, "nick": "BadUser"}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)) as m:
                result = client.get_bans(limit=50, offset=0)
            assert len(result) == 1
            call_kwargs = m.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert params["limit"] == 50

    def test_ban_user(self):
        with HubClient("http://test/api/v1") as client:
            data = {"id": 1, "nick": "Spammer"}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = client.ban_user("Spammer", "Flooding", duration_hours=24, ban_ip=True)
            assert result["nick"] == "Spammer"

    def test_unban(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "removed"}),
            ):
                assert client.unban(1) is True

    def test_unban_failure(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "not_found"}),
            ):
                assert client.unban(999) is False


# =============================================================================
# HubClient — Configuration
# =============================================================================


class TestHubClientConfig:
    """Tests for configuration methods."""

    def test_get_config(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"value": "411"}),
            ):
                assert client.get_config("config", "port") == "411"

    def test_get_config_default_on_error(self):
        with HubClient("http://test/api/v1") as client:
            resp = _error_response(404)
            with mock.patch.object(
                client._client, "request",
                side_effect=httpx.HTTPStatusError(
                    "404", request=httpx.Request("GET", "/"), response=resp
                ),
            ):
                assert client.get_config("config", "missing", "default_val") == "default_val"

    def test_get_config_default_empty(self):
        with HubClient("http://test/api/v1") as client:
            resp = _error_response(500)
            with mock.patch.object(
                client._client, "request",
                side_effect=httpx.HTTPStatusError(
                    "500", request=httpx.Request("GET", "/"), response=resp
                ),
            ):
                assert client.get_config("config", "bad") == ""

    def test_set_config(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "updated"}),
            ):
                assert client.set_config("config", "port", "411") is True

    def test_set_config_failure(self):
        with HubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "error"}),
            ):
                assert client.set_config("config", "port", "bad") is False


# =============================================================================
# HubClient — HTTPX Not Available
# =============================================================================


class TestHubClientNoHttpx:
    """Test HubClient when httpx is not available."""

    def test_raises_import_error(self):
        with mock.patch("verlihub.client.api.HTTPX_AVAILABLE", False):
            with pytest.raises(ImportError, match="httpx is required"):
                HubClient("http://test/api/v1")

    def test_async_raises_import_error(self):
        with mock.patch("verlihub.client.api.HTTPX_AVAILABLE", False):
            with pytest.raises(ImportError, match="httpx is required"):
                AsyncHubClient("http://test/api/v1")


# =============================================================================
# AsyncHubClient Tests
# =============================================================================


@pytest.mark.asyncio
class TestAsyncHubClientLogin:
    """Tests for AsyncHubClient authentication."""

    async def test_login_success(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            mock_resp = _json_response({
                "access_token": "async_tok",
                "expires_in": 3600,
                "user_class": 10,
            })
            with mock.patch.object(client._client, "post", return_value=mock_resp):
                result = await client.login("master", "pass")
            assert result is True
            assert client.is_authenticated
            assert client._token == "async_tok"
            assert client._user_class == 10

    async def test_logout(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            client._token = "tok"
            client._user_class = 5
            client._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)

            await client.logout()
            assert not client.is_authenticated
            assert client._token is None

    async def test_is_authenticated_expired(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            client._token = "expired"
            client._token_expires = datetime.now(timezone.utc) - timedelta(hours=1)
            assert not client.is_authenticated


@pytest.mark.asyncio
class TestAsyncHubClientRequest:
    """Tests for AsyncHubClient._request() method."""

    async def test_json_response(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"key": "val"})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                result = await client._request("GET", "/test")
            assert result == {"key": "val"}

    async def test_text_response(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            mock_resp = _text_response("hello")
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                result = await client._request("GET", "/test")
            assert result == "hello"

    async def test_headers_with_token(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            client._token = "my_async_token"
            headers = await client._headers()
            assert headers == {"Authorization": "Bearer my_async_token"}

    async def test_headers_without_token(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            headers = await client._headers()
            assert headers == {}


@pytest.mark.asyncio
class TestAsyncHubClientMethods:
    """Tests for AsyncHubClient API methods."""

    async def test_get_hub_stats(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"user_count": 10})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                result = await client.get_hub_stats()
            assert result["user_count"] == 10

    async def test_get_hub_info(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            mock_resp = _json_response({"hub_name": "Async Hub"})
            with mock.patch.object(client._client, "request", return_value=mock_resp):
                result = await client.get_hub_info()
            assert result["hub_name"] == "Async Hub"

    async def test_get_statistics(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = {"users_online": 5}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.get_statistics()
            assert result["users_online"] == 5

    async def test_get_geo_distribution(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = {"total_countries": 2, "distribution": []}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.get_geo_distribution()
            assert result["total_countries"] == 2

    async def test_get_share_stats(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = {"total": 1000}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.get_share_stats()
            assert result["total"] == 1000

    async def test_get_operators(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = [{"nick": "Op1"}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.get_operators()
            assert len(result) == 1

    async def test_get_bots(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = [{"nick": "Bot1", "description": ""}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.get_bots()
            assert result[0]["nick"] == "Bot1"

    async def test_get_detailed_users(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = [{"nick": "U1"}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)) as m:
                result = await client.get_detailed_users(limit=5, offset=2)
            assert len(result) == 1
            call_kwargs = m.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert params["limit"] == 5

    async def test_get_detailed_users_no_params(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            with mock.patch.object(client._client, "request", return_value=_json_response([])) as m:
                await client.get_detailed_users()
            call_kwargs = m.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert params is None

    async def test_health_check(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = {"status": "healthy"}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.health_check()
            assert result["status"] == "healthy"

    async def test_get_user_count(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"user_count": 99}),
            ):
                assert await client.get_user_count() == 99

    async def test_get_user_list(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"users": ["A", "B"]}),
            ):
                result = await client.get_user_list()
            assert result == ["A", "B"]

    async def test_kick_user(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "kicked"}),
            ):
                assert await client.kick_user("Op", "Bad", "reason") is True

    async def test_send_to_all(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "ok"}),
            ):
                assert await client.send_to_all("Hello") is True

    async def test_send_to_user(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            with mock.patch.object(
                client._client, "request",
                return_value=_json_response({"status": "sent"}),
            ):
                assert await client.send_to_user("Alice", "Hi") is True

    async def test_get_registered_users(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = [{"nick": "Reg1"}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.get_registered_users(limit=10, offset=0)
            assert len(result) == 1

    async def test_register_user(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = {"nick": "New", "user_class": 1}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.register_user("New", "pass", user_class=1)
            assert result["nick"] == "New"

    async def test_get_bans(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = [{"id": 1}]
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.get_bans()
            assert len(result) == 1

    async def test_ban_user(self):
        async with AsyncHubClient("http://test/api/v1") as client:
            data = {"id": 1, "nick": "Bad"}
            with mock.patch.object(client._client, "request", return_value=_json_response(data)):
                result = await client.ban_user("Bad", "reason", duration_hours=24)
            assert result["nick"] == "Bad"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
