"""MediaHandler — hub-side media sharing request handler.

Processes PbMediaUpload, PbMediaMeta, PbMediaDelete, and
PbMediaCapabilities messages for the NMDCpb hub plugin.

Architecture:
    - Upload flow is two-stage: client sends PbMediaUpload metadata → hub
      validates → hub replies with PbMediaCapabilities (upload URL + quota)
      → client uploads binary data via HTTP POST → hub stores & replies
      with PbMediaMeta containing the permanent URL.
    - For small files (≤ INLINE_UPLOAD_LIMIT), binary data can be sent
      inside a PbRelayData frame directly, avoiding a separate HTTP upload.
    - Media expiry is enforced by a background task (called from OnTimer).
    - Quota is tracked per-user in the MediaStorage layer.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Optional

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbMediaUpload,
    PbMediaMeta,
    PbMediaDelete,
    PbMediaCapabilities,
    PbStatus,
)
from verlihub.client.nmdcpb.media_storage import (
    MediaStorage,
    MediaConfig,
    MediaMeta,
    FileSystemStorage,
    create_storage,
)
from verlihub.client.nmdcpb.wire import WireCodec

log = logging.getLogger("nmdcpb_hub.media")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# If a file is ≤ this size, it can be uploaded inline via protobuf
INLINE_UPLOAD_LIMIT = 1 * 1024 * 1024  # 1 MB

# Media expiry check interval
MEDIA_EXPIRY_INTERVAL_SEC = 300  # 5 minutes

# Stats
_media_stats = {
    "uploads": 0,
    "downloads": 0,
    "deletes": 0,
    "inline_uploads": 0,
    "expired_purged": 0,
    "quota_rejections": 0,
    "type_rejections": 0,
}


class MediaHandler:
    """Hub-side media message handler.

    Holds a reference to the storage backend and a send callback
    that the hub plugin sets up. Async methods handle individual
    protobuf media payloads.
    """

    def __init__(
        self,
        config: MediaConfig,
        send_fn,       # (wire: str, nick: str) -> None
        status_fn,     # (nick, level, code, text) -> None
        hub_url: str = "",
    ):
        self.config = config
        self.storage: MediaStorage = create_storage(config)
        self._send = send_fn
        self._status = status_fn
        self._hub_url = hub_url.rstrip("/")
        self._last_expiry_check = 0.0
        # Pending inline uploads: nick → {upload_id → PbMediaUpload}
        self._pending_uploads: dict[str, dict[str, PbMediaUpload]] = {}

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------

    async def handle_media_upload(self, sender: str, upload: PbMediaUpload) -> None:
        """Handle a media upload request from a client."""
        # Validate
        error = self.storage.validate_upload(
            upload.filename, upload.mime_type, upload.size, sender)
        if error:
            if "too large" in error or "Quota" in error:
                _media_stats["quota_rejections"] += 1
            elif "not allowed" in error:
                _media_stats["type_rejections"] += 1
            self._status(sender, PbStatus.ERROR, 40, error)
            return

        # For small files, tell client to use inline upload
        if upload.size <= INLINE_UPLOAD_LIMIT:
            # Store the pending upload metadata; expect data in relay_data
            pending_id = hashlib.sha256(
                f"{sender}:{upload.filename}:{time.time()}".encode()
            ).hexdigest()[:16]
            if sender not in self._pending_uploads:
                self._pending_uploads[sender] = {}
            self._pending_uploads[sender][pending_id] = upload
            # Send capabilities with inline upload ID
            self._send_capabilities(sender, upload_id=pending_id)
        else:
            # Large file — provide HTTP upload URL
            upload_url = f"{self._hub_url}/media/upload"
            self._send_capabilities(sender, upload_url=upload_url)

        log.info(f"Upload request from {sender}: {upload.filename} "
                 f"({upload.size} bytes, {upload.mime_type})")

    async def handle_inline_upload(
        self, sender: str, upload_id: str, data: bytes
    ) -> Optional[MediaMeta]:
        """Complete an inline upload with the actual file data."""
        pending = self._pending_uploads.get(sender, {})
        upload = pending.pop(upload_id, None)
        if not pending:
            self._pending_uploads.pop(sender, None)

        if upload is None:
            self._status(sender, PbStatus.ERROR, 41,
                         f"No pending upload with id {upload_id}")
            return None

        # Verify size matches
        if len(data) != upload.size:
            self._status(sender, PbStatus.ERROR, 42,
                         f"Size mismatch: expected {upload.size}, got {len(data)}")
            return None

        # Verify checksum if provided
        if upload.checksum_sha256:
            actual = hashlib.sha256(data).hexdigest()
            if actual != upload.checksum_sha256:
                self._status(sender, PbStatus.ERROR, 43,
                             "Checksum mismatch")
                return None

        # Store
        meta = await self.storage.store(
            data=data,
            filename=upload.filename,
            mime_type=upload.mime_type,
            uploader=sender,
            ttl=upload.requested_ttl,
            is_encrypted=upload.is_encrypted,
            checksum=upload.checksum_sha256,
        )

        # Send PbMediaMeta back to uploader
        self._send_media_meta(sender, meta)
        _media_stats["inline_uploads"] += 1
        _media_stats["uploads"] += 1
        return meta

    async def handle_media_meta_request(
        self, sender: str, media_id: str
    ) -> None:
        """Handle a media metadata request (client requests info about a file)."""
        meta = await self.storage.get_meta(media_id)
        if meta is None:
            self._status(sender, PbStatus.ERROR, 44,
                         f"Media not found: {media_id}")
            return
        if meta.is_expired:
            self._status(sender, PbStatus.ERROR, 45,
                         f"Media expired: {media_id}")
            return
        self._send_media_meta(sender, meta)
        _media_stats["downloads"] += 1

    async def handle_media_delete(
        self, sender: str, delete: PbMediaDelete
    ) -> None:
        """Handle a media delete request."""
        meta = await self.storage.get_meta(delete.media_id)
        if meta is None:
            self._status(sender, PbStatus.ERROR, 44,
                         f"Media not found: {delete.media_id}")
            return

        # Only uploader or hub operator can delete
        if meta.uploader_nick != sender:
            self._status(sender, PbStatus.ERROR, 46,
                         "Only the uploader can delete this media")
            return

        ok = await self.storage.delete(delete.media_id)
        if ok:
            self._status(sender, PbStatus.INFO, 0,
                         f"Deleted media {delete.media_id}")
            _media_stats["deletes"] += 1
        else:
            self._status(sender, PbStatus.ERROR, 47,
                         f"Failed to delete media {delete.media_id}")

    async def handle_media_capabilities_request(self, sender: str) -> None:
        """Handle a capabilities request — send current config & quota."""
        self._send_capabilities(sender)

    # ------------------------------------------------------------------
    # Operator commands
    # ------------------------------------------------------------------

    async def handle_operator_delete(
        self, operator: str, media_id: str, reason: str = ""
    ) -> None:
        """Allow operators to delete any media."""
        meta = await self.storage.get_meta(media_id)
        if meta is None:
            self._status(operator, PbStatus.ERROR, 44,
                         f"Media not found: {media_id}")
            return

        ok = await self.storage.delete(media_id)
        if ok:
            # Notify uploader if still online
            log.info(f"Operator {operator} deleted media {media_id} "
                     f"(uploader: {meta.uploader_nick}, reason: {reason})")
            _media_stats["deletes"] += 1

    def get_stats(self) -> dict:
        """Return media handler statistics."""
        return dict(_media_stats)

    # ------------------------------------------------------------------
    # Periodic maintenance
    # ------------------------------------------------------------------

    async def check_expiry(self) -> int:
        """Purge expired media. Call from OnTimer."""
        now = time.time()
        if now - self._last_expiry_check < MEDIA_EXPIRY_INTERVAL_SEC:
            return 0
        self._last_expiry_check = now

        if hasattr(self.storage, "purge_expired"):
            count = await self.storage.purge_expired()
            _media_stats["expired_purged"] += count
            return count
        return 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_capabilities(
        self,
        nick: str,
        upload_id: str = "",
        upload_url: str = "",
    ) -> None:
        """Send PbMediaCapabilities to a user."""
        quota = self.storage.get_quota(nick)
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="",
            to_nick=nick,
        )
        caps = env.media_capabilities
        caps.enabled = self.config.enabled
        caps.max_file_size = self.config.max_file_size
        caps.user_quota_remaining = quota.remaining_bytes
        caps.max_ttl = self.config.max_ttl
        caps.default_ttl = self.config.default_ttl
        for t in self.config.allowed_types:
            caps.allowed_types.append(t)
        caps.thumbnails_available = self.config.thumbnails_enabled
        if upload_url:
            caps.upload_url = upload_url
        elif upload_id:
            # Inline upload: upload_url carries the pending upload ID
            caps.upload_url = f"inline:{upload_id}"
        env.timestamp = int(time.time() * 1000)
        wire = WireCodec.encode_text(env)
        self._send(wire, nick)

    def _send_media_meta(self, nick: str, meta: MediaMeta) -> None:
        """Send PbMediaMeta to a user."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="",
            to_nick=nick,
        )
        m = env.media_meta
        m.media_id = meta.media_id
        m.url = self._media_url(meta.media_id, "data.bin")
        if meta.thumbnail_path:
            m.thumbnail_url = self._media_url(meta.media_id, "thumb.jpg")
        m.mime_type = meta.mime_type
        m.size = meta.size
        m.filename = meta.filename
        m.expires_at = int(meta.expires_at * 1000)
        m.uploader_nick = meta.uploader_nick
        m.width = meta.width
        m.height = meta.height
        m.duration_ms = meta.duration_ms
        m.checksum_sha256 = meta.checksum_sha256
        env.timestamp = int(time.time() * 1000)
        wire = WireCodec.encode_text(env)
        self._send(wire, nick)

    def _media_url(self, media_id: str, name: str) -> str:
        """Build a media file URL."""
        if self._hub_url:
            return f"{self._hub_url}/media/{media_id}/{name}"
        return f"/media/{media_id}/{name}"
