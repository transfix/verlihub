"""MediaStorage — abstraction for media file storage backends.

Provides a common interface for storing and retrieving media files
(images, video, audio, documents) uploaded by hub users. Two backends:

1. **FileSystemStorage** — local disk storage (default, zero dependencies)
2. **S3Storage** — S3-compatible object storage (requires boto3)

Usage::

    from verlihub.client.nmdcpb.media_storage import FileSystemStorage

    storage = FileSystemStorage("/var/lib/verlihub/media")
    media_id = await storage.store(data, "image.png", "image/png", uploader="Alice")
    meta = await storage.get_meta(media_id)
    data = await storage.retrieve(media_id)
    await storage.delete(media_id)

Architecture:
    - Each stored file gets a unique `media_id` (UUID)
    - Metadata is persisted alongside binary data
    - Thumbnails are generated on upload for images (optional, requires Pillow)
    - Expiry times are tracked per-file; a background daemon prunes expired media
    - Per-user quota tracking prevents abuse
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class MediaMeta:
    """Metadata for a stored media file."""
    media_id: str = ""
    filename: str = ""
    mime_type: str = ""
    size: int = 0
    checksum_sha256: str = ""
    uploader_nick: str = ""
    uploaded_at: float = 0.0
    expires_at: float = 0.0
    is_encrypted: bool = False
    width: int = 0
    height: int = 0
    duration_ms: int = 0
    thumbnail_path: str = ""     # Relative path to thumbnail
    storage_path: str = ""       # Internal path / S3 key

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")

    @property
    def is_video(self) -> bool:
        return self.mime_type.startswith("video/")

    @property
    def is_audio(self) -> bool:
        return self.mime_type.startswith("audio/")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MediaMeta":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class MediaQuota:
    """Per-user storage quota tracking."""
    nick: str = ""
    used_bytes: int = 0
    file_count: int = 0
    max_bytes: int = 0
    max_files: int = 0

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.max_bytes - self.used_bytes)

    @property
    def remaining_files(self) -> int:
        return max(0, self.max_files - self.file_count)

    def can_store(self, size: int) -> bool:
        if self.max_bytes > 0 and self.used_bytes + size > self.max_bytes:
            return False
        if self.max_files > 0 and self.file_count >= self.max_files:
            return False
        return True


# =========================================================================
# Configuration
# =========================================================================

@dataclass
class MediaConfig:
    """Hub-wide media sharing configuration."""
    enabled: bool = True
    storage_backend: str = "filesystem"       # "filesystem" or "s3"
    storage_path: str = "/var/lib/verlihub/media"
    max_file_size: int = 50 * 1024 * 1024     # 50 MB
    default_ttl: int = 7 * 86400              # 7 days
    max_ttl: int = 30 * 86400                 # 30 days
    per_user_quota: int = 500 * 1024 * 1024   # 500 MB per user
    per_user_max_files: int = 200
    thumbnails_enabled: bool = True
    thumbnail_max_size: int = 256             # max dimension in pixels
    allowed_types: list[str] = field(default_factory=lambda: [
        "image/jpeg", "image/png", "image/gif", "image/webp",
        "video/mp4", "video/webm",
        "audio/mpeg", "audio/ogg", "audio/opus",
        "application/pdf",
        "text/plain",
    ])
    # S3 settings
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_public_url: str = ""

    def is_type_allowed(self, mime_type: str) -> bool:
        if not self.allowed_types:
            return True  # No restriction
        # Allow if prefix matches (e.g., "image/*")
        for allowed in self.allowed_types:
            if allowed.endswith("/*"):
                if mime_type.startswith(allowed[:-1]):
                    return True
            elif mime_type == allowed:
                return True
        return False


# =========================================================================
# Abstract base
# =========================================================================

class MediaStorage(ABC):
    """Abstract media storage backend."""

    def __init__(self, config: MediaConfig):
        self.config = config
        self._quotas: dict[str, MediaQuota] = {}

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def store(
        self,
        data: bytes,
        filename: str,
        mime_type: str,
        uploader: str,
        ttl: int = 0,
        is_encrypted: bool = False,
        checksum: str = "",
    ) -> MediaMeta:
        """Store a media file. Returns metadata with media_id and URLs."""
        ...

    @abstractmethod
    async def retrieve(self, media_id: str) -> Optional[bytes]:
        """Retrieve file data by media_id. Returns None if not found."""
        ...

    @abstractmethod
    async def get_meta(self, media_id: str) -> Optional[MediaMeta]:
        """Get metadata for a media file."""
        ...

    @abstractmethod
    async def delete(self, media_id: str) -> bool:
        """Delete a media file. Returns True if deleted."""
        ...

    @abstractmethod
    async def get_thumbnail(self, media_id: str) -> Optional[bytes]:
        """Get thumbnail data for a media file. Returns None if no thumbnail."""
        ...

    @abstractmethod
    async def list_expired(self) -> list[str]:
        """Return media_ids of expired files."""
        ...

    @abstractmethod
    async def list_by_user(self, nick: str) -> list[MediaMeta]:
        """List all media uploaded by a user."""
        ...

    # ------------------------------------------------------------------
    # Quota management
    # ------------------------------------------------------------------

    def get_quota(self, nick: str) -> MediaQuota:
        """Get current quota for a user."""
        if nick not in self._quotas:
            self._quotas[nick] = MediaQuota(
                nick=nick,
                max_bytes=self.config.per_user_quota,
                max_files=self.config.per_user_max_files,
            )
        return self._quotas[nick]

    def _update_quota(self, nick: str, size_delta: int, count_delta: int) -> None:
        """Adjust a user's quota tracking."""
        q = self.get_quota(nick)
        q.used_bytes = max(0, q.used_bytes + size_delta)
        q.file_count = max(0, q.file_count + count_delta)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_upload(self, filename: str, mime_type: str, size: int,
                        uploader: str) -> Optional[str]:
        """Validate an upload request. Returns error message or None if OK."""
        if not self.config.enabled:
            return "Media sharing is disabled"
        if size > self.config.max_file_size:
            return (f"File too large: {size} bytes "
                    f"(max {self.config.max_file_size})")
        if not self.config.is_type_allowed(mime_type):
            return f"File type not allowed: {mime_type}"
        quota = self.get_quota(uploader)
        if not quota.can_store(size):
            return (f"Quota exceeded: {quota.used_bytes}/{quota.max_bytes} bytes, "
                    f"{quota.file_count}/{quota.max_files} files")
        return None

    # ------------------------------------------------------------------
    # Thumbnail generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_thumbnail(data: bytes, mime_type: str,
                           max_size: int = 256) -> Optional[bytes]:
        """Generate a JPEG thumbnail for an image. Returns None on failure."""
        if not mime_type.startswith("image/"):
            return None
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            # Convert to RGB if necessary (e.g., RGBA PNGs)
            if img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue()
        except ImportError:
            log.debug("Pillow not installed — thumbnail generation skipped")
            return None
        except Exception as e:
            log.warning(f"Thumbnail generation failed: {e}")
            return None

    @staticmethod
    def get_image_dimensions(data: bytes, mime_type: str) -> tuple[int, int]:
        """Get (width, height) of an image. Returns (0, 0) on failure."""
        if not mime_type.startswith("image/"):
            return 0, 0
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            return img.size
        except Exception:
            return 0, 0


# =========================================================================
# FileSystemStorage
# =========================================================================

class FileSystemStorage(MediaStorage):
    """Store media files on the local filesystem.

    Directory structure::

        {storage_path}/
            {media_id[:2]}/          # 2-char prefix for sharding
                {media_id}/
                    data.bin         # Raw file data
                    meta.json        # Metadata
                    thumb.jpg        # Thumbnail (optional)
    """

    def __init__(self, config: MediaConfig):
        super().__init__(config)
        self._base = Path(config.storage_path)
        os.makedirs(self._base, exist_ok=True)

    def _media_dir(self, media_id: str) -> Path:
        """Get the directory for a media file."""
        return self._base / media_id[:2] / media_id

    async def store(
        self,
        data: bytes,
        filename: str,
        mime_type: str,
        uploader: str,
        ttl: int = 0,
        is_encrypted: bool = False,
        checksum: str = "",
    ) -> MediaMeta:
        media_id = uuid.uuid4().hex
        now = time.time()
        ttl = ttl or self.config.default_ttl
        ttl = min(ttl, self.config.max_ttl)

        # Compute checksum if not provided
        if not checksum:
            checksum = hashlib.sha256(data).hexdigest()

        # Get image dimensions
        width, height = self.get_image_dimensions(data, mime_type)

        # Create directory
        media_dir = self._media_dir(media_id)
        await asyncio.to_thread(os.makedirs, media_dir, exist_ok=True)

        # Write data
        data_path = media_dir / "data.bin"
        await asyncio.to_thread(data_path.write_bytes, data)

        # Generate thumbnail
        thumb_path = ""
        if self.config.thumbnails_enabled:
            thumb_data = self.generate_thumbnail(
                data, mime_type, self.config.thumbnail_max_size)
            if thumb_data:
                thumb_file = media_dir / "thumb.jpg"
                await asyncio.to_thread(thumb_file.write_bytes, thumb_data)
                thumb_path = "thumb.jpg"

        # Build metadata
        meta = MediaMeta(
            media_id=media_id,
            filename=filename,
            mime_type=mime_type,
            size=len(data),
            checksum_sha256=checksum,
            uploader_nick=uploader,
            uploaded_at=now,
            expires_at=now + ttl,
            is_encrypted=is_encrypted,
            width=width,
            height=height,
            thumbnail_path=thumb_path,
            storage_path=str(data_path.relative_to(self._base)),
        )

        # Write metadata
        meta_path = media_dir / "meta.json"
        await asyncio.to_thread(
            meta_path.write_text,
            json.dumps(meta.to_dict(), indent=2),
        )

        # Update quota
        self._update_quota(uploader, len(data), 1)

        log.info(f"Stored media {media_id}: {filename} ({len(data)} bytes) "
                 f"by {uploader}, expires {time.ctime(meta.expires_at)}")
        return meta

    async def retrieve(self, media_id: str) -> Optional[bytes]:
        data_path = self._media_dir(media_id) / "data.bin"
        if not data_path.exists():
            return None
        return await asyncio.to_thread(data_path.read_bytes)

    async def get_meta(self, media_id: str) -> Optional[MediaMeta]:
        meta_path = self._media_dir(media_id) / "meta.json"
        if not meta_path.exists():
            return None
        text = await asyncio.to_thread(meta_path.read_text)
        return MediaMeta.from_dict(json.loads(text))

    async def delete(self, media_id: str) -> bool:
        media_dir = self._media_dir(media_id)
        if not media_dir.exists():
            return False

        # Load meta for quota adjustment
        meta = await self.get_meta(media_id)
        if meta:
            self._update_quota(meta.uploader_nick, -meta.size, -1)

        await asyncio.to_thread(shutil.rmtree, media_dir, ignore_errors=True)
        log.info(f"Deleted media {media_id}")
        return True

    async def get_thumbnail(self, media_id: str) -> Optional[bytes]:
        thumb_path = self._media_dir(media_id) / "thumb.jpg"
        if not thumb_path.exists():
            return None
        return await asyncio.to_thread(thumb_path.read_bytes)

    async def list_expired(self) -> list[str]:
        expired = []
        now = time.time()
        # Scan all media directories
        if not self._base.exists():
            return expired
        for prefix_dir in self._base.iterdir():
            if not prefix_dir.is_dir() or len(prefix_dir.name) != 2:
                continue
            for media_dir in prefix_dir.iterdir():
                meta_path = media_dir / "meta.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = MediaMeta.from_dict(
                        json.loads(meta_path.read_text()))
                    if meta.expires_at > 0 and now > meta.expires_at:
                        expired.append(meta.media_id)
                except Exception:
                    continue
        return expired

    async def list_by_user(self, nick: str) -> list[MediaMeta]:
        result = []
        if not self._base.exists():
            return result
        for prefix_dir in self._base.iterdir():
            if not prefix_dir.is_dir() or len(prefix_dir.name) != 2:
                continue
            for media_dir in prefix_dir.iterdir():
                meta_path = media_dir / "meta.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = MediaMeta.from_dict(
                        json.loads(meta_path.read_text()))
                    if meta.uploader_nick == nick:
                        result.append(meta)
                except Exception:
                    continue
        return result

    async def purge_expired(self) -> int:
        """Delete all expired media. Returns count of deleted files."""
        expired = await self.list_expired()
        for media_id in expired:
            await self.delete(media_id)
        if expired:
            log.info(f"Purged {len(expired)} expired media files")
        return len(expired)


# =========================================================================
# S3Storage
# =========================================================================

class S3Storage(MediaStorage):
    """Store media files in S3-compatible object storage.

    Requires ``boto3``. Each file is stored as::

        {bucket}/{media_id[:2]}/{media_id}/data.bin
        {bucket}/{media_id[:2]}/{media_id}/meta.json
        {bucket}/{media_id[:2]}/{media_id}/thumb.jpg   (optional)
    """

    def __init__(self, config: MediaConfig):
        super().__init__(config)
        try:
            import boto3
        except ImportError:
            raise ImportError("S3Storage requires boto3: pip install boto3")

        kwargs = {"region_name": config.s3_region}
        if config.s3_endpoint:
            kwargs["endpoint_url"] = config.s3_endpoint
        if config.s3_access_key:
            kwargs["aws_access_key_id"] = config.s3_access_key
            kwargs["aws_secret_access_key"] = config.s3_secret_key

        self._s3 = boto3.client("s3", **kwargs)
        self._bucket = config.s3_bucket
        self._public_url = config.s3_public_url.rstrip("/")

    def _key(self, media_id: str, name: str) -> str:
        return f"{media_id[:2]}/{media_id}/{name}"

    def _url(self, media_id: str, name: str) -> str:
        if self._public_url:
            return f"{self._public_url}/{self._key(media_id, name)}"
        return f"https://{self._bucket}.s3.amazonaws.com/{self._key(media_id, name)}"

    async def store(
        self,
        data: bytes,
        filename: str,
        mime_type: str,
        uploader: str,
        ttl: int = 0,
        is_encrypted: bool = False,
        checksum: str = "",
    ) -> MediaMeta:
        media_id = uuid.uuid4().hex
        now = time.time()
        ttl = ttl or self.config.default_ttl
        ttl = min(ttl, self.config.max_ttl)

        if not checksum:
            checksum = hashlib.sha256(data).hexdigest()

        width, height = self.get_image_dimensions(data, mime_type)

        # Upload data
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=self._key(media_id, "data.bin"),
            Body=data,
            ContentType=mime_type,
        )

        # Generate + upload thumbnail
        thumb_path = ""
        if self.config.thumbnails_enabled:
            thumb_data = self.generate_thumbnail(
                data, mime_type, self.config.thumbnail_max_size)
            if thumb_data:
                await asyncio.to_thread(
                    self._s3.put_object,
                    Bucket=self._bucket,
                    Key=self._key(media_id, "thumb.jpg"),
                    Body=thumb_data,
                    ContentType="image/jpeg",
                )
                thumb_path = "thumb.jpg"

        meta = MediaMeta(
            media_id=media_id,
            filename=filename,
            mime_type=mime_type,
            size=len(data),
            checksum_sha256=checksum,
            uploader_nick=uploader,
            uploaded_at=now,
            expires_at=now + ttl,
            is_encrypted=is_encrypted,
            width=width,
            height=height,
            thumbnail_path=thumb_path,
            storage_path=self._key(media_id, "data.bin"),
        )

        # Upload metadata
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=self._key(media_id, "meta.json"),
            Body=json.dumps(meta.to_dict(), indent=2).encode(),
            ContentType="application/json",
        )

        self._update_quota(uploader, len(data), 1)

        log.info(f"Stored media {media_id} in S3: {filename} ({len(data)} bytes)")
        return meta

    async def retrieve(self, media_id: str) -> Optional[bytes]:
        try:
            resp = await asyncio.to_thread(
                self._s3.get_object,
                Bucket=self._bucket,
                Key=self._key(media_id, "data.bin"),
            )
            return resp["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            return None
        except Exception:
            return None

    async def get_meta(self, media_id: str) -> Optional[MediaMeta]:
        try:
            resp = await asyncio.to_thread(
                self._s3.get_object,
                Bucket=self._bucket,
                Key=self._key(media_id, "meta.json"),
            )
            return MediaMeta.from_dict(json.loads(resp["Body"].read()))
        except Exception:
            return None

    async def delete(self, media_id: str) -> bool:
        meta = await self.get_meta(media_id)
        if meta:
            self._update_quota(meta.uploader_nick, -meta.size, -1)

        objects = [
            {"Key": self._key(media_id, "data.bin")},
            {"Key": self._key(media_id, "meta.json")},
            {"Key": self._key(media_id, "thumb.jpg")},
        ]
        try:
            await asyncio.to_thread(
                self._s3.delete_objects,
                Bucket=self._bucket,
                Delete={"Objects": objects},
            )
            log.info(f"Deleted media {media_id} from S3")
            return True
        except Exception as e:
            log.warning(f"S3 delete failed for {media_id}: {e}")
            return False

    async def get_thumbnail(self, media_id: str) -> Optional[bytes]:
        try:
            resp = await asyncio.to_thread(
                self._s3.get_object,
                Bucket=self._bucket,
                Key=self._key(media_id, "thumb.jpg"),
            )
            return resp["Body"].read()
        except Exception:
            return None

    async def list_expired(self) -> list[str]:
        # S3 listing is expensive; in production use lifecycle rules
        # or maintain a separate index. This is a fallback for small deployments.
        expired = []
        now = time.time()
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=self._bucket, Prefix="", Delimiter="/"
            ):
                for prefix in page.get("CommonPrefixes", []):
                    # List items under each 2-char prefix
                    for inner_page in paginator.paginate(
                        Bucket=self._bucket,
                        Prefix=prefix["Prefix"],
                        Delimiter="/",
                    ):
                        for inner_prefix in inner_page.get("CommonPrefixes", []):
                            meta_key = inner_prefix["Prefix"] + "meta.json"
                            try:
                                resp = self._s3.get_object(
                                    Bucket=self._bucket, Key=meta_key)
                                meta = MediaMeta.from_dict(
                                    json.loads(resp["Body"].read()))
                                if meta.expires_at > 0 and now > meta.expires_at:
                                    expired.append(meta.media_id)
                            except Exception:
                                continue
        except Exception as e:
            log.warning(f"S3 list_expired error: {e}")
        return expired

    async def list_by_user(self, nick: str) -> list[MediaMeta]:
        # Similar to list_expired — scan all meta.json
        result = []
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=self._bucket, Prefix="", Delimiter="/"
            ):
                for prefix in page.get("CommonPrefixes", []):
                    for inner_page in paginator.paginate(
                        Bucket=self._bucket,
                        Prefix=prefix["Prefix"],
                        Delimiter="/",
                    ):
                        for inner_prefix in inner_page.get("CommonPrefixes", []):
                            meta_key = inner_prefix["Prefix"] + "meta.json"
                            try:
                                resp = self._s3.get_object(
                                    Bucket=self._bucket, Key=meta_key)
                                meta = MediaMeta.from_dict(
                                    json.loads(resp["Body"].read()))
                                if meta.uploader_nick == nick:
                                    result.append(meta)
                            except Exception:
                                continue
        except Exception as e:
            log.warning(f"S3 list_by_user error: {e}")
        return result

    async def purge_expired(self) -> int:
        expired = await self.list_expired()
        for media_id in expired:
            await self.delete(media_id)
        if expired:
            log.info(f"Purged {len(expired)} expired media files from S3")
        return len(expired)


# =========================================================================
# Factory
# =========================================================================

def create_storage(config: MediaConfig) -> MediaStorage:
    """Create a media storage backend from configuration."""
    if config.storage_backend == "s3":
        return S3Storage(config)
    return FileSystemStorage(config)
