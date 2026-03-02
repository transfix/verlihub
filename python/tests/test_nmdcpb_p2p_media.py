"""Tests for Phase 4b P2P media routing in hub_plugin + media_meta dispatch.

Tests:
  TestP2PChatValidation        — _validate_p2p_chat_attachments
  TestP2PMediaStatusForward    — _forward_p2p_media_status
  TestMediaMetaDispatch        — media_meta routing via _route_media
  TestP2PQuotaFallback         — _handle_media_upload_quota_fallback
  TestRouteMediaIntegration    — _route_media for all 4 payload types
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbChat,
    PbMediaUpload,
    PbMediaMeta,
    PbMediaDelete,
    PbMediaCapabilities,
    PbP2PMediaRef,
    PbP2PMediaStatus,
    PbStatus,
)
from verlihub.client.nmdcpb.wire import WireCodec


# Module path for patching
HP = "verlihub.client.nmdcpb.hub_plugin"


class TestP2PChatValidation(unittest.TestCase):
    """Test _validate_p2p_chat_attachments."""

    def _import_fn(self):
        from verlihub.client.nmdcpb.hub_plugin import _validate_p2p_chat_attachments
        return _validate_p2p_chat_attachments

    def test_no_chat_field_returns_true(self):
        fn = self._import_fn()
        env = PbEnvelope()
        # No chat field set
        self.assertTrue(fn("alice", env))

    def test_empty_attachments_returns_true(self):
        fn = self._import_fn()
        env = PbEnvelope()
        env.chat.text = "hello"
        # No p2p_attachments
        self.assertTrue(fn("alice", env))

    @patch(f"{HP}._send_status")
    @patch(f"{HP}.ENABLE_P2P_MEDIA", False)
    def test_p2p_disabled_returns_false(self, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        env.chat.text = "photo"
        ref = env.chat.p2p_attachments.add()
        ref.tth = "ABC123"
        ref.filename = "test.jpg"
        ref.size = 1000

        self.assertFalse(fn("alice", env))
        mock_status.assert_called_once()
        args = mock_status.call_args[0]
        self.assertEqual(args[0], "alice")
        self.assertEqual(args[2], 50)  # error code

    @patch(f"{HP}._send_status")
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    def test_missing_tth_returns_false(self, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        env.chat.text = "photo"
        ref = env.chat.p2p_attachments.add()
        ref.filename = "test.jpg"
        ref.size = 1000
        # No tth set

        self.assertFalse(fn("alice", env))
        args = mock_status.call_args[0]
        self.assertEqual(args[2], 52)

    @patch(f"{HP}._send_status")
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    def test_missing_filename_returns_false(self, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        env.chat.text = "photo"
        ref = env.chat.p2p_attachments.add()
        ref.tth = "ABC123"
        ref.size = 1000
        # No filename

        self.assertFalse(fn("alice", env))
        args = mock_status.call_args[0]
        self.assertEqual(args[2], 52)

    @patch(f"{HP}._send_status")
    @patch(f"{HP}.P2P_MEDIA_MAX_SIZE", 5000)
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    def test_file_too_large_returns_false(self, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        env.chat.text = "bigfile"
        ref = env.chat.p2p_attachments.add()
        ref.tth = "ABC123"
        ref.filename = "big.zip"
        ref.size = 10000  # > 5000

        self.assertFalse(fn("alice", env))
        args = mock_status.call_args[0]
        self.assertEqual(args[2], 53)

    @patch(f"{HP}._send_status")
    @patch(f"{HP}.P2P_MEDIA_MAX_SIZE", 100000)
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    def test_valid_attachment_returns_true(self, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        env.chat.text = "nice photo"
        ref = env.chat.p2p_attachments.add()
        ref.tth = "ABCDEF1234567890"
        ref.filename = "vacation.jpg"
        ref.size = 50000

        self.assertTrue(fn("alice", env))
        mock_status.assert_not_called()

    @patch(f"{HP}._send_status")
    @patch(f"{HP}.P2P_MEDIA_MAX_SIZE", 100000)
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    def test_multiple_valid_attachments(self, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        env.chat.text = "photos"
        for i in range(3):
            ref = env.chat.p2p_attachments.add()
            ref.tth = f"TTH{i:020d}"
            ref.filename = f"photo{i}.jpg"
            ref.size = 1000 * (i + 1)

        self.assertTrue(fn("alice", env))
        mock_status.assert_not_called()


class TestP2PMediaStatusForward(unittest.TestCase):
    """Test _forward_p2p_media_status."""

    def _import_fn(self):
        from verlihub.client.nmdcpb.hub_plugin import _forward_p2p_media_status
        return _forward_p2p_media_status

    @patch(f"{HP}._send_status")
    @patch(f"{HP}.ENABLE_P2P_MEDIA", False)
    def test_p2p_disabled_sends_error(self, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        fn("alice", "bob", env)
        mock_status.assert_called_once()
        self.assertEqual(mock_status.call_args[0][2], 50)

    @patch(f"{HP}._send_to_user")
    @patch(f"{HP}._check_rate", return_value=True)
    @patch(f"{HP}._is_pb_user", return_value=True)
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    @patch(f"{HP}._stats", {"p2p_media_status_forwarded": 0})
    def test_valid_forward(self, mock_ispb, mock_rate, mock_send):
        fn = self._import_fn()
        env = PbEnvelope()
        status = env.p2p_media_status
        status.tth = "ABCDEF123"
        status.status = PbP2PMediaStatus.DOWNLOADING

        fn("alice", "bob", env)
        mock_send.assert_called_once()
        # Check that it was forwarded to bob
        call_args = mock_send.call_args[0]
        self.assertEqual(call_args[1], "bob")

    @patch(f"{HP}._send_status")
    @patch(f"{HP}._is_pb_user", return_value=False)
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    def test_non_pb_user_rejected(self, mock_ispb, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        fn("alice", "legacy_user", env)
        mock_status.assert_called_once()
        self.assertEqual(mock_status.call_args[0][2], 51)

    @patch(f"{HP}._send_to_user")
    @patch(f"{HP}._check_rate", return_value=False)
    @patch(f"{HP}._is_pb_user", return_value=True)
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    def test_rate_limited(self, mock_ispb, mock_rate, mock_send):
        fn = self._import_fn()
        env = PbEnvelope()
        fn("alice", "bob", env)
        mock_send.assert_not_called()


class TestMediaMetaDispatch(unittest.TestCase):
    """Test media_meta routing via _route_media."""

    def _import_fn(self):
        from verlihub.client.nmdcpb.hub_plugin import _route_media
        return _route_media

    @patch(f"{HP}._send_status")
    @patch(f"{HP}._get_media_handler", return_value=None)
    def test_no_handler_sends_error(self, mock_handler, mock_status):
        fn = self._import_fn()
        env = PbEnvelope()
        env.media_meta.media_id = "test-id"
        fn("alice", env, "media_meta")
        mock_status.assert_called_once()
        self.assertEqual(mock_status.call_args[0][2], 40)

    @patch(f"{HP}._send_status")
    @patch(f"{HP}._get_event_loop")
    @patch(f"{HP}._get_media_handler")
    def test_media_meta_dispatched(self, mock_handler_fn, mock_loop_fn, mock_status):
        fn = self._import_fn()
        handler = MagicMock()
        handler.handle_media_meta_request = AsyncMock()
        mock_handler_fn.return_value = handler

        loop = asyncio.new_event_loop()
        mock_loop_fn.return_value = loop

        env = PbEnvelope()
        env.media_meta.media_id = "media-123"
        fn("alice", env, "media_meta")

        handler.handle_media_meta_request.assert_called_once_with("alice", "media-123")
        loop.close()

    @patch(f"{HP}._send_status")
    @patch(f"{HP}._get_media_handler")
    def test_media_meta_empty_id_sends_error(self, mock_handler_fn, mock_status):
        fn = self._import_fn()
        handler = MagicMock()
        mock_handler_fn.return_value = handler

        env = PbEnvelope()
        # media_meta with empty media_id
        env.media_meta.media_id = ""
        fn("alice", env, "media_meta")

        mock_status.assert_called_once()
        self.assertEqual(mock_status.call_args[0][2], 44)


class TestP2PQuotaFallback(unittest.TestCase):
    """Test _handle_media_upload_quota_fallback."""

    def _import_fn(self):
        from verlihub.client.nmdcpb.hub_plugin import _handle_media_upload_quota_fallback
        return _handle_media_upload_quota_fallback

    @patch(f"{HP}.ENABLE_P2P_MEDIA", False)
    def test_p2p_disabled_returns_false(self):
        fn = self._import_fn()
        upload = PbMediaUpload()
        upload.size = 1000
        self.assertFalse(fn("alice", upload))

    @patch(f"{HP}._get_media_handler", return_value=None)
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    def test_no_handler_returns_false(self, mock_handler):
        fn = self._import_fn()
        upload = PbMediaUpload()
        upload.size = 1000
        self.assertFalse(fn("alice", upload))

    @patch(f"{HP}._send_to_user")
    @patch(f"{HP}._get_media_handler")
    @patch(f"{HP}.P2P_MEDIA_MAX_SIZE", 200 * 1024 * 1024)
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    @patch(f"{HP}._stats", {"p2p_media_quota_fallbacks": 0})
    def test_quota_exhausted_sends_p2p_caps(self, mock_handler_fn, mock_send):
        fn = self._import_fn()
        handler = MagicMock()
        quota = MagicMock()
        quota.remaining_bytes = 0  # Quota exhausted
        handler.storage.get_quota.return_value = quota
        mock_handler_fn.return_value = handler

        upload = PbMediaUpload()
        upload.size = 5000

        result = fn("alice", upload)
        self.assertTrue(result)
        mock_send.assert_called_once()

    @patch(f"{HP}._send_to_user")
    @patch(f"{HP}._get_media_handler")
    @patch(f"{HP}.ENABLE_P2P_MEDIA", True)
    @patch(f"{HP}._stats", {"p2p_media_quota_fallbacks": 0})
    def test_quota_ok_returns_false(self, mock_handler_fn, mock_send):
        fn = self._import_fn()
        handler = MagicMock()
        quota = MagicMock()
        quota.remaining_bytes = 999999  # Plenty of quota
        handler.storage.get_quota.return_value = quota
        mock_handler_fn.return_value = handler

        upload = PbMediaUpload()
        upload.size = 5000

        result = fn("alice", upload)
        self.assertFalse(result)
        mock_send.assert_not_called()


class TestRouteMediaIntegration(unittest.TestCase):
    """Test _route_media handles all 4 payload types."""

    def _import_fn(self):
        from verlihub.client.nmdcpb.hub_plugin import _route_media
        return _route_media

    @patch(f"{HP}._get_event_loop")
    @patch(f"{HP}._get_media_handler")
    @patch(f"{HP}._stats", {"media_uploads": 0, "media_deletes": 0})
    @patch(f"{HP}.ENABLE_P2P_MEDIA", False)
    def test_media_upload_dispatched(self, mock_handler_fn, mock_loop_fn):
        fn = self._import_fn()
        handler = MagicMock()
        handler.handle_media_upload = AsyncMock()
        mock_handler_fn.return_value = handler

        loop = asyncio.new_event_loop()
        mock_loop_fn.return_value = loop

        env = PbEnvelope()
        env.media_upload.filename = "test.jpg"
        fn("alice", env, "media_upload")

        handler.handle_media_upload.assert_called_once()
        loop.close()

    @patch(f"{HP}._get_event_loop")
    @patch(f"{HP}._get_media_handler")
    @patch(f"{HP}._stats", {"media_uploads": 0, "media_deletes": 0})
    def test_media_delete_dispatched(self, mock_handler_fn, mock_loop_fn):
        fn = self._import_fn()
        handler = MagicMock()
        handler.handle_media_delete = AsyncMock()
        mock_handler_fn.return_value = handler

        loop = asyncio.new_event_loop()
        mock_loop_fn.return_value = loop

        env = PbEnvelope()
        env.media_delete.media_id = "m123"
        fn("alice", env, "media_delete")

        handler.handle_media_delete.assert_called_once()
        loop.close()

    @patch(f"{HP}._get_event_loop")
    @patch(f"{HP}._get_media_handler")
    @patch(f"{HP}._stats", {"media_uploads": 0, "media_deletes": 0})
    def test_media_capabilities_dispatched(self, mock_handler_fn, mock_loop_fn):
        fn = self._import_fn()
        handler = MagicMock()
        handler.handle_media_capabilities_request = AsyncMock()
        mock_handler_fn.return_value = handler

        loop = asyncio.new_event_loop()
        mock_loop_fn.return_value = loop

        env = PbEnvelope()
        fn("alice", env, "media_capabilities")

        handler.handle_media_capabilities_request.assert_called_once_with("alice")
        loop.close()


if __name__ == "__main__":
    unittest.main()
