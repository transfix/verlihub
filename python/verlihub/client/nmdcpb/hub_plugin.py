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
from verlihub.client.nmdcpb.nmdcpb_pb2 import PbEnvelope, PbChat, PbStatus

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VERSION = "0.1.0"
LOG_LEVEL = logging.DEBUG

# Feature flags
ENABLE_LEGACY_TRANSLATION = True   # Translate PB chat → legacy <nick> text
ENABLE_HUBRELAY = False            # Not yet implemented
ENABLE_E2EPM_FORWARD = True        # Opaque forward of E2EPM messages
MAX_PB_SIZE = 65536                # Max protobuf wire frame size

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
}


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
    _pb_users.pop(nick, None)
    return 1


def OnCloseConnEx(ip: str, reason: int, nick: str) -> int:
    """Clean up on connection close."""
    _pb_users.pop(nick, None)
    _ip_to_nick.pop(ip, None)
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

    try:
        result = WireCodec.decode(msg_str)
    except Exception as e:
        log.warning(f"Failed to decode PB from {nick}: {e}")
        _send_status(nick, PbStatus.ERROR, 2, f"Decode error: {e}")
        return 0

    if isinstance(result, tuple):
        relay_id, data = result
        _handle_relay(nick, relay_id, data)
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
    """Periodic maintenance."""
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
            )
        elif args == "users":
            if _pb_users:
                msg = f"NMDCpb users ({len(_pb_users)}):\n"
                for pb_nick, feats in _pb_users.items():
                    msg += f"  {pb_nick}: {', '.join(feats)}\n"
            else:
                msg = "No NMDCpb-capable users online."
        else:
            msg = (
                f"NMDCpb Hub Plugin v{VERSION}\n"
                f"  NMDCpb users: {len(_pb_users)}\n"
                f"  Total users:  {len(_get_all_nicks())}\n"
                f"  Legacy translation: {'on' if ENABLE_LEGACY_TRANSLATION else 'off'}\n"
                f"  HubRelay: {'on' if ENABLE_HUBRELAY else 'off'}\n"
                f"  E2EPM forward: {'on' if ENABLE_E2EPM_FORWARD else 'off'}\n"
                f"\nCommands: +nmdcpb stats | +nmdcpb users"
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
# Relay (HubRelay) — stub for Phase 0
# ---------------------------------------------------------------------------

def _handle_relay(sender: str, relay_id: int, data: bytes) -> None:
    """Handle relay data frames."""
    log.debug(f"Relay from {sender}: id={relay_id}, {len(data)} bytes — NOT IMPLEMENTED")
    _stats["unknown_dropped"] += 1
    _send_status(sender, PbStatus.WARNING, 11, "HubRelay is not yet implemented")
