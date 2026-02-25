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
    "unknown_dropped": 0,
    "rate_limited": 0,
    "flood_mutes": 0,
    "relay_sessions_created": 0,
    "relay_bytes_forwarded": 0,
    "relay_sessions_closed": 0,
    "stealth_queries": 0,
    "stealth_sweeps": 0,
}


# ---------------------------------------------------------------------------
# Relay session management
# ---------------------------------------------------------------------------

class _RelaySession:
    """Hub-side relay session tracker."""
    __slots__ = (
        "relay_id", "user_a", "user_b", "token",
        "bytes_forwarded", "created_at", "last_activity",
    )

    def __init__(self, relay_id: int, user_a: str, user_b: str, token: str):
        self.relay_id = relay_id
        self.user_a = user_a
        self.user_b = user_b
        self.token = token
        self.bytes_forwarded: int = 0
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
    log.info(f"Relay session {relay_id} closed ({sess.user_a} ↔ {sess.user_b}, "
             f"{sess.bytes_forwarded} bytes)")
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
    return 1


def OnUserLogout(nick: str) -> int:
    """Clean up NMDCpb state on user logout."""
    _close_user_relays(nick, reason=4)  # USER_DISCONNECT
    _pb_users.pop(nick, None)
    _rate_pb.pop(nick, None)
    _rate_e2epm.pop(nick, None)
    return 1


def OnCloseConnEx(ip: str, reason: int, nick: str) -> int:
    """Clean up on connection close."""
    _close_user_relays(nick, reason=4)  # USER_DISCONNECT
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
    """Handle legacy chat — bridge to NMDCpb users."""
    if not _pb_users:
        return 1

    env = WireCodec.make_envelope(
        route=PbEnvelope.BROADCAST,
        from_nick=nick,
    )
    env.chat.text = message
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
    return 1


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
        else:
            msg = (
                f"NMDCpb Hub Plugin v{VERSION}\n"
                f"  NMDCpb users: {len(_pb_users)}\n"
                f"  Total users:  {len(_get_all_nicks())}\n"
                f"  Legacy translation: {'on' if ENABLE_LEGACY_TRANSLATION else 'off'}\n"
                f"  HubRelay: {'on' if ENABLE_HUBRELAY else 'off'}\n"
                f"  Active relays: {len(_relay_sessions)}\n"
                f"  E2EPM forward: {'on' if ENABLE_E2EPM_FORWARD else 'off'}\n"
                f"  Rate limit: {RATE_MAX_MESSAGES}/{RATE_WINDOW_SEC}s (PB), "
                f"{RATE_MAX_E2EPM}/{RATE_WINDOW_SEC}s (E2EPM)\n"
                f"\nCommands: +nmdcpb stats | +nmdcpb users | +nmdcpb relay"
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
    log.info(f"NMDCpb hub plugin unloading (code={code})")
    _pb_users.clear()
    _ip_to_nick.clear()
    _rate_pb.clear()
    _rate_e2epm.clear()
    _relay_sessions.clear()
    _pending_relay.clear()
    return 1


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route_broadcast(sender: str, env: PbEnvelope, raw_wire: str) -> None:
    """Route a BROADCAST message to all users."""
    wire = WireCodec.encode_text(env)
    for pb_nick in _get_pb_nicks():
        if pb_nick != sender:
            _send_to_user(wire, pb_nick)
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
    log.debug(f"E2EPM {env.WhichOneof('payload')} forwarded: {sender} → {target}")


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


def _forward_relay_data(sender: str, env: PbEnvelope) -> None:
    """Forward relay data to the peer in the relay session."""
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

    # Forward to peer
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
    """Forward a relay resume request to the peer (sender side of transfer)."""
    rr = env.relay_resume
    relay_id = rr.relay_id

    sess = _relay_sessions.get(relay_id)
    if not sess:
        _send_status(sender, PbStatus.ERROR, 17, f"Unknown relay session {relay_id}")
        return

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
    log.info(f"Relay resume forwarded: relay {relay_id}, offset={rr.resume_offset}, "
             f"{sender} → {peer}")


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
