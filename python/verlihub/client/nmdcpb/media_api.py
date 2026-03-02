"""Media HTTP API — FastAPI endpoints for NMDCpb media upload/download.

Provides:
  POST   /api/media/upload   — multipart file upload (session token auth)
  GET    /api/media/{id}     — download media file
  GET    /api/media/{id}/thumb — download thumbnail
  GET    /api/media/{id}/meta  — get metadata JSON
  DELETE /api/media/{id}     — delete media (owner or admin)
  GET    /api/media/quota    — get user quota info

Authentication uses Bearer tokens issued during NMDCpb session negotiation.
Tokens are short-lived and map to nick + hub session.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional

try:
    from fastapi import (
        APIRouter, Depends, File, HTTPException, Header, Response, UploadFile,
    )
    from fastapi.responses import FileResponse, JSONResponse
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from verlihub.client.nmdcpb.media_storage import MediaStorage, MediaConfig, MediaMeta

log = logging.getLogger("nmdcpb_hub.media_api")

# ---------------------------------------------------------------------------
# Session token management
# ---------------------------------------------------------------------------

# token → SessionInfo
_active_sessions: dict[str, "SessionInfo"] = {}

# HMAC key for token generation (set via configure())
_token_secret: bytes = b""

# Token lifetime
TOKEN_TTL_SEC = 3600  # 1 hour default


class SessionInfo:
    """Tracks an authenticated media API session."""
    __slots__ = ("nick", "ip", "created_at", "expires_at", "is_admin")

    def __init__(self, nick: str, ip: str = "", is_admin: bool = False,
                 ttl: int = TOKEN_TTL_SEC):
        self.nick = nick
        self.ip = ip
        self.is_admin = is_admin
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


def generate_session_token(nick: str, ip: str = "",
                           is_admin: bool = False) -> str:
    """Generate a new session token for a NMDCpb user.

    Called by the hub plugin during PbHubInfo / capabilities exchange.
    Returns a hex token string.
    """
    raw = secrets.token_bytes(24)
    # HMAC-SHA256 binds the token to our secret
    if _token_secret:
        mac = hmac.new(_token_secret, raw, hashlib.sha256).hexdigest()[:32]
    else:
        mac = raw.hex()[:32]

    token = f"nmdcpb_{mac}"
    _active_sessions[token] = SessionInfo(nick, ip, is_admin)
    log.debug(f"Session token issued for {nick} (ip={ip})")
    return token


def revoke_session_token(token: str) -> None:
    """Revoke a session token (e.g., on logout)."""
    _active_sessions.pop(token, None)


def revoke_sessions_for_nick(nick: str) -> int:
    """Revoke all sessions for a user (e.g., on disconnect)."""
    to_remove = [t for t, s in _active_sessions.items() if s.nick == nick]
    for t in to_remove:
        del _active_sessions[t]
    return len(to_remove)


def prune_expired_sessions() -> int:
    """Remove expired session tokens. Call periodically."""
    now = time.time()
    expired = [t for t, s in _active_sessions.items() if s.expires_at < now]
    for t in expired:
        del _active_sessions[t]
    return len(expired)


def validate_token(token: str) -> Optional[SessionInfo]:
    """Validate a session token. Returns SessionInfo or None."""
    session = _active_sessions.get(token)
    if session is None:
        return None
    if session.is_expired:
        _active_sessions.pop(token, None)
        return None
    return session


def configure(secret: bytes | str = b"", token_ttl: int = 3600) -> None:
    """Configure the media API authentication.

    Args:
        secret: HMAC key for token generation. If empty, random.
        token_ttl: Token lifetime in seconds.
    """
    global _token_secret, TOKEN_TTL_SEC
    if isinstance(secret, str):
        secret = secret.encode()
    _token_secret = secret or secrets.token_bytes(32)
    TOKEN_TTL_SEC = token_ttl


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

if FASTAPI_AVAILABLE:
    router = APIRouter(prefix="/api/media", tags=["media"])

    # Shared storage reference — set by the hub plugin
    _storage: Optional[MediaStorage] = None
    _media_meta_callback = None  # (nick, MediaMeta) -> None

    def set_storage(storage: MediaStorage) -> None:
        """Set the media storage backend for the API."""
        global _storage
        _storage = storage

    def set_meta_callback(cb) -> None:
        """Set callback to send PbMediaMeta to the uploader."""
        global _media_meta_callback
        _media_meta_callback = cb

    async def _get_session(
        authorization: str = Header(default=""),
    ) -> SessionInfo:
        """Dependency: extract and validate Bearer token."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        token = authorization[7:].strip()
        session = validate_token(token)
        if session is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return session

    @router.post("/upload")
    async def upload_media(
        file: UploadFile = File(...),
        session: SessionInfo = Depends(_get_session),
    ):
        """Upload a media file.

        Accepts multipart form data with a single file field.
        Returns PbMediaMeta-like JSON on success.
        """
        if _storage is None:
            raise HTTPException(status_code=503, detail="Media storage not configured")

        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty file")

        filename = file.filename or "upload.bin"
        mime_type = file.content_type or "application/octet-stream"

        # Validate via storage backend
        error = _storage.validate_upload(
            filename, mime_type, len(data), session.nick)
        if error:
            raise HTTPException(status_code=400, detail=error)

        checksum = hashlib.sha256(data).hexdigest()

        meta = await _storage.store(
            data=data,
            filename=filename,
            mime_type=mime_type,
            uploader=session.nick,
            ttl=0,  # use default
            is_encrypted=False,
            checksum=checksum,
        )

        # Notify via protobuf callback if set
        if _media_meta_callback:
            _media_meta_callback(session.nick, meta)

        return JSONResponse(content={
            "media_id": meta.media_id,
            "filename": meta.filename,
            "mime_type": meta.mime_type,
            "size": meta.size,
            "checksum_sha256": meta.checksum_sha256,
            "expires_at": int(meta.expires_at),
            "uploader": meta.uploader_nick,
        }, status_code=201)

    @router.get("/quota", name="get_quota")
    async def get_quota(
        session: SessionInfo = Depends(_get_session),
    ):
        """Get current user's storage quota."""
        if _storage is None:
            raise HTTPException(status_code=503, detail="Media storage not configured")

        quota = _storage.get_quota(session.nick)
        return JSONResponse(content={
            "nick": session.nick,
            "used_bytes": quota.used_bytes,
            "remaining_bytes": quota.remaining_bytes,
            "max_bytes": quota.max_bytes,
        })

    @router.get("/{media_id}")
    async def download_media(
        media_id: str,
        session: SessionInfo = Depends(_get_session),
    ):
        """Download a media file by ID."""
        if _storage is None:
            raise HTTPException(status_code=503, detail="Media storage not configured")

        meta = await _storage.get_meta(media_id)
        if meta is None or meta.is_expired:
            raise HTTPException(status_code=404, detail="Media not found")

        data = await _storage.retrieve(media_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Media file missing")

        return Response(
            content=data,
            media_type=meta.mime_type,
            headers={"Content-Disposition": f'attachment; filename="{meta.filename}"'},
        )

    @router.get("/{media_id}/thumb")
    async def download_thumbnail(
        media_id: str,
        session: SessionInfo = Depends(_get_session),
    ):
        """Download thumbnail for a media item."""
        if _storage is None:
            raise HTTPException(status_code=503, detail="Media storage not configured")

        meta = await _storage.get_meta(media_id)
        if meta is None or meta.is_expired:
            raise HTTPException(status_code=404, detail="Media not found")

        if not meta.thumbnail_path or not os.path.isfile(meta.thumbnail_path):
            raise HTTPException(status_code=404, detail="No thumbnail available")

        return FileResponse(
            meta.thumbnail_path,
            media_type="image/jpeg",
            filename=f"thumb_{meta.filename}.jpg",
        )

    @router.get("/{media_id}/meta")
    async def get_media_meta(
        media_id: str,
        session: SessionInfo = Depends(_get_session),
    ):
        """Get metadata for a media item."""
        if _storage is None:
            raise HTTPException(status_code=503, detail="Media storage not configured")

        meta = await _storage.get_meta(media_id)
        if meta is None or meta.is_expired:
            raise HTTPException(status_code=404, detail="Media not found")

        return JSONResponse(content={
            "media_id": meta.media_id,
            "filename": meta.filename,
            "mime_type": meta.mime_type,
            "size": meta.size,
            "width": meta.width,
            "height": meta.height,
            "duration_ms": meta.duration_ms,
            "checksum_sha256": meta.checksum_sha256,
            "uploader": meta.uploader_nick,
            "expires_at": int(meta.expires_at),
        })

    @router.delete("/{media_id}")
    async def delete_media(
        media_id: str,
        session: SessionInfo = Depends(_get_session),
    ):
        """Delete a media item (owner or admin only)."""
        if _storage is None:
            raise HTTPException(status_code=503, detail="Media storage not configured")

        meta = await _storage.get_meta(media_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Media not found")

        if meta.uploader_nick != session.nick and not session.is_admin:
            raise HTTPException(status_code=403, detail="Not authorized")

        ok = await _storage.delete(media_id)
        if not ok:
            raise HTTPException(status_code=500, detail="Delete failed")

        return JSONResponse(content={"deleted": media_id})

else:
    router = None  # type: ignore
