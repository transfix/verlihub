"""
NMDCpb Hub Plugin for Verlihub
==============================

A Verlihub Python plugin that adds NMDCpb protobuf extension support to
the hub. Handles feature negotiation, protobuf message routing, and
legacy client translation.

Load with:  !pyload /path/to/hub_plugin.py

Phase 0 prototype — validates the NMDCpb wire protocol design with a
real NMDC hub.

This module can also be used as a reference for the hub-side NMDCpb
routing logic.
"""

import sys
import os
import asyncio
import time
import logging

try:
    import vh
except ImportError:
    # Running outside verlihub (for testing)
    vh = None  # type: ignore

from verlihub.client.nmdcpb.wire import WireCodec, FEATURE_NMDCPB, FEATURE_HUBRELAY
from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope, PbChat, PbStatus,
    PbRelayRequest, PbRelayAck, PbRelayClosed, PbRelayStatus, PbRelayResume,
    PbPrivateSearch, PbPrivateSearchResult,
    PbUserQueryResult,
    PbMediaUpload, PbMediaMeta, PbMediaDelete, PbMediaCapabilities,
    PbP2PMediaRef, PbP2PMediaStatus,
    PbHubStream,
)
from verlihub.client.nmdcpb.media_storage import MediaConfig
from verlihub.client.nmdcpb.media_handler import MediaHandler
from verlihub.client.nmdcpb.media_api import (
    generate_session_token, revoke_sessions_for_nick,
    prune_expired_sessions, configure as configure_media_api,
)
from verlihub.client.nmdcpb.channel_manager import (
    ChannelManager, ChannelConfig, GENERAL_CHANNEL_ID,
)
from verlihub.client.nmdcpb.call_manager import (
    CallManager, CallConfig, StreamManager, StreamConfig,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VERSION = "0.3.0"
LOG_LEVEL = logging.DEBUG

# Feature flags
ENABLE_LEGACY_TRANSLATION = True   # Translate PB chat → legacy <nick> text
ENABLE_HUBRELAY = True             # Hub relay for passive-to-passive transfers
ENABLE_E2EPM_FORWARD = True        # Opaque forward of E2EPM messages
ENABLE_STEALTH_SEARCH = True       # Hub user-query / stealth sweep search
MAX_PB_SIZE = 65536                # Max protobuf wire frame size

# Rate limiting
RATE_WINDOW_SEC = 10               # Sliding window duration
RATE_MAX_MESSAGES = 30             # Max PB messages per window per user
RATE_MAX_E2EPM = 10                # Max E2EPM ops per window per user
RATE_FLOOD_BAN_SEC = 60            # Temp-mute duration on flood detection

# Session cleanup
SESSION_IDLE_SEC = 300             # Idle user entries kept this long after disconnect

# Relay limits
RELAY_MAX_SESSIONS_PER_USER = 3    # Max concurrent relay sessions per user
RELAY_MAX_SESSIONS_TOTAL = 50      # Hub-wide max concurrent relay sessions
RELAY_IDLE_TIMEOUT_SEC = 60        # Close idle relay sessions after this
RELAY_MAX_PAYLOAD = 65536          # Max relay data frame size

# Stealth search limits
STEALTH_MAX_RESULTS = 100          # Max nicks returned in user query
STEALTH_MAX_SWEEP_TARGETS = 50     # Max users to forward search to in one sweep

# MediaShare settings
ENABLE_MEDIASHARE = True            # Enable media upload/download
MEDIA_STORAGE_PATH = "/var/lib/verlihub/media"
MEDIA_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MEDIA_DEFAULT_TTL = 7 * 86400       # 7 days
MEDIA_MAX_TTL = 30 * 86400          # 30 days
MEDIA_PER_USER_QUOTA = 500 * 1024 * 1024  # 500 MB
MEDIA_HUB_URL = ""                  # Public base URL for media access

# P2P MediaShare settings
ENABLE_P2P_MEDIA = True             # Enable P2P media sharing
P2P_MEDIA_DEFAULT = False           # When True, prefer P2P over hub-hosted
P2P_MEDIA_MAX_SIZE = 200 * 1024 * 1024  # 200 MB max P2P file size
P2P_STATUS_RATE_LIMIT = 20         # Max p2p_media_status per window per user

# Channel settings (Section 9.8)
ENABLE_CHANNELS = True              # Enable/disable channels feature
CHANNEL_MAX_PER_HUB = 50           # Maximum total channels on the hub
CHANNEL_MAX_PER_USER = 10          # Maximum channels a single user can join
CHANNEL_MAX_MEMBERS = 200          # Maximum members per channel
CHANNEL_CREATE_MIN_CLASS = 1       # Minimum user class to create public channels
CHANNEL_PRIVATE_ENABLED = True     # Enable/disable private (E2E) channels
CHANNEL_PRIVATE_CREATE_MIN_CLASS = 1  # Minimum class for private channels
CHANNEL_PRIVATE_MAX_MEMBERS = 50   # Max members per private channel
CHANNEL_HISTORY_DEPTH = 100        # Public channel scrollback depth (messages)
CHANNEL_HISTORY_TTL = 86400        # Public channel history retention (seconds)
CHANNEL_PRIVATE_HISTORY = False    # Enable encrypted history for private channels
CHANNEL_NAME_MAX_LENGTH = 32       # Maximum channel name length
CHANNEL_TOPIC_MAX_LENGTH = 200     # Maximum topic length

# VoiceVideo call settings (Section 8.8)
ENABLE_VOICEVIDEO = False           # Enable/disable voice/video calls (off by default)
CALL_MAX_PARTICIPANTS = 8           # Max participants in a group call
CALL_MIN_CLASS = 1                  # Minimum user class to make/receive calls
CALL_MAX_CONCURRENT_PER_USER = 2    # Per-user concurrent call limit
CALL_MAX_CONCURRENT_HUB = 20       # Hub-wide concurrent call limit
CALL_MAX_DURATION = 7200            # Max call duration in seconds (2 hours)
CALL_MAX_BITRATE = 512000           # Max call bitrate (512 kbps)
CALL_OFFER_TIMEOUT = 30             # Auto-reject unanswered offers (seconds)

# Hub stream settings
ENABLE_HUB_STREAMS = False          # Enable/disable hub-wide streams (off by default)
STREAM_MAX_CONCURRENT = 3           # Max simultaneous hub streams
STREAM_MAX_VIEWERS = 100            # Max viewers per stream
STREAM_MIN_CLASS_BROADCAST = 3      # Class to start a stream (operator)
STREAM_MIN_CLASS_VIEW = 0           # Class to view (everyone)
STREAM_MAX_BITRATE = 256000         # Max stream bitrate (256 kbps)

logging.basicConfig(
    level=LOG_LEVEL,
    format="[NMDCpb] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("nmdcpb_hub")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

# Users that support NMDCpb — nick → set of features
_pb_users: dict[str, set[str]] = {}

# IP → nick mapping (for $Supports which arrives before login by IP)
_ip_to_nick: dict[str, str] = {}

# Stats
_stats = {
    "pb_messages_routed": 0,
    "pb_messages_translated": 0,
    "e2epm_forwarded": 0,
    "e2epm_key_exchanges": 0,
    "e2epm_encrypted_pms": 0,
    "e2epm_session_ends": 0,
    "e2epm_private_searches": 0,
    "e2epm_search_results": 0,
    "unknown_dropped": 0,
    "rate_limited": 0,
    "flood_mutes": 0,
    "relay_sessions_created": 0,
    "relay_bytes_forwarded": 0,
    "relay_sessions_closed": 0,
    "stealth_queries": 0,
    "stealth_sweeps": 0,
    "relay_opaque_forwards": 0,
    "relay_opaque_fallbacks": 0,
    "media_uploads": 0,
    "media_deletes": 0,
    "media_expired_purged": 0,
    "p2p_media_chats_routed": 0,
    "p2p_media_status_forwarded": 0,
    "p2p_media_quota_fallbacks": 0,
    "channel_messages_routed": 0,
    "channel_actions_handled": 0,
    "calls_offered": 0,
    "calls_answered": 0,
    "calls_ended": 0,
    "calls_timed_out": 0,
    "streams_started": 0,
    "streams_stopped": 0,
    "stream_joins": 0,
}


# ---------------------------------------------------------------------------
# Channel manager (lazy-init)
# ---------------------------------------------------------------------------

_channel_manager: ChannelManager | None = None


def _get_user_class(nick: str) -> int:
    """Get a user's class level from verlihub."""
    if vh is None:
        return 1  # Default class for testing
    try:
        return vh.GetUserClass(nick) or 0
    except Exception:
        return 0


def _get_channel_manager() -> ChannelManager | None:
    """Lazy-initialize the channel manager on first use."""
    global _channel_manager
    if _channel_manager is not None:
        return _channel_manager
    if not ENABLE_CHANNELS:
        return None
    cfg = ChannelConfig(
        enabled=True,
        max_per_hub=CHANNEL_MAX_PER_HUB,
        max_per_user=CHANNEL_MAX_PER_USER,
        max_members=CHANNEL_MAX_MEMBERS,
        create_min_class=CHANNEL_CREATE_MIN_CLASS,
        private_enabled=CHANNEL_PRIVATE_ENABLED,
        private_create_min_class=CHANNEL_PRIVATE_CREATE_MIN_CLASS,
        private_max_members=CHANNEL_PRIVATE_MAX_MEMBERS,
        history_depth=CHANNEL_HISTORY_DEPTH,
        history_ttl=CHANNEL_HISTORY_TTL,
        private_history=CHANNEL_PRIVATE_HISTORY,
        name_max_length=CHANNEL_NAME_MAX_LENGTH,
        topic_max_length=CHANNEL_TOPIC_MAX_LENGTH,
    )
    _channel_manager = ChannelManager(
        config=cfg,
        send_fn=_send_to_user,
        status_fn=_send_status,
        get_user_class_fn=_get_user_class,
    )
    log.info("ChannelManager initialized")
    return _channel_manager


# ---------------------------------------------------------------------------
# Media handler (lazy-init)
# ---------------------------------------------------------------------------

_media_handler: MediaHandler | None = None


def _get_media_handler() -> MediaHandler | None:
    """Lazy-initialize the media handler on first use."""
    global _media_handler
    if _media_handler is not None:
        return _media_handler
    if not ENABLE_MEDIASHARE:
        return None
    cfg = MediaConfig(
        enabled=True,
        storage_backend="filesystem",
        storage_path=MEDIA_STORAGE_PATH,
        max_file_size=MEDIA_MAX_FILE_SIZE,
        default_ttl=MEDIA_DEFAULT_TTL,
        max_ttl=MEDIA_MAX_TTL,
        per_user_quota=MEDIA_PER_USER_QUOTA,
    )
    _media_handler = MediaHandler(
        config=cfg,
        send_fn=_send_to_user,
        status_fn=_send_status,
        hub_url=MEDIA_HUB_URL,
        p2p_enabled=ENABLE_P2P_MEDIA,
        p2p_default=P2P_MEDIA_DEFAULT,
        p2p_max_size=P2P_MEDIA_MAX_SIZE,
    )
    log.info(f"MediaHandler initialized: storage={cfg.storage_path}")
    return _media_handler


# ---------------------------------------------------------------------------
# Call manager (lazy-init)
# ---------------------------------------------------------------------------

_call_manager: CallManager | None = None


def _get_call_manager() -> CallManager | None:
    """Lazy-initialize the call manager on first use."""
    global _call_manager
    if _call_manager is not None:
        return _call_manager
    if not ENABLE_VOICEVIDEO:
        return None
    cfg = CallConfig(
        enabled=True,
        max_participants=CALL_MAX_PARTICIPANTS,
        min_class=CALL_MIN_CLASS,
        max_concurrent_per_user=CALL_MAX_CONCURRENT_PER_USER,
        max_concurrent_hub=CALL_MAX_CONCURRENT_HUB,
        max_duration_sec=CALL_MAX_DURATION,
        max_bitrate=CALL_MAX_BITRATE,
        offer_timeout_sec=CALL_OFFER_TIMEOUT,
    )
    _call_manager = CallManager(
        config=cfg,
        send_fn=_send_to_user,
        status_fn=_send_status,
        get_user_class_fn=_get_user_class,
        is_pb_user_fn=_is_pb_user,
        get_all_nicks_fn=_get_all_nicks,
    )
    log.info("CallManager initialized")
    return _call_manager


# ---------------------------------------------------------------------------
# Stream manager (lazy-init)
# ---------------------------------------------------------------------------

_stream_manager: StreamManager | None = None


def _get_stream_manager() -> StreamManager | None:
    """Lazy-initialize the stream manager on first use."""
    global _stream_manager
    if _stream_manager is not None:
        return _stream_manager
    if not ENABLE_HUB_STREAMS:
        return None
    cfg = StreamConfig(
        enabled=True,
        max_concurrent=STREAM_MAX_CONCURRENT,
        max_viewers=STREAM_MAX_VIEWERS,
        min_class_broadcast=STREAM_MIN_CLASS_BROADCAST,
        min_class_view=STREAM_MIN_CLASS_VIEW,
        max_bitrate=STREAM_MAX_BITRATE,
    )
    _stream_manager = StreamManager(
        config=cfg,
        send_fn=_send_to_user,
        status_fn=_send_status,
        get_user_class_fn=_get_user_class,
        is_pb_user_fn=_is_pb_user,
        get_pb_nicks_fn=_get_pb_nicks,
    )
    log.info("StreamManager initialized")
    return _stream_manager


# ---------------------------------------------------------------------------
# Relay session management
# ---------------------------------------------------------------------------

class _RelaySession:
    """Hub-side relay session tracker."""
    __slots__ = (
        "relay_id", "user_a", "user_b", "token",
        "bytes_forwarded", "created_at", "last_activity",
    )

    def __init__(self, relay_id: int, user_a: str, user_b: str, token: str,
                 bytes_forwarded: int = 0):
        self.relay_id = relay_id
        self.user_a = user_a
        self.user_b = user_b
        self.token = token
        self.bytes_forwarded: int = bytes_forwarded
        self.created_at: float = time.time()
        self.last_activity: float = self.created_at

    def peer_of(self, nick: str) -> str:
        """Return the other user in this session."""
        if nick == self.user_a:
            return self.user_b
        if nick == self.user_b:
            return self.user_a
        return ""

    def touches(self, nick: str) -> bool:
        """True if nick is one of the two session participants."""
        return nick == self.user_a or nick == self.user_b

    def is_idle(self, now: float) -> bool:
        return (now - self.last_activity) > RELAY_IDLE_TIMEOUT_SEC


# relay_id → _RelaySession
_relay_sessions: dict[int, _RelaySession] = {}
# token → {from_nick, to_nick, purpose, pubkey, created_at}
_pending_relay: dict[str, dict] = {}
# Auto-incrementing relay_id
_next_relay_id: int = 1
# Closed sessions archive: token → {user_a, user_b, bytes_forwarded, closed_at}
# Used for resume: when a client reconnects and sends PbRelayResume with a
# previous token, the hub matches it here and creates a new relay session
# between the same pair.  Entries expire after RELAY_RESUME_TOKEN_TTL seconds.
RELAY_RESUME_TOKEN_TTL = 300  # 5 minutes
_closed_relay_tokens: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Envelope pool (avoids repeated PbEnvelope allocation)
# ---------------------------------------------------------------------------

_ENVELOPE_POOL_MAX = 32
_envelope_pool: list[PbEnvelope] = []


def _get_envelope() -> PbEnvelope:
    """Get a PbEnvelope from the pool, or create a new one."""
    if _envelope_pool:
        return _envelope_pool.pop()
    return PbEnvelope()


def _return_envelope(env: PbEnvelope) -> None:
    """Return a PbEnvelope to the pool after clearing it."""
    if len(_envelope_pool) < _ENVELOPE_POOL_MAX:
        env.Clear()
        _envelope_pool.append(env)


def _user_relay_count(nick: str) -> int:
    """Count active relay sessions for a user."""
    return sum(1 for s in _relay_sessions.values() if s.touches(nick))


def _close_relay_session(relay_id: int, reason: int = 0,
                         notify: bool = True) -> None:
    """Close a relay session and notify participants."""
    sess = _relay_sessions.pop(relay_id, None)
    if not sess:
        return
    _stats["relay_sessions_closed"] += 1

    # Archive token for potential resume within TTL
    if sess.token:
        _closed_relay_tokens[sess.token] = {
            "user_a": sess.user_a,
            "user_b": sess.user_b,
            "bytes_forwarded": sess.bytes_forwarded,
            "closed_at": time.time(),
        }

    log.info(f"Relay session {relay_id} closed ({sess.user_a} ↔ {sess.user_b}, "
             f"{sess.bytes_forwarded} bytes)")
    try:
        from verlihub.dashboard.websocket import emit_relay_event
        emit_relay_event("relay_closed", {
            "relay_id": relay_id, "user_a": sess.user_a, "user_b": sess.user_b,
            "reason": reason, "bytes_forwarded": sess.bytes_forwarded,
        })
    except Exception:
        pass
    if notify:
        env = WireCodec.make_envelope(route=PbEnvelope.DIRECT)
        env.relay_closed.relay_id = relay_id
        env.relay_closed.reason = reason
        wire = WireCodec.encode_text(env)
        _send_to_user(wire, sess.user_a)
        _send_to_user(wire, sess.user_b)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class _RateBucket:
    """Sliding-window token bucket per user."""
    __slots__ = ("timestamps", "muted_until")

    def __init__(self):
        self.timestamps: list[float] = []
        self.muted_until: float = 0.0

    def allow(self, now: float, window: float, limit: int) -> bool:
        """Return True if within rate limit; prune expired entries."""
        if now < self.muted_until:
            return False
        cutoff = now - window
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        if len(self.timestamps) >= limit:
            return False
        self.timestamps.append(now)
        return True

    def mute(self, now: float, duration: float) -> None:
        self.muted_until = now + duration

    def is_idle(self, now: float, idle_sec: float) -> bool:
        if not self.timestamps:
            return True
        return (now - self.timestamps[-1]) > idle_sec


# nick → _RateBucket for general PB messages
_rate_pb: dict[str, _RateBucket] = {}
# nick → _RateBucket for E2EPM operations
_rate_e2epm: dict[str, _RateBucket] = {}


def _check_rate(nick: str, category: str = "pb") -> bool:
    """Check if nick is within rate limit. Returns True if allowed."""
    now = time.time()
    if category == "e2epm":
        bucket = _rate_e2epm.setdefault(nick, _RateBucket())
        ok = bucket.allow(now, RATE_WINDOW_SEC, RATE_MAX_E2EPM)
    else:
        bucket = _rate_pb.setdefault(nick, _RateBucket())
        ok = bucket.allow(now, RATE_WINDOW_SEC, RATE_MAX_MESSAGES)

    if not ok:
        _stats["rate_limited"] += 1
        # Check if this is a flood (significantly over limit)
        if len(bucket.timestamps) >= (RATE_MAX_MESSAGES * 2 if category == "pb"
                                       else RATE_MAX_E2EPM * 2):
            bucket.mute(now, RATE_FLOOD_BAN_SEC)
            _stats["flood_mutes"] += 1
            _send_status(
                nick, PbStatus.ERROR, 20,
                f"Flood detected — muted for {RATE_FLOOD_BAN_SEC}s",
            )
            log.warning(f"Flood mute: {nick} ({category})")
        else:
            _send_status(
                nick, PbStatus.WARNING, 21,
                f"Rate limit exceeded ({category}) — slow down",
            )
        return False
    return True


def name_and_version():
    """Plugin identification."""
    return "NMDCpb Hub Plugin", VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_to_user(data: str, nick: str) -> bool:
    """Send raw protocol data to a user. Returns success."""
    if vh is None:
        log.debug(f"[mock] → {nick}: {data[:120]}")
        return True
    try:
        vh.SendDataToUser(data, nick)
        return True
    except Exception as e:
        log.error(f"SendDataToUser({nick}) failed: {e}")
        return False


def _send_status(nick: str, severity: int, code: int, message: str) -> None:
    """Send a PbStatus message to a user."""
    env = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT,
        from_nick="",
        to_nick=nick,
    )
    env.status.severity = severity
    env.status.code = code
    env.status.message = message
    env.timestamp = int(time.time() * 1000)
    wire = WireCodec.encode_text(env)
    _send_to_user(wire, nick)


def _is_pb_user(nick: str) -> bool:
    """Check if a user supports NMDCpb."""
    return nick in _pb_users


def _get_pb_nicks() -> list[str]:
    """Get all NMDCpb-capable user nicks."""
    return list(_pb_users.keys())


def _get_all_nicks() -> list[str]:
    """Get all connected user nicks."""
    if vh is None:
        return list(_pb_users.keys())
    try:
        return vh.GetNickList() or []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Verlihub Hooks
# ---------------------------------------------------------------------------

def OnParsedMsgSupports(ip: str, msg_str: str, back: str) -> int:
    """Handle $Supports from connecting clients."""
    has_nmdcpb, has_relay, has_relayonly = WireCodec.check_supports(msg_str)

    if has_nmdcpb:
        _ip_to_nick[ip] = ""
        features = []
        if has_relay:
            features.append("HubRelay")
        if has_relayonly:
            features.append("RelayOnly")
        log.info(f"Client {ip} supports NMDCpb" +
                 (f" + {', '.join(features)}" if features else ""))
    return 1


def OnParsedMsgAnyEx(ip: str, msg_str: str) -> int:
    """Handle any message by IP (pre-login too)."""
    if WireCodec.is_nmdcpb_command(msg_str):
        log.debug(f"Pre-login PB from {ip}: dropped")
        return 0
    return 1


def OnUserLogin(nick: str) -> int:
    """Track NMDCpb-capable users after login."""
    if vh is not None:
        ip = vh.GetUserIP(nick)
        if ip and ip in _ip_to_nick:
            features = {FEATURE_NMDCPB}
            _pb_users[nick] = features
            del _ip_to_nick[ip]
            log.info(f"User {nick} logged in with NMDCpb support")
            _send_status(
                nick,
                PbStatus.INFO,
                0,
                f"NMDCpb v{VERSION} active — "
                f"{len(_pb_users)} protobuf-capable users online",
            )
            # Send media capabilities if enabled
            if ENABLE_MEDIASHARE:
                handler = _get_media_handler()
                if handler is not None:
                    # Issue a session token for HTTP media API auth
                    try:
                        token = generate_session_token(nick, ip or "")
                        handler.set_session_token(nick, token)
                    except Exception as e:
                        log.warning(f"Failed to issue media token for {nick}: {e}")
                    try:
                        loop = _get_event_loop()
                        loop.run_until_complete(
                            handler.handle_media_capabilities_request(nick))
                    except Exception as e:
                        log.warning(f"Failed to send media caps to {nick}: {e}")
            # Channel auto-join + list
            if ENABLE_CHANNELS:
                cm = _get_channel_manager()
                if cm is not None:
                    cm.on_user_login(nick)
    return 1


def OnUserLogout(nick: str) -> int:
    """Clean up NMDCpb state on user logout."""
    _close_user_relays(nick, reason=4)  # USER_DISCONNECT
    # Remove from channels before clearing PB user state
    if ENABLE_CHANNELS:
        cm = _get_channel_manager()
        if cm is not None:
            cm.on_user_logout(nick)
    # Clean up VoiceVideo calls/streams
    if ENABLE_VOICEVIDEO and _call_manager is not None:
        _call_manager.handle_user_disconnect(nick)
    if ENABLE_HUB_STREAMS and _stream_manager is not None:
        _stream_manager.handle_user_disconnect(nick)
    _pb_users.pop(nick, None)
    _rate_pb.pop(nick, None)
    revoke_sessions_for_nick(nick)
    _rate_e2epm.pop(nick, None)
    return 1


def OnCloseConnEx(ip: str, reason: int, nick: str) -> int:
    """Clean up on connection close."""
    _close_user_relays(nick, reason=4)  # USER_DISCONNECT
    # Clean up VoiceVideo calls/streams
    if ENABLE_VOICEVIDEO and _call_manager is not None:
        _call_manager.handle_user_disconnect(nick)
    if ENABLE_HUB_STREAMS and _stream_manager is not None:
        _stream_manager.handle_user_disconnect(nick)
    _pb_users.pop(nick, None)
    _ip_to_nick.pop(ip, None)
    _rate_pb.pop(nick, None)
    _rate_e2epm.pop(nick, None)
    return 1


def OnUnknownMsg(nick: str, msg_str: str) -> int:
    """Handle unknown protocol messages — catches $PB/$PBB/$PBR."""
    if not WireCodec.is_nmdcpb_command(msg_str):
        return 1

    if not _is_pb_user(nick):
        log.warning(f"PB message from non-PB user {nick} — dropping")
        return 0

    if len(msg_str) > MAX_PB_SIZE:
        _send_status(nick, PbStatus.ERROR, 1, "Message too large")
        return 0

    # Rate limiting — general PB messages
    if not _check_rate(nick, "pb"):
        return 0

    # ---------------------------------------------------------------
    # Fast path: opaque relay data forwarding
    # If this is a DIRECT relay_data frame, we can forward it using
    # raw byte surgery — only scanning the protobuf wire format for
    # relay_id / data_length without a full ParseFromString.  This
    # avoids copying the (potentially 64 KB) data blob into Python.
    # ---------------------------------------------------------------
    fast = WireCodec.decode_relay_opaque(msg_str)
    if fast is not None:
        _route, relay_id, data_length, raw_pb = fast
        _stats["pb_messages_routed"] += 1
        # We still need a parsed envelope for validation
        # (relay_id lookup, sender check, size check), but by
        # passing raw_pb we enable build_relay_forward() for output.
        try:
            env = WireCodec.decode(msg_str)
        except Exception as e:
            log.warning(f"Failed to decode PB from {nick}: {e}")
            _send_status(nick, PbStatus.ERROR, 2, f"Decode error: {e}")
            return 0
        if env is None:
            return 0
        env.from_nick = nick
        _forward_relay_data(nick, env, raw_pb=raw_pb)
        _return_envelope(env)
        return 0

    try:
        result = WireCodec.decode(msg_str)
    except Exception as e:
        log.warning(f"Failed to decode PB from {nick}: {e}")
        _send_status(nick, PbStatus.ERROR, 2, f"Decode error: {e}")
        return 0

    env = result
    assert isinstance(env, PbEnvelope)
    env.from_nick = nick

    _stats["pb_messages_routed"] += 1
    route = env.route

    if route == PbEnvelope.BROADCAST:
        _route_broadcast(nick, env, msg_str)
    elif route == PbEnvelope.DIRECT:
        _route_direct(nick, env)
    elif route == PbEnvelope.HUB:
        _route_hub(nick, env)
    elif route == PbEnvelope.ECHO:
        _route_echo(nick, env)
    elif route == PbEnvelope.FEATURE:
        _route_feature(nick, env)
    elif route == PbEnvelope.INFO:
        _send_status(nick, PbStatus.ERROR, 3, "INFO route is hub-only")
    else:
        _send_status(nick, PbStatus.ERROR, 4, f"Unknown route: {route}")

    return 0


def OnParsedMsgChat(nick: str, message: str) -> int:
    """Handle legacy chat — bridge to NMDCpb users as #general channel messages."""
    if not _pb_users:
        return 1

    env = WireCodec.make_envelope(
        route=PbEnvelope.BROADCAST,
        from_nick=nick,
    )
    env.chat.text = message
    env.chat.channel_id = GENERAL_CHANNEL_ID  # Tag as #general
    env.timestamp = int(time.time() * 1000)
    wire = WireCodec.encode_text(env)

    for pb_nick in _get_pb_nicks():
        if pb_nick != nick:
            _send_to_user(wire, pb_nick)
    return 1


def OnParsedMsgPM(nick: str, message: str, to_nick: str) -> int:
    """Handle legacy PM — bridge to NMDCpb users."""
    if to_nick in _pb_users and nick not in _pb_users:
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=nick,
            to_nick=to_nick,
        )
        env.chat.text = message
        env.chat.is_pm = True
        env.chat.target_nick = to_nick
        env.timestamp = int(time.time() * 1000)
        wire = WireCodec.encode_text(env)
        _send_to_user(wire, to_nick)
    return 1


def OnTimer(msec: float) -> int:
    """Periodic maintenance — prune stale rate buckets and relay sessions."""
    now = time.time()
    # Prune rate buckets for users no longer online
    online = _get_all_nicks()
    for store in (_rate_pb, _rate_e2epm):
        stale = [
            nick for nick, bucket in store.items()
            if nick not in online and bucket.is_idle(now, SESSION_IDLE_SEC)
        ]
        for nick in stale:
            del store[nick]

    # Expire idle relay sessions
    if ENABLE_HUBRELAY:
        idle_relays = [rid for rid, s in _relay_sessions.items()
                       if s.is_idle(now)]
        for rid in idle_relays:
            _close_relay_session(rid, 2, notify=True)  # TIMEOUT
        # Clean up stale pending relay requests (>30s)
        stale_pending = [t for t, p in _pending_relay.items()
                         if now - p.get("created_at", 0) > 30]
        for t in stale_pending:
            _pending_relay.pop(t, None)
        # Expire closed relay tokens past TTL
        expired_tokens = [t for t, info in _closed_relay_tokens.items()
                          if (now - info["closed_at"]) > RELAY_RESUME_TOKEN_TTL]
        for t in expired_tokens:
            del _closed_relay_tokens[t]

    # Media expiry check
    if ENABLE_MEDIASHARE and _media_handler is not None:
        try:
            loop = _get_event_loop()
            purged = loop.run_until_complete(_media_handler.check_expiry())
            if purged:
                _stats["media_expired_purged"] += purged
        except Exception as e:
            log.warning(f"Media expiry check failed: {e}")

    # Prune expired session tokens
    prune_expired_sessions()

    # Channel history TTL pruning
    if ENABLE_CHANNELS and _channel_manager is not None:
        _channel_manager.prune_expired_history()

        _channel_manager.prune_expired_history()

    # VoiceVideo call timeout pruning
    if ENABLE_VOICEVIDEO and _call_manager is not None:
        pruned = _call_manager.prune_expired()
        if pruned:
            _stats["calls_timed_out"] += pruned

    return 1


# ---------------------------------------------------------------------------
# Admin channel commands
# ---------------------------------------------------------------------------

def _handle_channel_admin(nick: str, sub_args: str, user_class: int) -> str:
    """Handle +nmdcpb channel <subcommand> <args>.

    Requires hub operator (class >= 3) for kick/topic,
    hub admin (class >= 5) for create/delete/rotate-keys.
    """
    cm = _get_channel_manager()
    if cm is None:
        return "Channels are not enabled."

    parts = sub_args.split(None, 2)
    if not parts:
        return (
            "Channel admin commands:\n"
            "  +nmdcpb channel create <id> [private]\n"
            "  +nmdcpb channel delete <id>\n"
            "  +nmdcpb channel kick <id> <nick>\n"
            "  +nmdcpb channel topic <id> <new topic>\n"
            "  +nmdcpb channel rotate-keys <id>\n"
            "  +nmdcpb channel info <id>"
        )

    subcmd = parts[0].lower()
    ch_id = parts[1] if len(parts) > 1 else ""
    extra = parts[2] if len(parts) > 2 else ""

    # Strip leading '#' from channel id for convenience
    if ch_id.startswith("#"):
        ch_id = ch_id[1:]

    if subcmd == "create":
        if user_class < 5:
            return "Insufficient privileges (requires admin class >= 5)"
        if not ch_id:
            return "Usage: +nmdcpb channel create <id> [private]"
        is_private = extra.lower() in ("private", "true", "1", "yes")
        # Build a synthetic PbChannel action envelope
        from verlihub.client.nmdcpb.proto.nmdcpb_pb2 import (
            PbChannel,
            PbEnvelope,
        )
        env = PbEnvelope()
        env.route = PbEnvelope.HUB
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = ch_id
        env.channel.name = ch_id
        env.channel.is_private = is_private
        cm.handle_channel_action(nick, env)
        priv_str = " (private)" if is_private else ""
        return f"Channel #{ch_id}{priv_str} creation requested"

    elif subcmd == "delete":
        if user_class < 5:
            return "Insufficient privileges (requires admin class >= 5)"
        if not ch_id:
            return "Usage: +nmdcpb channel delete <id>"
        ch = cm.get_channel(ch_id)
        if not ch:
            return f"Channel '{ch_id}' not found"
        from verlihub.client.nmdcpb.proto.nmdcpb_pb2 import (
            PbChannel,
            PbEnvelope,
        )
        env = PbEnvelope()
        env.route = PbEnvelope.HUB
        env.channel.action = PbChannel.DELETE
        env.channel.channel_id = ch_id
        cm.handle_channel_action(nick, env)
        return f"Channel #{ch_id} deletion requested"

    elif subcmd == "kick":
        if user_class < 3:
            return "Insufficient privileges (requires operator class >= 3)"
        if not ch_id or not extra:
            return "Usage: +nmdcpb channel kick <id> <nick>"
        target_nick = extra.split()[0]
        ch = cm.get_channel(ch_id)
        if not ch:
            return f"Channel '{ch_id}' not found"
        if not ch.has_member(target_nick):
            return f"{target_nick} is not in #{ch_id}"
        from verlihub.client.nmdcpb.proto.nmdcpb_pb2 import (
            PbChannel,
            PbEnvelope,
        )
        env = PbEnvelope()
        env.route = PbEnvelope.HUB
        env.channel.action = PbChannel.KICK
        env.channel.channel_id = ch_id
        env.channel.target_nick = target_nick
        cm.handle_channel_action(nick, env)
        return f"Kick of {target_nick} from #{ch_id} requested"

    elif subcmd == "topic":
        if user_class < 3:
            return "Insufficient privileges (requires operator class >= 3)"
        if not ch_id:
            return "Usage: +nmdcpb channel topic <id> <new topic>"
        ch = cm.get_channel(ch_id)
        if not ch:
            return f"Channel '{ch_id}' not found"
        from verlihub.client.nmdcpb.proto.nmdcpb_pb2 import (
            PbChannel,
            PbEnvelope,
        )
        env = PbEnvelope()
        env.route = PbEnvelope.HUB
        env.channel.action = PbChannel.SET_TOPIC
        env.channel.channel_id = ch_id
        env.channel.topic = extra
        cm.handle_channel_action(nick, env)
        return f"Topic of #{ch_id} set to: {extra[:60]}"

    elif subcmd == "rotate-keys":
        if user_class < 5:
            return "Insufficient privileges (requires admin class >= 5)"
        if not ch_id:
            return "Usage: +nmdcpb channel rotate-keys <id>"
        return cm.force_rotate_keys(ch_id, nick)

    elif subcmd == "info":
        if not ch_id:
            return "Usage: +nmdcpb channel info <id>"
        ch = cm.get_channel(ch_id)
        if not ch:
            return f"Channel '{ch_id}' not found"
        members = ", ".join(sorted(ch.members.keys()))
        return (
            f"Channel #{ch.channel_id}:\n"
            f"  Name:    {ch.name}\n"
            f"  Private: {ch.is_private}\n"
            f"  Owner:   {ch.owner_nick}\n"
            f"  Topic:   {ch.topic or '(none)'}\n"
            f"  Members ({ch.member_count}): {members}\n"
            f"  History: {len(ch.history)} entries\n"
            f"  Created: {ch.created_at}"
        )

    else:
        return (
            f"Unknown channel subcommand: {subcmd}\n"
            "  Valid: create, delete, kick, topic, rotate-keys, info"
        )


def OnHubCommand(nick: str, command: str, user_class: int, in_pm: int, prefix: str) -> int:
    """Handle hub commands for NMDCpb management."""
    if command.startswith("nmdcpb"):
        args = command[6:].strip()

        if args == "stats":
            msg = (
                f"NMDCpb Statistics:\n"
                f"  PB messages routed: {_stats['pb_messages_routed']}\n"
                f"  Legacy translations: {_stats['pb_messages_translated']}\n"
                f"  E2EPM forwarded:    {_stats['e2epm_forwarded']}\n"
                f"  Unknown dropped:    {_stats['unknown_dropped']}\n"
                f"  Rate limited:       {_stats['rate_limited']}\n"
                f"  Flood mutes:        {_stats['flood_mutes']}\n"
                f"  Relay created:      {_stats['relay_sessions_created']}\n"
                f"  Relay closed:       {_stats['relay_sessions_closed']}\n"
                f"  Relay bytes:        {_stats['relay_bytes_forwarded']}\n"
                f"  Media uploads:      {_stats.get('media_uploads', 0)}\n"
                f"  Media deletes:      {_stats.get('media_deletes', 0)}\n"
                f"  Media expired:      {_stats.get('media_expired_purged', 0)}\n"
                f"  P2P media chats:    {_stats.get('p2p_media_chats_routed', 0)}\n"
                f"  P2P status fwd:     {_stats.get('p2p_media_status_forwarded', 0)}\n"
                f"  P2P quota fallback: {_stats.get('p2p_media_quota_fallbacks', 0)}\n"
                f"  Channel messages:   {_stats.get('channel_messages_routed', 0)}\n"
                f"  Channel actions:    {_stats.get('channel_actions_handled', 0)}\n"
                f"  Calls offered:      {_stats.get('calls_offered', 0)}\n"
                f"  Calls answered:     {_stats.get('calls_answered', 0)}\n"
                f"  Calls ended:        {_stats.get('calls_ended', 0)}\n"
                f"  Calls timed out:    {_stats.get('calls_timed_out', 0)}\n"
                f"  Streams started:    {_stats.get('streams_started', 0)}\n"
                f"  Streams stopped:    {_stats.get('streams_stopped', 0)}\n"
                f"  Stream joins:       {_stats.get('stream_joins', 0)}\n"
            )
        elif args == "users":
            if _pb_users:
                msg = f"NMDCpb users ({len(_pb_users)}):\n"
                for pb_nick, feats in _pb_users.items():
                    msg += f"  {pb_nick}: {', '.join(feats)}\n"
            else:
                msg = "No NMDCpb-capable users online."
        elif args == "relay":
            if not _relay_sessions:
                msg = "No active relay sessions."
            else:
                msg = f"Active relay sessions ({len(_relay_sessions)}):\n"
                for rid, sess in _relay_sessions.items():
                    age = int(time.time() - sess.created_at)
                    msg += (f"  #{rid}: {sess.user_a} ↔ {sess.user_b} "
                            f"({sess.bytes_forwarded} bytes, {age}s)\n")
            if _pending_relay:
                msg += f"\nPending requests: {len(_pending_relay)}"
        elif args == "media":
            handler = _get_media_handler()
            if handler is None:
                msg = "MediaShare is not enabled."
            else:
                ms = handler.get_stats()
                msg = (
                    f"MediaShare Status:\n"
                    f"  Enabled:   {handler.config.enabled}\n"
                    f"  Storage:   {handler.config.storage_backend} "
                    f"({handler.config.storage_path})\n"
                    f"  Max file:  {handler.config.max_file_size // (1024*1024)} MB\n"
                    f"  Quota/user: {handler.config.per_user_quota // (1024*1024)} MB\n"
                    f"  TTL:       {handler.config.default_ttl // 86400}d "
                    f"(max {handler.config.max_ttl // 86400}d)\n"
                    f"  Uploads:   {ms['uploads']}\n"
                    f"  Deletes:   {ms['deletes']}\n"
                    f"  Expired:   {ms['expired_purged']}\n"
                    f"  Quota rej: {ms['quota_rejections']}\n"
                    f"  Type rej:  {ms['type_rejections']}\n"
                )
        elif args.startswith("media delete "):
            # Operator delete: +nmdcpb media delete <media_id> [reason]
            parts = args[len("media delete "):].split(None, 1)
            media_id = parts[0] if parts else ""
            reason = parts[1] if len(parts) > 1 else ""
            handler = _get_media_handler()
            if handler is None:
                msg = "MediaShare is not enabled."
            elif not media_id:
                msg = "Usage: +nmdcpb media delete <media_id> [reason]"
            else:
                loop = _get_event_loop()
                loop.run_until_complete(
                    handler.handle_operator_delete(nick, media_id, reason))
                msg = f"Delete request submitted for {media_id}"
        elif args == "channels":
            cm = _get_channel_manager()
            if cm is None:
                msg = "Channels are not enabled."
            else:
                msg = f"Channel Status:\n{cm.get_stats_summary()}\n\nChannels:\n"
                for ch in cm.get_all_channels():
                    priv = " [private]" if ch.is_private else ""
                    msg += (f"  #{ch.channel_id}{priv}: "
                            f"{ch.member_count} members")
                    if ch.topic:
                        msg += f" — {ch.topic[:40]}"
                    msg += "\n"
        elif args.startswith("channel "):
            # Admin channel management: +nmdcpb channel <subcommand> <args>
            msg = _handle_channel_admin(nick, args[8:].strip(), user_class)
        elif args == "calls":
            cm = _get_call_manager()
            if cm is None:
                msg = "Voice/video calls are not enabled."
            else:
                msg = f"VoiceVideo Status:\n{cm.get_stats_summary()}\n\nActive calls:\n"
                for call in cm.get_active_calls():
                    participants = ', '.join(call.all_nicks)
                    dur = call.duration_sec
                    answered = 'active' if call.answered_at else 'ringing'
                    msg += (f"  {call.call_id[:8]}...: {participants} "
                            f"({answered}, {dur}s)\n")
                if not cm.get_active_calls():
                    msg += "  (none)\n"
        elif args == "streams":
            sm = _get_stream_manager()
            if sm is None:
                msg = "Hub streams are not enabled."
            else:
                msg = f"Stream Status:\n{sm.get_stats_summary()}\n\nActive streams:\n"
                for stream in sm.get_active_streams():
                    msg += (f"  {stream.stream_id[:8]}...: '{stream.title}' "
                            f"by {stream.broadcaster} "
                            f"({stream.viewer_count} viewers)\n")
                if not sm.get_active_streams():
                    msg += "  (none)\n"
        elif args.startswith("call end "):
            # Admin force-end a call: +nmdcpb call end <call_id_prefix>
            if user_class < 5:
                msg = "Insufficient privileges (requires admin class >= 5)"
            else:
                cm = _get_call_manager()
                if cm is None:
                    msg = "Voice/video calls are not enabled."
                else:
                    prefix_arg = args[9:].strip()
                    found = None
                    for call in cm.get_active_calls():
                        if call.call_id.startswith(prefix_arg):
                            found = call
                            break
                    if found is None:
                        msg = f"No call found matching '{prefix_arg}'"
                    else:
                        # Build an end envelope and process it
                        end_env = WireCodec.make_envelope(
                            route=PbEnvelope.DIRECT,
                            from_nick="",
                            to_nick="",
                        )
                        end_env.call_end.call_id = found.call_id
                        end_env.call_end.reason = PbCallEnd.ERROR
                        cm.handle_call_end(found.initiator, end_env)
                        msg = f"Call {found.call_id[:8]}... force-ended"
        elif args.startswith("stream stop "):
            # Admin force-stop a stream: +nmdcpb stream stop <stream_id_prefix>
            if user_class < 5:
                msg = "Insufficient privileges (requires admin class >= 5)"
            else:
                sm = _get_stream_manager()
                if sm is None:
                    msg = "Hub streams are not enabled."
                else:
                    prefix_arg = args[12:].strip()
                    found = None
                    for stream in sm.get_active_streams():
                        if stream.stream_id.startswith(prefix_arg):
                            found = stream
                            break
                    if found is None:
                        msg = f"No stream found matching '{prefix_arg}'"
                    else:
                        stop_env = WireCodec.make_envelope(
                            route=PbEnvelope.HUB,
                            from_nick=found.broadcaster,
                        )
                        stop_env.hub_stream.action = PbHubStream.STOP_STREAM
                        stop_env.hub_stream.stream_id = found.stream_id
                        sm.handle_hub_stream(found.broadcaster, stop_env)
                        msg = f"Stream {found.stream_id[:8]}... force-stopped"
        else:
            cm = _get_channel_manager()
            ch_count = cm.get_channel_count() if cm else 0
            msg = (
                f"NMDCpb Hub Plugin v{VERSION}\n"
                f"  NMDCpb users: {len(_pb_users)}\n"
                f"  Total users:  {len(_get_all_nicks())}\n"
                f"  Legacy translation: {'on' if ENABLE_LEGACY_TRANSLATION else 'off'}\n"
                f"  HubRelay: {'on' if ENABLE_HUBRELAY else 'off'}\n"
                f"  Active relays: {len(_relay_sessions)}\n"
                f"  E2EPM forward: {'on' if ENABLE_E2EPM_FORWARD else 'off'}\n"
                f"  MediaShare:  {'on' if ENABLE_MEDIASHARE else 'off'}\n"
                f"  P2P Media:   {'on' if ENABLE_P2P_MEDIA else 'off'}"
                f"{'  (default)' if P2P_MEDIA_DEFAULT else ''}\n"
                f"  Channels:    {'on' if ENABLE_CHANNELS else 'off'}"
                f" ({ch_count} active)\n"
                f"  VoiceVideo:  {'on' if ENABLE_VOICEVIDEO else 'off'}"
                f" ({_call_manager.get_call_count() if _call_manager else 0} calls)\n"
                f"  Hub Streams: {'on' if ENABLE_HUB_STREAMS else 'off'}"
                f" ({_stream_manager.get_stream_count() if _stream_manager else 0} streams)\n"
                f"  Rate limit: {RATE_MAX_MESSAGES}/{RATE_WINDOW_SEC}s (PB), "
                f"{RATE_MAX_E2EPM}/{RATE_WINDOW_SEC}s (E2EPM)\n"
                f"\nCommands: +nmdcpb stats | users | relay | media | channels"
                f"\n         +nmdcpb calls | streams"
                f"\n         +nmdcpb channel <create|delete|kick|topic|rotate-keys>"
                f"\n         +nmdcpb call end <id> | stream stop <id>"
            )

        if vh and in_pm:
            vh.pm(msg, nick)
        elif vh:
            vh.usermc(msg, nick, vh.botname if vh else "NMDCpb")
        else:
            log.info(msg)
        return 0
    return 1


def OnUnLoad(code: int) -> int:
    """Script unload — clean up."""
    global _channel_manager, _call_manager, _stream_manager
    log.info(f"NMDCpb hub plugin unloading (code={code})")
    _pb_users.clear()
    _ip_to_nick.clear()
    _rate_pb.clear()
    _rate_e2epm.clear()
    _relay_sessions.clear()
    _pending_relay.clear()
    _channel_manager = None
    _call_manager = None
    _stream_manager = None
    return 1


# ---------------------------------------------------------------------------
# VoiceVideo call/stream routing helpers
# ---------------------------------------------------------------------------

def _route_call_signaling(sender: str, env: PbEnvelope, payload: str) -> None:
    """Route voice/video call signaling messages through CallManager."""
    if not ENABLE_VOICEVIDEO:
        _send_status(sender, PbStatus.ERROR, 100,
                     "Voice/video calls are not enabled on this hub")
        return

    cm = _get_call_manager()
    if cm is None:
        _send_status(sender, PbStatus.ERROR, 100,
                     "Voice/video calls are not enabled on this hub")
        return

    handled = False
    if payload == "call_offer":
        handled = cm.handle_call_offer(sender, env)
        _stats["calls_offered"] += 1
    elif payload == "call_answer":
        handled = cm.handle_call_answer(sender, env)
        if env.call_answer.accepted:
            _stats["calls_answered"] += 1
    elif payload == "call_candidate":
        handled = cm.handle_call_candidate(sender, env)
    elif payload == "call_end":
        handled = cm.handle_call_end(sender, env)
        _stats["calls_ended"] += 1
    elif payload == "call_media_control":
        handled = cm.handle_call_media_control(sender, env)

    if not handled:
        log.warning(f"Unhandled call signaling {payload} from {sender}")


def _route_hub_stream(sender: str, env: PbEnvelope) -> None:
    """Route hub stream management messages through StreamManager."""
    if not ENABLE_HUB_STREAMS:
        _send_status(sender, PbStatus.ERROR, 120,
                     "Hub streams are not enabled on this hub")
        return

    sm = _get_stream_manager()
    if sm is None:
        _send_status(sender, PbStatus.ERROR, 120,
                     "Hub streams are not enabled on this hub")
        return

    hs = env.hub_stream
    sm.handle_hub_stream(sender, env)

    # Update hub-level stats
    action = hs.action
    if action == PbHubStream.START_STREAM:
        _stats["streams_started"] += 1
    elif action == PbHubStream.STOP_STREAM:
        _stats["streams_stopped"] += 1
    elif action == PbHubStream.JOIN_STREAM:
        _stats["stream_joins"] += 1


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route_broadcast(sender: str, env: PbEnvelope, raw_wire: str) -> None:
    """Route a BROADCAST message to all users."""
    # Channel message intercept: PbChat with channel_id → channel routing
    if ENABLE_CHANNELS:
        cm = _get_channel_manager()
        if cm is not None:
            if cm.route_channel_chat(sender, env):
                _stats["channel_messages_routed"] += 1
                # Also translate #general messages to legacy
                if (ENABLE_LEGACY_TRANSLATION
                        and env.HasField("chat")
                        and env.chat.channel_id == GENERAL_CHANNEL_ID):
                    _broadcast_legacy_chat(sender, env)
                return
            if cm.route_channel_encrypted(sender, env):
                _stats["channel_messages_routed"] += 1
                return
            if cm.route_sender_key_rotation(sender, env):
                return

    # Validate P2P attachments if present
    if not _validate_p2p_chat_attachments(sender, env):
        return

    wire = WireCodec.encode_text(env)
    for pb_nick in _get_pb_nicks():
        if pb_nick != sender:
            _send_to_user(wire, pb_nick)

    # Track P2P media chats
    if env.HasField("chat") and env.chat.p2p_attachments.__len__() > 0:
        _stats["p2p_media_chats_routed"] += 1

    if ENABLE_LEGACY_TRANSLATION:
        payload = env.WhichOneof("payload")
        if payload == "chat":
            _broadcast_legacy_chat(sender, env)


def _route_direct(sender: str, env: PbEnvelope) -> None:
    """Route a DIRECT message to a specific user."""
    target = env.to_nick
    if not target:
        _send_status(sender, PbStatus.ERROR, 5, "DIRECT route requires to_nick")
        return

    payload = env.WhichOneof("payload")

    # Relay message intercepts — hub handles these specially
    if payload == "relay_request":
        _route_relay_request(sender, env)
        return
    if payload == "relay_ack":
        _route_relay_ack(sender, env)
        return
    if payload == "relay_data":
        _forward_relay_data(sender, env)
        return
    if payload == "relay_closed":
        _route_relay_closed(sender, env)
        return
    if payload == "relay_resume":
        _forward_relay_resume(sender, env)
        return
    if payload in ("segment_request", "segment_info"):
        _forward_segment_msg(sender, env)
        return

    # P2P media status messages — forward peer-to-peer
    if payload == "p2p_media_status":
        _forward_p2p_media_status(sender, target, env)
        return

    # VoiceVideo call signaling — routed through CallManager
    if payload in ("call_offer", "call_answer", "call_candidate",
                    "call_end", "call_media_control"):
        _route_call_signaling(sender, env, payload)
        return

    if payload in ("pm_key_exchange", "encrypted_pm", "pm_session_end",
                    "private_search", "private_search_result"):
        if ENABLE_E2EPM_FORWARD:
            _forward_e2epm(sender, target, env)
        else:
            _send_status(sender, PbStatus.ERROR, 6, "E2EPM not enabled on this hub")
        return

    all_nicks = _get_all_nicks()
    if target not in all_nicks:
        _send_status(sender, PbStatus.ERROR, 7, f"User {target} not found")
        return

    if _is_pb_user(target):
        wire = WireCodec.encode_text(env)
        _send_to_user(wire, target)
    else:
        if payload == "chat" and env.chat.is_pm:
            text = env.chat.text
            legacy = f"$To: {target} From: {sender} $<{sender}> {text}|"
            _send_to_user(legacy, target)
            _stats["pb_messages_translated"] += 1
        else:
            _send_status(
                sender, PbStatus.WARNING, 8,
                f"User {target} doesn't support NMDCpb — cannot deliver",
            )


def _route_hub(sender: str, env: PbEnvelope) -> None:
    """Handle HUB-bound messages."""
    payload = env.WhichOneof("payload")
    if payload == "user_query":
        _route_user_query(sender, env)
        return
    if payload in ("media_upload", "media_delete", "media_capabilities",
                    "media_meta"):
        _route_media(sender, env, payload)
        return
    # Channel management actions
    if payload == "channel":
        if ENABLE_CHANNELS:
            cm = _get_channel_manager()
            if cm is not None:
                cm.handle_channel_action(sender, env)
                _stats["channel_actions_handled"] += 1
        else:
            _send_status(sender, PbStatus.ERROR, 60,
                         "Channels are not enabled on this hub")
        return
    if payload == "channel_invite":
        if ENABLE_CHANNELS:
            cm = _get_channel_manager()
            if cm is not None:
                cm.handle_channel_invite(sender, env)
        return
    if payload == "channel_invite_response":
        if ENABLE_CHANNELS:
            cm = _get_channel_manager()
            if cm is not None:
                cm.handle_channel_invite_response(sender, env)
        return
    # Hub stream management
    if payload == "hub_stream":
        _route_hub_stream(sender, env)
        return
    if payload == "extension":
        log.info(f"Extension negotiation from {sender}: {env.extension}")
        _send_status(sender, PbStatus.INFO, 0, "Extension noted")
    else:
        _send_status(sender, PbStatus.WARNING, 9, f"Unhandled HUB payload: {payload}")


def _route_echo(sender: str, env: PbEnvelope) -> None:
    """Route ECHO — send to target + echo back to sender (ADC E-type)."""
    target = env.to_nick
    if not target:
        _send_status(sender, PbStatus.ERROR, 5, "ECHO route requires to_nick")
        return

    all_nicks = _get_all_nicks()
    if target not in all_nicks:
        _send_status(sender, PbStatus.ERROR, 7, f"User {target} not found")
        return

    wire = WireCodec.encode_text(env)

    # Send to target
    if _is_pb_user(target):
        _send_to_user(wire, target)
    else:
        payload = env.WhichOneof("payload")
        if payload == "chat":
            text = env.chat.text
            if env.chat.is_pm:
                legacy = f"$To: {target} From: {sender} $<{sender}> {text}|"
            else:
                legacy = f"<{sender}> {text}|"
            _send_to_user(legacy, target)
            _stats["pb_messages_translated"] += 1
        else:
            _send_status(
                sender, PbStatus.WARNING, 8,
                f"User {target} doesn't support NMDCpb — cannot deliver",
            )

    # Echo back to sender
    _send_to_user(wire, sender)


def _route_feature(sender: str, env: PbEnvelope) -> None:
    """Route FEATURE — send only to users with matching features."""
    required = set(env.features)
    wire = WireCodec.encode_text(env)
    for pb_nick, user_feats in _pb_users.items():
        if pb_nick != sender and required.issubset(user_feats):
            _send_to_user(wire, pb_nick)


# ---------------------------------------------------------------------------
# Legacy translation
# ---------------------------------------------------------------------------

def _broadcast_legacy_chat(sender: str, env: PbEnvelope) -> None:
    """Translate a PbChat broadcast to legacy NMDC chat for non-PB users."""
    text = env.chat.text
    if env.chat.is_action:
        legacy = f"<{sender}> /me {text}|"
    else:
        legacy = f"<{sender}> {text}|"

    all_nicks = _get_all_nicks()
    for nick in all_nicks:
        if nick != sender and not _is_pb_user(nick):
            _send_to_user(legacy, nick)
            _stats["pb_messages_translated"] += 1


# ---------------------------------------------------------------------------
# E2EPM forwarding
# ---------------------------------------------------------------------------

def _forward_e2epm(sender: str, target: str, env: PbEnvelope) -> None:
    """Forward E2EPM messages opaquely between users."""
    # E2EPM-specific rate limit
    if not _check_rate(sender, "e2epm"):
        return
    all_nicks = _get_all_nicks()
    if target not in all_nicks:
        _send_status(sender, PbStatus.ERROR, 7, f"User {target} not found")
        return
    if not _is_pb_user(target):
        _send_status(
            sender, PbStatus.ERROR, 10,
            f"User {target} doesn't support NMDCpb — cannot deliver E2EPM",
        )
        return
    wire = WireCodec.encode_text(env)
    _send_to_user(wire, target)
    _stats["e2epm_forwarded"] += 1
    # Track E2EPM message subtypes
    payload_type = env.WhichOneof("payload")
    if payload_type == "pm_key_exchange":
        _stats["e2epm_key_exchanges"] += 1
    elif payload_type == "encrypted_pm":
        _stats["e2epm_encrypted_pms"] += 1
    elif payload_type == "pm_session_end":
        _stats["e2epm_session_ends"] += 1
    elif payload_type == "private_search":
        _stats["e2epm_private_searches"] += 1
    elif payload_type == "private_search_result":
        _stats["e2epm_search_results"] += 1
    log.debug(f"E2EPM {payload_type} forwarded: {sender} → {target}")


# ---------------------------------------------------------------------------
# Relay (HubRelay) routing
# ---------------------------------------------------------------------------

def _close_user_relays(nick: str, reason: int = 4) -> None:
    """Close all relay sessions involving a user (e.g., on disconnect).

    reason 4 = USER_DISCONNECT in PbRelayClosed.CloseReason.
    """
    to_close = [rid for rid, s in _relay_sessions.items() if s.touches(nick)]
    for rid in to_close:
        _close_relay_session(rid, reason, notify=True)
    # Clean up pending relay requests from/to this user
    to_remove = [t for t, p in _pending_relay.items()
                 if p.get("from_nick") == nick or p.get("to_nick") == nick]
    for t in to_remove:
        _pending_relay.pop(t, None)


def _route_relay_request(sender: str, env: PbEnvelope) -> None:
    """Handle relay session request: validate, track pending, forward to target."""
    req = env.relay_request
    target = req.target_nick or env.to_nick
    token = req.token

    if not ENABLE_HUBRELAY:
        _send_status(sender, PbStatus.ERROR, 11, "HubRelay is disabled")
        return

    if not target:
        _send_status(sender, PbStatus.ERROR, 12, "Relay request missing target_nick")
        return

    all_nicks = _get_all_nicks()
    if target not in all_nicks:
        _send_status(sender, PbStatus.ERROR, 7, f"User {target} not found")
        return

    if not _is_pb_user(target):
        _send_status(
            sender, PbStatus.ERROR, 13,
            f"User {target} doesn't support NMDCpb relay",
        )
        return

    # Per-user limit
    if _user_relay_count(sender) >= RELAY_MAX_SESSIONS_PER_USER:
        _send_status(
            sender, PbStatus.ERROR, 14,
            f"Max relay sessions per user ({RELAY_MAX_SESSIONS_PER_USER}) reached",
        )
        return

    # Hub-wide limit
    if len(_relay_sessions) >= RELAY_MAX_SESSIONS_TOTAL:
        _send_status(sender, PbStatus.ERROR, 15, "Hub relay session limit reached")
        return

    # Store pending request
    _pending_relay[token] = {
        "from_nick": sender,
        "to_nick": target,
        "purpose": req.purpose,
        "pubkey": bytes(req.public_key) if req.public_key else b"",
        "created_at": time.time(),
    }

    # Forward to target
    env.from_nick = sender
    wire = WireCodec.encode_text(env)
    _send_to_user(wire, target)
    log.info(f"Relay request forwarded: {sender} → {target} (token={token[:16]}...)")


def _route_relay_ack(sender: str, env: PbEnvelope) -> None:
    """Handle relay ack: match token, assign relay_id, create session, notify both."""
    ack = env.relay_ack
    token = ack.token

    pending = _pending_relay.pop(token, None)
    if not pending:
        _send_status(sender, PbStatus.WARNING, 16, "Relay ack for unknown token")
        return

    requester = pending["from_nick"]

    if not ack.accepted:
        # Rejection — forward to requester
        fwd = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, from_nick=sender, to_nick=requester,
        )
        fwd.relay_ack.token = token
        fwd.relay_ack.accepted = False
        fwd.relay_ack.reject_reason = ack.reject_reason
        _send_to_user(WireCodec.encode_text(fwd), requester)
        log.info(f"Relay rejected by {sender} for {requester}")
        return

    # Assign relay_id
    global _next_relay_id
    relay_id = _next_relay_id
    _next_relay_id += 1

    # Create session
    sess = _RelaySession(relay_id, requester, sender, token)
    _relay_sessions[relay_id] = sess
    _stats["relay_sessions_created"] += 1
    log.info(f"Relay session {relay_id} created: {requester} ↔ {sender}")
    try:
        from verlihub.dashboard.websocket import emit_relay_event
        emit_relay_event("relay_created", {
            "relay_id": relay_id, "user_a": requester, "user_b": sender,
        })
    except Exception:
        pass

    # Notify requester (ack with relay_id)
    ack_to_req = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT, from_nick=sender, to_nick=requester,
    )
    ack_to_req.relay_ack.token = token
    ack_to_req.relay_ack.accepted = True
    ack_to_req.relay_ack.relay_id = relay_id
    if ack.public_key:
        ack_to_req.relay_ack.public_key = ack.public_key
    _send_to_user(WireCodec.encode_text(ack_to_req), requester)

    # Notify responder (so they also learn relay_id)
    ack_to_resp = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT, from_nick=requester, to_nick=sender,
    )
    ack_to_resp.relay_ack.token = token
    ack_to_resp.relay_ack.accepted = True
    ack_to_resp.relay_ack.relay_id = relay_id
    if pending.get("pubkey"):
        ack_to_resp.relay_ack.public_key = pending["pubkey"]
    _send_to_user(WireCodec.encode_text(ack_to_resp), sender)


def _forward_relay_data(sender: str, env: PbEnvelope,
                        raw_pb: bytes | None = None) -> None:
    """Forward relay data to the peer in the relay session.

    If *raw_pb* (the original serialized protobuf bytes) is provided,
    uses opaque forwarding — the (potentially large) relay data payload
    is forwarded as raw bytes without re-serialization, cutting the
    per-frame cost roughly in half for large payloads.
    """
    rd = env.relay_data
    relay_id = rd.relay_id

    sess = _relay_sessions.get(relay_id)
    if not sess:
        _send_status(sender, PbStatus.ERROR, 17, f"Unknown relay session {relay_id}")
        return

    if not sess.touches(sender):
        _send_status(sender, PbStatus.ERROR, 18,
                     "Not a participant in this relay session")
        return

    data_len = len(rd.data)
    if data_len > RELAY_MAX_PAYLOAD:
        _send_status(sender, PbStatus.ERROR, 19,
                     f"Relay data exceeds max size ({RELAY_MAX_PAYLOAD})")
        return

    peer = sess.peer_of(sender)
    sess.bytes_forwarded += data_len
    sess.last_activity = time.time()
    _stats["relay_bytes_forwarded"] += data_len

    # Try opaque forwarding (raw byte surgery — avoids full re-serialize)
    if raw_pb is not None:
        wire = WireCodec.build_relay_forward(
            raw_pb, from_nick=sender, to_nick=peer,
            timestamp=env.timestamp,
        )
        if wire:
            _stats["relay_opaque_forwards"] += 1
            _send_to_user(wire, peer)
            return
        _stats["relay_opaque_fallbacks"] += 1

    # Fallback: full protobuf re-serialization
    env.from_nick = sender
    env.to_nick = peer
    wire = WireCodec.encode_text(env)
    _send_to_user(wire, peer)


def _route_relay_closed(sender: str, env: PbEnvelope) -> None:
    """Handle relay close from a participant."""
    rc = env.relay_closed
    relay_id = rc.relay_id

    sess = _relay_sessions.get(relay_id)
    if not sess:
        return  # Already closed, silently ignore

    if not sess.touches(sender):
        _send_status(sender, PbStatus.ERROR, 18,
                     "Not a participant in this relay session")
        return

    _close_relay_session(relay_id, rc.reason, notify=True)


def _forward_relay_resume(sender: str, env: PbEnvelope) -> None:
    """Forward a relay resume request to the peer.

    Two scenarios:
    1. In-session resume: relay_id is still active → forward to peer.
    2. Reconnect resume: relay_id=0 but token matches a recently-closed session
       → create a NEW relay session between the same pair, assign new relay_id,
       then forward the resume to the peer.
    """
    global _next_relay_id
    rr = env.relay_resume
    relay_id = rr.relay_id
    token = rr.token

    # --- Case 1: Active session resume ---
    sess = _relay_sessions.get(relay_id) if relay_id else None
    if sess:
        if not sess.touches(sender):
            _send_status(sender, PbStatus.ERROR, 18,
                         "Not a participant in this relay session")
            return

        peer = sess.peer_of(sender)
        sess.last_activity = time.time()

        env.from_nick = sender
        env.to_nick = peer
        wire = WireCodec.encode_text(env)
        _send_to_user(wire, peer)
        log.info(f"Relay resume forwarded (active): relay {relay_id}, "
                 f"offset={rr.resume_offset}, {sender} → {peer}")
        return

    # --- Case 2: Reconnect resume via archived token ---
    if token and token in _closed_relay_tokens:
        archived = _closed_relay_tokens[token]

        # Check TTL
        if (time.time() - archived["closed_at"]) > RELAY_RESUME_TOKEN_TTL:
            del _closed_relay_tokens[token]
            _send_status(sender, PbStatus.ERROR, 17,
                         "Resume token expired (session closed too long ago)")
            return

        # Verify sender was a participant
        if sender not in (archived["user_a"], archived["user_b"]):
            _send_status(sender, PbStatus.ERROR, 18,
                         "Not a participant in the original session")
            return

        peer = (archived["user_b"] if sender == archived["user_a"]
                else archived["user_a"])

        # Check peer is still online
        all_nicks = _get_all_nicks()
        if peer not in all_nicks:
            _send_status(sender, PbStatus.ERROR, 7,
                         f"Peer {peer} is offline, cannot resume")
            return

        # Check session limits
        if _user_relay_count(sender) >= RELAY_MAX_SESSIONS_PER_USER:
            _send_status(sender, PbStatus.ERROR, 15,
                         "Relay session limit reached")
            return

        # Create new relay session for the resumed transfer
        new_relay_id = _next_relay_id
        _next_relay_id += 1

        new_sess = _RelaySession(
            relay_id=new_relay_id,
            user_a=sender,
            user_b=peer,
            token=token,
            bytes_forwarded=archived["bytes_forwarded"],
        )
        _relay_sessions[new_relay_id] = new_sess
        _stats["relay_sessions_created"] += 1

        # Remove from archive (one-time use)
        del _closed_relay_tokens[token]

        # Update resume message with new relay_id and forward
        rr.relay_id = new_relay_id
        env.from_nick = sender
        env.to_nick = peer
        wire = WireCodec.encode_text(env)
        _send_to_user(wire, peer)
        log.info(f"Relay resume (reconnect): new relay {new_relay_id} "
                 f"(token={token[:8]}...), offset={rr.resume_offset}, "
                 f"{sender} → {peer}")
        return

    _send_status(sender, PbStatus.ERROR, 17,
                 f"Unknown relay session {relay_id} and no matching resume token")


def _forward_segment_msg(sender: str, env: PbEnvelope) -> None:
    """Forward segment_request or segment_info as opaque DIRECT messages.

    These are P2P negotiation messages — the hub just routes them
    to the target without interpreting them.
    """
    target = env.to_nick
    if not target:
        _send_status(sender, PbStatus.ERROR, 5, "Segment message requires to_nick")
        return

    all_nicks = _get_all_nicks()
    if target not in all_nicks:
        _send_status(sender, PbStatus.ERROR, 7, f"User {target} not found")
        return

    if not _is_pb_user(target):
        _send_status(sender, PbStatus.ERROR, 13,
                     f"User {target} doesn't support NMDCpb")
        return

    env.from_nick = sender
    wire = WireCodec.encode_text(env)
    _send_to_user(wire, target)


# ---------------------------------------------------------------------------
# MediaShare routing
# ---------------------------------------------------------------------------

def _route_media(sender: str, env: PbEnvelope, payload: str) -> None:
    """Route media messages to the MediaHandler (async bridge)."""
    handler = _get_media_handler()
    if handler is None:
        _send_status(sender, PbStatus.ERROR, 40,
                     "Media sharing is not enabled on this hub")
        return

    loop = _get_event_loop()
    if payload == "media_upload":
        # Check for P2P fallback when quota exhausted
        if ENABLE_P2P_MEDIA and _handle_media_upload_quota_fallback(
                sender, env.media_upload):
            return
        loop.run_until_complete(
            handler.handle_media_upload(sender, env.media_upload))
        _stats["media_uploads"] += 1
    elif payload == "media_delete":
        loop.run_until_complete(
            handler.handle_media_delete(sender, env.media_delete))
        _stats["media_deletes"] += 1
    elif payload == "media_capabilities":
        loop.run_until_complete(
            handler.handle_media_capabilities_request(sender))
    elif payload == "media_meta":
        media_id = env.media_meta.media_id if env.HasField("media_meta") else ""
        if media_id:
            loop.run_until_complete(
                handler.handle_media_meta_request(sender, media_id))
        else:
            _send_status(sender, PbStatus.ERROR, 44,
                         "Missing media_id in media_meta request")


# ---------------------------------------------------------------------------
# P2P Media routing
# ---------------------------------------------------------------------------

def _forward_p2p_media_status(sender: str, target: str, env: PbEnvelope) -> None:
    """Forward PbP2PMediaStatus between peers.

    Validates that the target is a PB user and rate-limits status updates.
    """
    if not ENABLE_P2P_MEDIA:
        _send_status(sender, PbStatus.ERROR, 50,
                     "P2P media sharing is not enabled on this hub")
        return

    if not _is_pb_user(target):
        _send_status(sender, PbStatus.ERROR, 51,
                     f"User {target} does not support NMDCpb")
        return

    # Rate limit P2P status messages
    if not _check_rate(sender, "pb"):
        return

    wire = WireCodec.encode_text(env)
    _send_to_user(wire, target)
    _stats["p2p_media_status_forwarded"] += 1


def _validate_p2p_chat_attachments(sender: str, env: PbEnvelope) -> bool:
    """Validate P2P attachments in a PbChat before broadcast.

    Checks: P2P enabled, file sizes within limits, required fields present.
    Returns True if valid (or no attachments), False if rejected.
    """
    if not env.HasField("chat"):
        return True
    chat = env.chat
    if chat.p2p_attachments.__len__() == 0:
        return True

    if not ENABLE_P2P_MEDIA:
        _send_status(sender, PbStatus.ERROR, 50,
                     "P2P media sharing is not enabled on this hub")
        return False

    for ref in chat.p2p_attachments:
        if not ref.tth:
            _send_status(sender, PbStatus.ERROR, 52,
                         "P2P media ref missing TTH")
            return False
        if not ref.filename:
            _send_status(sender, PbStatus.ERROR, 52,
                         "P2P media ref missing filename")
            return False
        if ref.size > P2P_MEDIA_MAX_SIZE:
            _send_status(sender, PbStatus.ERROR, 53,
                         f"P2P media file too large: {ref.size} > {P2P_MEDIA_MAX_SIZE}")
            return False

    return True


def _handle_media_upload_quota_fallback(
    sender: str, upload: PbMediaUpload
) -> bool:
    """When hub-hosted upload fails due to quota, suggest P2P as fallback.

    Returns True if a fallback P2P capabilities response was sent.
    """
    if not ENABLE_P2P_MEDIA:
        return False

    handler = _get_media_handler()
    if handler is None:
        return False

    # Check if quota would be exceeded
    quota = handler.storage.get_quota(sender)
    if quota.remaining_bytes >= upload.size:
        return False  # Quota OK, no fallback needed

    # Quota exceeded — suggest P2P
    env = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT,
        from_nick="",
        to_nick=sender,
    )
    caps = env.media_capabilities
    caps.enabled = True
    caps.max_file_size = P2P_MEDIA_MAX_SIZE
    caps.user_quota_remaining = 0
    caps.p2p_enabled = True
    caps.p2p_default = True  # Signal that P2P is the recommended path
    caps.p2p_max_size = P2P_MEDIA_MAX_SIZE
    import time as _time
    env.timestamp = int(_time.time() * 1000)
    wire = WireCodec.encode_text(env)
    _send_to_user(wire, sender)
    _stats["p2p_media_quota_fallbacks"] += 1
    log.info(f"P2P fallback for {sender}: quota exhausted, file={upload.size}")
    return True


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create an asyncio event loop for sync→async bridging."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


# ---------------------------------------------------------------------------
# Stealth Hub-Wide User Query
# ---------------------------------------------------------------------------

def _route_user_query(sender: str, env: PbEnvelope) -> None:
    """Handle a PbUserQuery: filter online users, optionally sweep search.

    The hub filters connected NMDCpb users by feature_filter and
    min_share_size, returning matching nicks to the requester.
    If sweep=true and a search payload is provided, the hub forwards
    PbPrivateSearch to each matching user on behalf of the requester.
    """
    if not ENABLE_STEALTH_SEARCH:
        _send_status(sender, PbStatus.ERROR, 14,
                     "Stealth search is not enabled on this hub")
        return

    uq = env.user_query
    query_id = uq.query_id
    feature_filter = uq.feature_filter or ""
    min_share = uq.min_share_size
    max_results = min(uq.max_results or STEALTH_MAX_RESULTS,
                      STEALTH_MAX_RESULTS)

    # Build matching user list
    matching: list[str] = []
    for nick in _get_pb_nicks():
        if nick == sender:
            continue

        # Feature filter: if specified, user must have that feature
        if feature_filter:
            user_features = _pb_users.get(nick)
            if isinstance(user_features, set):
                if feature_filter not in user_features:
                    continue
            elif not user_features:
                continue

        # min_share_size: we'd need user info for this — skip if we can't check
        # (Hub doesn't always cache share sizes; filter is best-effort)

        matching.append(nick)

    total_matching = len(matching)
    result_nicks = matching[:max_results]

    # Build response
    resp = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT,
        from_nick="",
        to_nick=sender,
    )
    resp.user_query_result.query_id = query_id
    resp.user_query_result.nicks.extend(result_nicks)
    resp.user_query_result.total_matching = total_matching

    # Sweep: forward PbPrivateSearch to each matching user
    sweep_count = 0
    if uq.sweep and uq.HasField("search"):
        sweep_targets = matching[:STEALTH_MAX_SWEEP_TARGETS]
        search = uq.search

        for target_nick in sweep_targets:
            search_env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=sender,
                to_nick=target_nick,
            )
            search_env.private_search.CopyFrom(search)
            wire = WireCodec.encode_text(search_env)
            _send_to_user(wire, target_nick)
            sweep_count += 1

        resp.user_query_result.sweep_started = True
        resp.user_query_result.sweep_count = sweep_count
        log.info(f"Stealth sweep from {sender}: query_id={query_id}, "
                 f"sent to {sweep_count} users")
    else:
        log.info(f"User query from {sender}: query_id={query_id}, "
                 f"{total_matching} matching, {len(result_nicks)} returned")

    _send_to_user(WireCodec.encode_text(resp), sender)
    _stats["pb_messages_routed"] += 1
    _stats["stealth_queries"] += 1
    if sweep_count:
        _stats["stealth_sweeps"] += 1
