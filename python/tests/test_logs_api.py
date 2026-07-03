"""
Tests for the system logs REST API (``/api/v1/logs``).

Covers: GET (paginated retrieval, auth), DELETE (clear, auth),
parameter validation, empty buffer behaviour.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from verlihub.api.auth import Permission, create_access_token


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def app():
    from verlihub.api.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_header():
    token = create_access_token("admin", Permission.ADMIN)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def operator_header():
    token = create_access_token("op_user", Permission.OPERATOR)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def user_header():
    token = create_access_token("user1", Permission.USER)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture(autouse=True)
def fresh_buffer():
    """Ensure a clean buffer for each test."""
    from verlihub.log_buffer import get_log_buffer
    buf = get_log_buffer()
    buf.clear()
    yield buf
    buf.clear()


# ======================================================================
# GET /api/v1/logs
# ======================================================================


class TestGetLogs:

    def test_empty_buffer(self, client, admin_header):
        r = client.get("/api/v1/logs", headers=admin_header)
        assert r.status_code == 200
        data = r.json()
        assert data["entries"] == []
        assert data["total"] == 0
        assert data["returned"] == 0

    def test_returns_entries(self, client, admin_header, fresh_buffer):
        fresh_buffer.add(level="info", message="msg1", log_type="core")
        fresh_buffer.add(level="debug", message="msg2", log_type="system")
        r = client.get("/api/v1/logs", headers=admin_header)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert data["returned"] == 2
        assert len(data["entries"]) == 2
        # oldest first
        assert data["entries"][0]["message"] == "msg1"
        assert data["entries"][1]["message"] == "msg2"

    def test_limit_parameter(self, client, admin_header, fresh_buffer):
        for i in range(10):
            fresh_buffer.add(level="info", message=f"m-{i}")
        r = client.get("/api/v1/logs?limit=3", headers=admin_header)
        data = r.json()
        assert data["returned"] == 3
        assert data["total"] == 10
        # most recent 3
        msgs = [e["message"] for e in data["entries"]]
        assert msgs == ["m-7", "m-8", "m-9"]

    def test_limit_exceeds_buffer_size(self, client, admin_header, fresh_buffer):
        fresh_buffer.add(level="info", message="only-one")
        r = client.get("/api/v1/logs?limit=5000", headers=admin_header)
        data = r.json()
        assert data["returned"] == 1
        assert data["total"] == 1

    def test_limit_validation_min(self, client, admin_header):
        r = client.get("/api/v1/logs?limit=0", headers=admin_header)
        assert r.status_code == 422  # FastAPI validation error

    def test_limit_validation_max(self, client, admin_header):
        r = client.get("/api/v1/logs?limit=10000", headers=admin_header)
        assert r.status_code == 422

    def test_entry_schema(self, client, admin_header, fresh_buffer):
        fresh_buffer.add(level="info", message="schema-test", log_type="core")
        r = client.get("/api/v1/logs", headers=admin_header)
        entry = r.json()["entries"][0]
        assert entry["type"] == "log"
        assert entry["level"] == "info"
        assert entry["message"] == "schema-test"
        assert entry["log_type"] == "core"
        assert "time" in entry

    # ---- Auth ----

    def test_requires_admin(self, client, operator_header):
        r = client.get("/api/v1/logs", headers=operator_header)
        assert r.status_code == 403

    def test_requires_auth(self, client):
        r = client.get("/api/v1/logs")
        assert r.status_code in (401, 403)

    def test_user_cannot_access(self, client, user_header):
        r = client.get("/api/v1/logs", headers=user_header)
        assert r.status_code == 403


# ======================================================================
# DELETE /api/v1/logs
# ======================================================================


class TestDeleteLogs:

    def test_clear_empty(self, client, admin_header):
        r = client.delete("/api/v1/logs", headers=admin_header)
        assert r.status_code == 200
        data = r.json()
        assert data["cleared"] == 0

    def test_clear_with_entries(self, client, admin_header, fresh_buffer):
        for i in range(5):
            fresh_buffer.add(level="info", message=f"m{i}")
        r = client.delete("/api/v1/logs", headers=admin_header)
        assert r.status_code == 200
        data = r.json()
        assert data["cleared"] == 5
        assert "message" in data
        # Buffer should be empty now
        assert len(fresh_buffer) == 0

    def test_get_after_clear(self, client, admin_header, fresh_buffer):
        fresh_buffer.add(level="info", message="gone")
        client.delete("/api/v1/logs", headers=admin_header)
        r = client.get("/api/v1/logs", headers=admin_header)
        assert r.json()["total"] == 0

    # ---- Auth ----

    def test_requires_admin(self, client, operator_header):
        r = client.delete("/api/v1/logs", headers=operator_header)
        assert r.status_code == 403

    def test_requires_auth(self, client):
        r = client.delete("/api/v1/logs")
        assert r.status_code in (401, 403)


# ======================================================================
# POST /api/v1/logs  (inject entries)
# ======================================================================


class TestInjectLogs:

    def test_inject_single_entry(self, client, admin_header, fresh_buffer):
        payload = {"entries": [{"level": "info", "message": "injected-1", "log_type": "test"}]}
        r = client.post("/api/v1/logs", json=payload, headers=admin_header)
        assert r.status_code == 200
        data = r.json()
        assert data["added"] == 1
        assert data["total"] == 1
        assert len(fresh_buffer) == 1
        assert fresh_buffer.get_all()[0]["message"] == "injected-1"

    def test_inject_multiple_entries(self, client, admin_header, fresh_buffer):
        payload = {"entries": [
            {"level": "info", "message": f"m{i}", "log_type": "system"}
            for i in range(5)
        ]}
        r = client.post("/api/v1/logs", json=payload, headers=admin_header)
        assert r.status_code == 200
        data = r.json()
        assert data["added"] == 5
        assert data["total"] == 5

    def test_inject_defaults(self, client, admin_header, fresh_buffer):
        """Entries with only 'message' should get default level/log_type."""
        payload = {"entries": [{"message": "default-test"}]}
        r = client.post("/api/v1/logs", json=payload, headers=admin_header)
        assert r.status_code == 200
        entry = fresh_buffer.get_all()[0]
        assert entry["level"] == "info"
        assert entry["log_type"] == "system"

    def test_inject_empty_list(self, client, admin_header, fresh_buffer):
        payload = {"entries": []}
        r = client.post("/api/v1/logs", json=payload, headers=admin_header)
        assert r.status_code == 200
        data = r.json()
        assert data["added"] == 0
        assert data["total"] == 0

    def test_inject_visible_via_get(self, client, admin_header, fresh_buffer):
        """Injected entries should be visible via GET."""
        payload = {"entries": [{"message": "visible-via-get", "log_type": "core"}]}
        client.post("/api/v1/logs", json=payload, headers=admin_header)
        r = client.get("/api/v1/logs", headers=admin_header)
        msgs = [e["message"] for e in r.json()["entries"]]
        assert "visible-via-get" in msgs

    def test_inject_requires_admin(self, client, operator_header):
        payload = {"entries": [{"message": "no-auth"}]}
        r = client.post("/api/v1/logs", json=payload, headers=operator_header)
        assert r.status_code == 403

    def test_inject_requires_auth(self, client):
        payload = {"entries": [{"message": "no-auth"}]}
        r = client.post("/api/v1/logs", json=payload)
        assert r.status_code in (401, 403)

    def test_inject_bad_body(self, client, admin_header):
        r = client.post("/api/v1/logs", json={"not_entries": []}, headers=admin_header)
        assert r.status_code == 422
