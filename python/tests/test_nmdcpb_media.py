"""Tests for Phase 4 MediaShare — storage backend and handler.

Tests:
  TestMediaConfig        — validation, type checking
  TestMediaMeta          — data class serialization
  TestFileSystemStorage  — store / retrieve / delete / expiry / quota
  TestMediaHandler       — upload flow, capabilities, delete, expiry
"""
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from verlihub.client.nmdcpb.media_storage import (
    MediaConfig,
    MediaMeta,
    MediaQuota,
    MediaStorage,
    FileSystemStorage,
    create_storage,
)
from verlihub.client.nmdcpb.media_handler import (
    MediaHandler,
    INLINE_UPLOAD_LIMIT,
)
from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbMediaUpload,
    PbMediaDelete,
    PbStatus,
)


def run_async(coro):
    """Helper to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestMediaConfig(unittest.TestCase):
    """Test MediaConfig validation."""

    def test_default_config_values(self):
        cfg = MediaConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.max_file_size, 50 * 1024 * 1024)
        self.assertEqual(cfg.default_ttl, 7 * 86400)
        self.assertEqual(cfg.max_ttl, 30 * 86400)
        self.assertTrue(len(cfg.allowed_types) > 0)

    def test_type_allowed_exact(self):
        cfg = MediaConfig(allowed_types=["image/jpeg", "image/png"])
        self.assertTrue(cfg.is_type_allowed("image/jpeg"))
        self.assertTrue(cfg.is_type_allowed("image/png"))
        self.assertFalse(cfg.is_type_allowed("image/gif"))
        self.assertFalse(cfg.is_type_allowed("video/mp4"))

    def test_type_allowed_wildcard(self):
        cfg = MediaConfig(allowed_types=["image/*", "video/mp4"])
        self.assertTrue(cfg.is_type_allowed("image/jpeg"))
        self.assertTrue(cfg.is_type_allowed("image/webp"))
        self.assertTrue(cfg.is_type_allowed("video/mp4"))
        self.assertFalse(cfg.is_type_allowed("video/webm"))

    def test_type_allowed_empty_allows_all(self):
        cfg = MediaConfig(allowed_types=[])
        self.assertTrue(cfg.is_type_allowed("anything/whatever"))


class TestMediaMeta(unittest.TestCase):
    """Test MediaMeta data class."""

    def test_to_dict_from_dict(self):
        meta = MediaMeta(
            media_id="abc123",
            filename="test.png",
            mime_type="image/png",
            size=1024,
            uploader_nick="Alice",
            uploaded_at=1000.0,
            expires_at=2000.0,
        )
        d = meta.to_dict()
        self.assertEqual(d["media_id"], "abc123")
        self.assertEqual(d["filename"], "test.png")

        restored = MediaMeta.from_dict(d)
        self.assertEqual(restored.media_id, "abc123")
        self.assertEqual(restored.size, 1024)

    def test_is_expired(self):
        meta = MediaMeta(expires_at=time.time() - 100)
        self.assertTrue(meta.is_expired)

        meta2 = MediaMeta(expires_at=time.time() + 3600)
        self.assertFalse(meta2.is_expired)

        meta3 = MediaMeta(expires_at=0)
        self.assertFalse(meta3.is_expired)  # 0 = no expiry

    def test_is_image_video_audio(self):
        self.assertTrue(MediaMeta(mime_type="image/png").is_image)
        self.assertFalse(MediaMeta(mime_type="video/mp4").is_image)
        self.assertTrue(MediaMeta(mime_type="video/mp4").is_video)
        self.assertTrue(MediaMeta(mime_type="audio/opus").is_audio)


class TestMediaQuota(unittest.TestCase):
    """Test MediaQuota tracking."""

    def test_can_store(self):
        q = MediaQuota(nick="Alice", used_bytes=0, max_bytes=1000, max_files=10)
        self.assertTrue(q.can_store(500))
        self.assertTrue(q.can_store(1000))
        self.assertFalse(q.can_store(1001))

    def test_remaining(self):
        q = MediaQuota(nick="Bob", used_bytes=300, max_bytes=1000,
                       file_count=3, max_files=10)
        self.assertEqual(q.remaining_bytes, 700)
        self.assertEqual(q.remaining_files, 7)


class TestFileSystemStorage(unittest.TestCase):
    """Test FileSystemStorage backend."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nmdcpb_media_test_")
        self.config = MediaConfig(
            storage_path=self.tmpdir,
            max_file_size=1024 * 1024,
            default_ttl=3600,
            max_ttl=86400,
            per_user_quota=10 * 1024 * 1024,
            per_user_max_files=50,
            thumbnails_enabled=False,  # Skip Pillow dependency in tests
        )
        self.storage = FileSystemStorage(self.config)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_store_and_retrieve(self):
        data = b"Hello, MediaShare!"
        meta = run_async(self.storage.store(
            data, "hello.txt", "text/plain", "Alice"))
        self.assertTrue(len(meta.media_id) > 0)
        self.assertEqual(meta.filename, "hello.txt")
        self.assertEqual(meta.size, len(data))
        self.assertEqual(meta.uploader_nick, "Alice")
        self.assertEqual(meta.mime_type, "text/plain")
        self.assertTrue(meta.expires_at > time.time())

        # Retrieve
        got = run_async(self.storage.retrieve(meta.media_id))
        self.assertEqual(got, data)

    def test_get_meta(self):
        data = b"test data"
        meta = run_async(self.storage.store(
            data, "test.bin", "application/octet-stream", "Bob"))

        loaded = run_async(self.storage.get_meta(meta.media_id))
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.media_id, meta.media_id)
        self.assertEqual(loaded.filename, "test.bin")
        self.assertEqual(loaded.uploader_nick, "Bob")

    def test_delete(self):
        data = b"delete me"
        meta = run_async(self.storage.store(
            data, "del.txt", "text/plain", "Charlie"))

        ok = run_async(self.storage.delete(meta.media_id))
        self.assertTrue(ok)

        # Verify deleted
        got = run_async(self.storage.retrieve(meta.media_id))
        self.assertIsNone(got)

    def test_delete_nonexistent(self):
        ok = run_async(self.storage.delete("nonexistent_id"))
        self.assertFalse(ok)

    def test_quota_tracking(self):
        data = b"x" * 500
        run_async(self.storage.store(data, "f1.bin", "application/octet-stream", "Alice"))
        q = self.storage.get_quota("Alice")
        self.assertEqual(q.used_bytes, 500)
        self.assertEqual(q.file_count, 1)

        run_async(self.storage.store(data, "f2.bin", "application/octet-stream", "Alice"))
        self.assertEqual(q.used_bytes, 1000)
        self.assertEqual(q.file_count, 2)

    def test_quota_restored_on_delete(self):
        data = b"x" * 300
        meta = run_async(self.storage.store(
            data, "f.bin", "application/octet-stream", "Alice"))
        q = self.storage.get_quota("Alice")
        self.assertEqual(q.used_bytes, 300)

        run_async(self.storage.delete(meta.media_id))
        self.assertEqual(q.used_bytes, 0)
        self.assertEqual(q.file_count, 0)

    def test_checksum_computed(self):
        data = b"checksum test data"
        expected = hashlib.sha256(data).hexdigest()
        meta = run_async(self.storage.store(
            data, "cs.bin", "application/octet-stream", "Alice"))
        self.assertEqual(meta.checksum_sha256, expected)

    def test_ttl_clamped_to_max(self):
        data = b"ttl test"
        meta = run_async(self.storage.store(
            data, "t.bin", "text/plain", "Alice", ttl=999999))
        # Should be clamped to max_ttl (86400 in our config)
        expected_max = time.time() + 86400 + 5  # small tolerance
        self.assertLess(meta.expires_at, expected_max)

    def test_list_expired(self):
        data = b"expired data"
        meta = run_async(self.storage.store(
            data, "exp.txt", "text/plain", "Alice", ttl=1))
        # Manually set expires_at to the past
        meta_path = os.path.join(
            self.tmpdir, meta.media_id[:2], meta.media_id, "meta.json")
        with open(meta_path) as f:
            d = json.load(f)
        d["expires_at"] = time.time() - 100
        with open(meta_path, "w") as f:
            json.dump(d, f)

        expired = run_async(self.storage.list_expired())
        self.assertIn(meta.media_id, expired)

    def test_purge_expired(self):
        data = b"will expire"
        meta = run_async(self.storage.store(
            data, "exp.txt", "text/plain", "Alice", ttl=1))
        # Force expiry
        meta_path = os.path.join(
            self.tmpdir, meta.media_id[:2], meta.media_id, "meta.json")
        with open(meta_path) as f:
            d = json.load(f)
        d["expires_at"] = time.time() - 100
        with open(meta_path, "w") as f:
            json.dump(d, f)

        count = run_async(self.storage.purge_expired())
        self.assertEqual(count, 1)

        # Verify gone
        got = run_async(self.storage.retrieve(meta.media_id))
        self.assertIsNone(got)

    def test_list_by_user(self):
        run_async(self.storage.store(b"a", "a.txt", "text/plain", "Alice"))
        run_async(self.storage.store(b"b", "b.txt", "text/plain", "Bob"))
        run_async(self.storage.store(b"c", "c.txt", "text/plain", "Alice"))

        alice_files = run_async(self.storage.list_by_user("Alice"))
        bob_files = run_async(self.storage.list_by_user("Bob"))
        self.assertEqual(len(alice_files), 2)
        self.assertEqual(len(bob_files), 1)

    def test_validate_upload_size_too_large(self):
        err = self.storage.validate_upload("big.bin", "application/octet-stream",
                                           10 * 1024 * 1024, "Alice")
        self.assertIsNotNone(err)
        self.assertIn("too large", err)

    def test_validate_upload_type_not_allowed(self):
        cfg = MediaConfig(
            storage_path=self.tmpdir,
            allowed_types=["image/png"],
        )
        storage = FileSystemStorage(cfg)
        err = storage.validate_upload("test.exe", "application/x-executable",
                                      100, "Alice")
        self.assertIsNotNone(err)
        self.assertIn("not allowed", err)


class TestMediaHandler(unittest.TestCase):
    """Test MediaHandler — hub-side media message processing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nmdcpb_handler_test_")
        self.config = MediaConfig(
            storage_path=self.tmpdir,
            max_file_size=1024 * 1024,
            default_ttl=3600,
            max_ttl=86400,
            per_user_quota=10 * 1024 * 1024,
            thumbnails_enabled=False,
        )
        self.sent_messages = []
        self.status_messages = []

        def mock_send(wire, nick):
            self.sent_messages.append((wire, nick))

        def mock_status(nick, level, code, text):
            self.status_messages.append((nick, level, code, text))

        self.handler = MediaHandler(
            config=self.config,
            send_fn=mock_send,
            status_fn=mock_status,
            hub_url="http://localhost:8080",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_handle_upload_small_inline(self):
        """Small file -> inline upload with pending ID."""
        upload = PbMediaUpload()
        upload.filename = "test.txt"
        upload.mime_type = "text/plain"
        upload.size = 100
        upload.requested_ttl = 3600

        run_async(self.handler.handle_media_upload("Alice", upload))

        # Should have sent capabilities with inline upload ID
        self.assertEqual(len(self.sent_messages), 1)
        # Verify pending upload was registered
        self.assertIn("Alice", self.handler._pending_uploads)
        self.assertEqual(len(self.handler._pending_uploads["Alice"]), 1)

    def test_handle_upload_too_large(self):
        """File exceeding max_file_size is rejected."""
        upload = PbMediaUpload()
        upload.filename = "huge.bin"
        upload.mime_type = "application/octet-stream"
        upload.size = 10 * 1024 * 1024  # 10 MB, config max is 1 MB

        run_async(self.handler.handle_media_upload("Alice", upload))

        # Should have sent error status
        self.assertEqual(len(self.status_messages), 1)
        self.assertIn("too large", self.status_messages[0][3])

    def test_complete_inline_upload(self):
        """Complete inline upload flow: request -> approve -> data -> meta."""
        # Step 1: Request upload
        upload = PbMediaUpload()
        upload.filename = "hello.txt"
        upload.mime_type = "text/plain"
        upload.size = 13
        upload.checksum_sha256 = hashlib.sha256(b"Hello, World!").hexdigest()

        run_async(self.handler.handle_media_upload("Alice", upload))
        self.assertEqual(len(self.handler._pending_uploads.get("Alice", {})), 1)

        # Get the upload ID from pending
        upload_id = list(self.handler._pending_uploads["Alice"].keys())[0]

        # Step 2: Complete with data
        self.sent_messages.clear()
        meta = run_async(self.handler.handle_inline_upload(
            "Alice", upload_id, b"Hello, World!"))

        self.assertIsNotNone(meta)
        self.assertEqual(meta.filename, "hello.txt")
        self.assertEqual(meta.size, 13)
        self.assertEqual(meta.uploader_nick, "Alice")

        # Should have sent PbMediaMeta back
        self.assertEqual(len(self.sent_messages), 1)

        # Pending should be cleared
        self.assertEqual(len(self.handler._pending_uploads.get("Alice", {})), 0)

    def test_inline_upload_size_mismatch(self):
        """Inline upload with wrong size is rejected."""
        upload = PbMediaUpload()
        upload.filename = "test.txt"
        upload.mime_type = "text/plain"
        upload.size = 10

        run_async(self.handler.handle_media_upload("Alice", upload))
        upload_id = list(self.handler._pending_uploads["Alice"].keys())[0]

        self.status_messages.clear()
        meta = run_async(self.handler.handle_inline_upload(
            "Alice", upload_id, b"wrong size data"))

        self.assertIsNone(meta)
        self.assertEqual(len(self.status_messages), 1)
        self.assertIn("Size mismatch", self.status_messages[0][3])

    def test_inline_upload_checksum_mismatch(self):
        """Inline upload with wrong checksum is rejected."""
        upload = PbMediaUpload()
        upload.filename = "test.txt"
        upload.mime_type = "text/plain"
        upload.size = 4
        upload.checksum_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

        run_async(self.handler.handle_media_upload("Alice", upload))
        upload_id = list(self.handler._pending_uploads["Alice"].keys())[0]

        self.status_messages.clear()
        meta = run_async(self.handler.handle_inline_upload(
            "Alice", upload_id, b"test"))

        self.assertIsNone(meta)
        self.assertEqual(len(self.status_messages), 1)
        self.assertIn("Checksum", self.status_messages[0][3])

    def test_handle_media_delete_owner(self):
        """Owner can delete their own media."""
        data = b"delete me"
        meta = run_async(self.handler.storage.store(
            data, "del.txt", "text/plain", "Alice"))

        delete = PbMediaDelete()
        delete.media_id = meta.media_id

        self.status_messages.clear()
        run_async(self.handler.handle_media_delete("Alice", delete))

        # Should get INFO status (success)
        self.assertEqual(len(self.status_messages), 1)
        self.assertEqual(self.status_messages[0][1], PbStatus.INFO)

        # File should be gone
        got = run_async(self.handler.storage.retrieve(meta.media_id))
        self.assertIsNone(got)

    def test_handle_media_delete_non_owner(self):
        """Non-owner cannot delete media."""
        data = b"not yours"
        meta = run_async(self.handler.storage.store(
            data, "nope.txt", "text/plain", "Alice"))

        delete = PbMediaDelete()
        delete.media_id = meta.media_id

        self.status_messages.clear()
        run_async(self.handler.handle_media_delete("Bob", delete))

        # Should get ERROR
        self.assertEqual(len(self.status_messages), 1)
        self.assertEqual(self.status_messages[0][1], PbStatus.ERROR)

    def test_handle_capabilities_request(self):
        """Capabilities request returns config info."""
        run_async(self.handler.handle_media_capabilities_request("Alice"))
        self.assertEqual(len(self.sent_messages), 1)

    def test_operator_delete(self):
        """Operators can force-delete any media."""
        data = b"admin delete"
        meta = run_async(self.handler.storage.store(
            data, "admin.txt", "text/plain", "Alice"))

        run_async(self.handler.handle_operator_delete("Operator", meta.media_id, "policy violation"))

        got = run_async(self.handler.storage.retrieve(meta.media_id))
        self.assertIsNone(got)

    def test_expiry_check(self):
        """check_expiry purges expired media."""
        data = b"will expire"
        meta = run_async(self.handler.storage.store(
            data, "exp.txt", "text/plain", "Alice", ttl=1))
        # Force expiry
        meta_path = os.path.join(
            self.tmpdir, meta.media_id[:2], meta.media_id, "meta.json")
        with open(meta_path) as f:
            d = json.load(f)
        d["expires_at"] = time.time() - 100
        with open(meta_path, "w") as f:
            json.dump(d, f)

        # Force check (override interval)
        self.handler._last_expiry_check = 0
        count = run_async(self.handler.check_expiry())
        self.assertEqual(count, 1)

    def test_get_stats(self):
        """Stats are tracked."""
        stats = self.handler.get_stats()
        self.assertIn("uploads", stats)
        self.assertIn("deletes", stats)
        self.assertIn("expired_purged", stats)

    def test_media_meta_url_construction(self):
        """Media URLs use hub_url prefix."""
        data = b"url test"
        meta = run_async(self.handler.storage.store(
            data, "url.txt", "text/plain", "Alice"))

        self.sent_messages.clear()
        run_async(self.handler.handle_media_meta_request("Alice", meta.media_id))

        self.assertEqual(len(self.sent_messages), 1)
        # The sent wire data contains the URL — we can verify the URL helper
        url = self.handler._media_url(meta.media_id, "data.bin")
        self.assertTrue(url.startswith("http://localhost:8080/media/"))
        self.assertIn(meta.media_id, url)


class TestCreateStorage(unittest.TestCase):
    """Test storage factory."""

    def test_create_filesystem(self):
        tmpdir = tempfile.mkdtemp(prefix="nmdcpb_factory_test_")
        try:
            cfg = MediaConfig(storage_backend="filesystem", storage_path=tmpdir)
            storage = create_storage(cfg)
            self.assertIsInstance(storage, FileSystemStorage)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
