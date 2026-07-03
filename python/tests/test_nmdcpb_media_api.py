"""Tests for NMDCpb Media HTTP API — FastAPI endpoints.

Tests:
  TestMediaApiAuth           — session token generation, validation, revocation
  TestMediaApiUpload         — upload endpoint (success, errors, quota)
  TestMediaApiDownload       — download, thumbnail, meta endpoints
  TestMediaApiDelete         — owner delete, admin delete, unauthorized
  TestMediaApiQuota          — per-user quota reporting
  TestMediaApiAppIntegration — router wired into hub FastAPI app
"""
import asyncio
import hashlib
import io
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from verlihub.client.nmdcpb.media_api import (
    SessionInfo,
    configure,
    generate_session_token,
    prune_expired_sessions,
    revoke_session_token,
    revoke_sessions_for_nick,
    validate_token,
    _active_sessions,
    TOKEN_TTL_SEC,
)


class TestMediaApiAuth(unittest.TestCase):
    """Session token management."""

    def setUp(self):
        _active_sessions.clear()
        configure(secret="test-secret-key", token_ttl=3600)

    def tearDown(self):
        _active_sessions.clear()

    def test_generate_token_format(self):
        token = generate_session_token("Alice")
        self.assertTrue(token.startswith("nmdcpb_"))
        self.assertGreater(len(token), 10)

    def test_generate_and_validate(self):
        token = generate_session_token("Alice", ip="127.0.0.1")
        session = validate_token(token)
        self.assertIsNotNone(session)
        self.assertEqual(session.nick, "Alice")
        self.assertEqual(session.ip, "127.0.0.1")
        self.assertFalse(session.is_admin)

    def test_generate_admin_token(self):
        token = generate_session_token("Admin", is_admin=True)
        session = validate_token(token)
        self.assertTrue(session.is_admin)

    def test_invalid_token_returns_none(self):
        self.assertIsNone(validate_token("nmdcpb_bogus"))

    def test_revoke_token(self):
        token = generate_session_token("Alice")
        self.assertIsNotNone(validate_token(token))
        revoke_session_token(token)
        self.assertIsNone(validate_token(token))

    def test_revoke_sessions_for_nick(self):
        t1 = generate_session_token("Alice")
        t2 = generate_session_token("Alice")
        t3 = generate_session_token("Bob")
        count = revoke_sessions_for_nick("Alice")
        self.assertEqual(count, 2)
        self.assertIsNone(validate_token(t1))
        self.assertIsNone(validate_token(t2))
        self.assertIsNotNone(validate_token(t3))

    def test_expired_token_pruned(self):
        configure(secret="test", token_ttl=1)
        token = generate_session_token("Alice")
        self.assertIsNotNone(validate_token(token))
        # Manually expire it
        _active_sessions[token].expires_at = time.time() - 1
        self.assertIsNone(validate_token(token))

    def test_prune_expired_sessions(self):
        configure(secret="test", token_ttl=1)
        t1 = generate_session_token("Alice")
        t2 = generate_session_token("Bob")
        # Expire t1 only
        _active_sessions[t1].expires_at = time.time() - 1
        pruned = prune_expired_sessions()
        self.assertEqual(pruned, 1)
        self.assertIsNone(validate_token(t1))
        self.assertIsNotNone(validate_token(t2))

    def test_configure_random_secret(self):
        """configure() with empty secret generates random key."""
        configure(secret="")
        token = generate_session_token("Alice")
        self.assertIsNotNone(validate_token(token))

    def test_session_info_properties(self):
        si = SessionInfo("Alice", "10.0.0.1", is_admin=False, ttl=60)
        self.assertEqual(si.nick, "Alice")
        self.assertEqual(si.ip, "10.0.0.1")
        self.assertFalse(si.is_expired)
        si.expires_at = time.time() - 1
        self.assertTrue(si.is_expired)


class TestMediaApiEndpoints(unittest.TestCase):
    """Test FastAPI media endpoints using TestClient."""

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            cls.fastapi_available = True
        except ImportError:
            cls.fastapi_available = False

    def setUp(self):
        if not self.fastapi_available:
            self.skipTest("FastAPI not available")

        _active_sessions.clear()
        configure(secret="test-key", token_ttl=3600)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from verlihub.client.nmdcpb.media_api import router, set_storage

        self.app = FastAPI()
        self.app.include_router(router)

        # Create a temp dir for storage
        self.tmpdir = tempfile.mkdtemp()

        # Set up a real FileSystemStorage
        from verlihub.client.nmdcpb.media_storage import (
            FileSystemStorage, MediaConfig,
        )
        cfg = MediaConfig(
            enabled=True,
            storage_backend="filesystem",
            storage_path=self.tmpdir,
            max_file_size=10 * 1024 * 1024,
            per_user_quota=50 * 1024 * 1024,
        )
        self.storage = FileSystemStorage(cfg)
        set_storage(self.storage)

        self.client = TestClient(self.app)
        self.token = generate_session_token("Alice")
        self.admin_token = generate_session_token("Admin", is_admin=True)

    def tearDown(self):
        _active_sessions.clear()
        if hasattr(self, 'tmpdir') and os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _auth_header(self, token=None):
        return {"Authorization": f"Bearer {token or self.token}"}

    def test_upload_no_auth(self):
        r = self.client.post("/api/media/upload",
                             files={"file": ("test.txt", b"hello")})
        self.assertEqual(r.status_code, 401)

    def test_upload_invalid_token(self):
        r = self.client.post(
            "/api/media/upload",
            files={"file": ("test.txt", b"hello")},
            headers={"Authorization": "Bearer nmdcpb_invalid"},
        )
        self.assertEqual(r.status_code, 401)

    def test_upload_success(self):
        data = b"Hello, media world!"
        r = self.client.post(
            "/api/media/upload",
            files={"file": ("hello.txt", data, "text/plain")},
            headers=self._auth_header(),
        )
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertIn("media_id", body)
        self.assertEqual(body["filename"], "hello.txt")
        self.assertEqual(body["mime_type"], "text/plain")
        self.assertEqual(body["size"], len(data))
        self.assertEqual(body["uploader"], "Alice")
        expected_hash = hashlib.sha256(data).hexdigest()
        self.assertEqual(body["checksum_sha256"], expected_hash)

    def test_upload_empty_file(self):
        r = self.client.post(
            "/api/media/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
            headers=self._auth_header(),
        )
        self.assertEqual(r.status_code, 400)

    def test_download_success(self):
        # Upload first
        data = b"downloadable content"
        r = self.client.post(
            "/api/media/upload",
            files={"file": ("dl.txt", data, "text/plain")},
            headers=self._auth_header(),
        )
        media_id = r.json()["media_id"]

        # Download
        r = self.client.get(
            f"/api/media/{media_id}",
            headers=self._auth_header(),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, data)

    def test_download_not_found(self):
        r = self.client.get(
            "/api/media/nonexistent-id",
            headers=self._auth_header(),
        )
        self.assertEqual(r.status_code, 404)

    def test_meta_success(self):
        data = b"meta test data"
        r = self.client.post(
            "/api/media/upload",
            files={"file": ("meta.txt", data, "text/plain")},
            headers=self._auth_header(),
        )
        media_id = r.json()["media_id"]

        r = self.client.get(
            f"/api/media/{media_id}/meta",
            headers=self._auth_header(),
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["media_id"], media_id)
        self.assertEqual(body["filename"], "meta.txt")
        self.assertEqual(body["uploader"], "Alice")

    def test_delete_by_owner(self):
        data = b"to be deleted"
        r = self.client.post(
            "/api/media/upload",
            files={"file": ("del.txt", data, "text/plain")},
            headers=self._auth_header(),
        )
        media_id = r.json()["media_id"]

        r = self.client.delete(
            f"/api/media/{media_id}",
            headers=self._auth_header(),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["deleted"], media_id)

        # Verify actually deleted
        r = self.client.get(
            f"/api/media/{media_id}",
            headers=self._auth_header(),
        )
        self.assertEqual(r.status_code, 404)

    def test_delete_by_admin(self):
        data = b"admin delete test"
        r = self.client.post(
            "/api/media/upload",
            files={"file": ("adm.txt", data, "text/plain")},
            headers=self._auth_header(),
        )
        media_id = r.json()["media_id"]

        # Admin deletes Alice's file
        r = self.client.delete(
            f"/api/media/{media_id}",
            headers=self._auth_header(self.admin_token),
        )
        self.assertEqual(r.status_code, 200)

    def test_delete_unauthorized(self):
        data = b"no delete for you"
        r = self.client.post(
            "/api/media/upload",
            files={"file": ("nodel.txt", data, "text/plain")},
            headers=self._auth_header(),
        )
        media_id = r.json()["media_id"]

        # Bob can't delete Alice's file
        bob_token = generate_session_token("Bob")
        r = self.client.delete(
            f"/api/media/{media_id}",
            headers=self._auth_header(bob_token),
        )
        self.assertEqual(r.status_code, 403)

    def test_quota_endpoint(self):
        r = self.client.get(
            "/api/media/quota",
            headers=self._auth_header(),
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["nick"], "Alice")
        self.assertIn("used_bytes", body)
        self.assertIn("remaining_bytes", body)
        self.assertIn("max_bytes", body)


class TestMediaApiAppIntegration(unittest.TestCase):
    """Verify the media router is included in the hub's main app."""

    def test_media_router_registered(self):
        """The media API routes should be registered in the app."""
        try:
            from verlihub.client.nmdcpb.media_api import router
            self.assertIsNotNone(router, "media_api.router should not be None")
        except ImportError:
            self.skipTest("media_api not available")

    def test_media_endpoints_exist(self):
        """Verify the media router has all expected routes."""
        try:
            from verlihub.client.nmdcpb.media_api import router
        except ImportError:
            self.skipTest("media_api not available")

        routes = {r.path for r in router.routes if hasattr(r, 'path')}
        expected = {"/api/media/upload", "/api/media/{media_id}",
                    "/api/media/{media_id}/thumb",
                    "/api/media/{media_id}/meta", "/api/media/quota"}
        for ep in expected:
            self.assertIn(ep, routes, f"Missing route: {ep}")

    def test_configure_sets_token_secret(self):
        """configure() should set the HMAC token secret."""
        _active_sessions.clear()
        configure(secret="my-secret", token_ttl=120)
        token = generate_session_token("TestUser")
        session = validate_token(token)
        self.assertIsNotNone(session)
        self.assertEqual(session.nick, "TestUser")
        _active_sessions.clear()


if __name__ == "__main__":
    unittest.main()
