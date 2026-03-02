"""
VoiceVideo Call & Stream Manager for NMDCpb Hub Plugin
=====================================================

Hub-side signaling manager for voice/video calls and hub-wide streams.

Calls use the SFU model — the hub forwards encrypted media packets
between participants without decoding or mixing.  Media transport
goes through HubRelay sessions; this module only handles signaling
(offer / answer / end / media-control).

Hub streams are one-to-many broadcasts (TLS-protected, not E2E) where
the hub replicates media to all subscribers.

Config
------
``CallConfig``  — per-hub call limits and permission settings.
``StreamConfig`` — per-hub stream limits and permission settings.

Usage
-----
The ``CallManager`` and ``StreamManager`` are instantiated by the hub
plugin on first use (lazy-init) and receive the same ``send_fn`` /
``status_fn`` / ``get_user_class_fn`` hooks as ChannelManager.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from verlihub.client.nmdcpb.wire import WireCodec
from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope, PbCallOffer, PbCallAnswer, PbCallCandidate,
    PbCallEnd, PbCallMediaControl, PbHubStream, PbStatus,
)

log = logging.getLogger("nmdcpb_hub.call_manager")


# ============================================================================
# Configuration dataclasses
# ============================================================================

@dataclass
class CallConfig:
    """Hub-level voice/video call configuration."""
    enabled: bool = False                 # Master switch — disabled by default
    max_participants: int = 8             # Max peers in a group call
    min_class: int = 1                    # Minimum user class to initiate/accept
    max_concurrent_per_user: int = 2      # Per-user concurrent call limit
    max_concurrent_hub: int = 20          # Hub-wide concurrent call limit
    max_duration_sec: int = 7200          # 2 hours default
    max_bitrate: int = 512_000            # 512 kbps
    offer_timeout_sec: int = 30           # Auto-reject unanswered offers


@dataclass
class StreamConfig:
    """Hub-level stream broadcast configuration."""
    enabled: bool = False                 # Master switch — disabled by default
    max_concurrent: int = 3               # Max simultaneous hub streams
    max_viewers: int = 100                # Max viewers per stream
    min_class_broadcast: int = 3          # Class to start a stream (operator)
    min_class_view: int = 0               # Class to view (everyone)
    max_bitrate: int = 256_000            # 256 kbps


# ============================================================================
# Call session tracking
# ============================================================================

@dataclass
class CallSession:
    """Tracks a single call (1-to-1 or group)."""
    call_id: str
    initiator: str                        # Nick that sent PbCallOffer
    is_group: bool = False
    group_id: str = ""
    # Participants: nick → joined (True when answered, False during ringing)
    participants: dict[str, bool] = field(default_factory=dict)
    # Media types offered
    media: list[int] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    answered_at: float = 0.0
    max_participants: int = 8

    @property
    def active_count(self) -> int:
        """Number of participants that have answered."""
        return sum(1 for v in self.participants.values() if v)

    @property
    def all_nicks(self) -> set[str]:
        """All nicks in the call (ringing + active)."""
        return set(self.participants.keys())

    @property
    def duration_sec(self) -> int:
        """Seconds since the call was answered (0 if unanswered)."""
        if self.answered_at:
            return int(time.time() - self.answered_at)
        return 0

    def is_participant(self, nick: str) -> bool:
        return nick in self.participants

    def is_active_participant(self, nick: str) -> bool:
        return self.participants.get(nick, False)


# ============================================================================
# Stream session tracking
# ============================================================================

@dataclass
class StreamSession:
    """Tracks a single hub-wide stream."""
    stream_id: str
    broadcaster: str                      # Nick that started the stream
    title: str = ""
    description: str = ""
    media: list[int] = field(default_factory=list)
    bitrate: int = 0
    max_viewers: int = 100
    viewers: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)

    @property
    def viewer_count(self) -> int:
        return len(self.viewers)


# ============================================================================
# CallManager
# ============================================================================

class CallManager:
    """Hub-side call signaling manager.

    Parameters
    ----------
    config : CallConfig
        Hub-level call settings (limits, permissions).
    send_fn : callable(data: str, nick: str) -> bool
        Send raw wire data to a user.
    status_fn : callable(nick: str, severity: int, code: int, message: str) -> None
        Send a PbStatus message to a user.
    get_user_class_fn : callable(nick: str) -> int
        Retrieve a user's class level.
    is_pb_user_fn : callable(nick: str) -> bool
        Check if a user supports NMDCpb.
    get_all_nicks_fn : callable() -> list[str]
        Get all online nicks.
    """

    # Status codes for VoiceVideo — 100-119 range
    ERR_VV_DISABLED = 100
    ERR_VV_CLASS = 101
    ERR_VV_CONCURRENT = 102
    ERR_VV_TARGET_NOT_FOUND = 103
    ERR_VV_TARGET_NO_PB = 104
    ERR_VV_CALL_NOT_FOUND = 105
    ERR_VV_NOT_PARTICIPANT = 106
    ERR_VV_HUB_LIMIT = 107
    ERR_VV_SELF_CALL = 108
    ERR_VV_ALREADY_IN_CALL = 109
    ERR_VV_MAX_DURATION = 110

    def __init__(
        self,
        config: CallConfig | None = None,
        send_fn: Callable[[str, str], bool] | None = None,
        status_fn: Callable[[str, int, int, str], None] | None = None,
        get_user_class_fn: Callable[[str], int] | None = None,
        is_pb_user_fn: Callable[[str], bool] | None = None,
        get_all_nicks_fn: Callable[[], list[str]] | None = None,
    ):
        self.config = config or CallConfig()
        self._send_fn = send_fn or self._noop_send
        self._status_fn = status_fn or self._noop_status
        self._get_user_class = get_user_class_fn or (lambda _: 1)
        self._is_pb_user = is_pb_user_fn or (lambda _: True)
        self._get_all_nicks = get_all_nicks_fn or (lambda: [])

        # call_id → CallSession
        self._calls: dict[str, CallSession] = {}

        self.stats: dict[str, int] = {
            "calls_offered": 0,
            "calls_answered": 0,
            "calls_rejected": 0,
            "calls_ended": 0,
            "calls_timed_out": 0,
            "media_control_forwarded": 0,
            "candidates_forwarded": 0,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _noop_send(data: str, nick: str) -> bool:
        return True

    @staticmethod
    def _noop_status(nick: str, severity: int, code: int, message: str) -> None:
        pass

    def _user_call_count(self, nick: str) -> int:
        """Count how many active calls a user is in."""
        return sum(
            1 for c in self._calls.values() if c.is_participant(nick)
        )

    def _send_envelope(self, env: PbEnvelope, target: str) -> bool:
        """Encode and send an envelope to a target nick."""
        wire = WireCodec.encode_text(env)
        return self._send_fn(wire, target)

    def _forward_to_call_peers(
        self, call: CallSession, sender: str, env: PbEnvelope
    ) -> int:
        """Forward an envelope to all call participants except sender."""
        wire = WireCodec.encode_text(env)
        sent = 0
        for nick in call.all_nicks:
            if nick != sender and self._is_pb_user(nick):
                if self._send_fn(wire, nick):
                    sent += 1
        return sent

    # ------------------------------------------------------------------
    # Public: call offer
    # ------------------------------------------------------------------

    def handle_call_offer(self, sender: str, env: PbEnvelope) -> bool:
        """Process PbCallOffer from sender → create session, forward to target.

        Returns True if the offer was processed (even if rejected).
        """
        self.stats["calls_offered"] += 1
        offer = env.call_offer

        # Validate
        if not self.config.enabled:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_DISABLED,
                            "Voice/video calls are not enabled on this hub")
            return True

        # Check caller class
        caller_class = self._get_user_class(sender)
        if caller_class < self.config.min_class:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_CLASS,
                            f"Insufficient class ({caller_class}) to make calls "
                            f"(need {self.config.min_class})")
            return True

        # No self-calls
        target = offer.target_nick
        if target == sender:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_SELF_CALL,
                            "Cannot call yourself")
            return True

        # Check per-user concurrent limit
        if self._user_call_count(sender) >= self.config.max_concurrent_per_user:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_CONCURRENT,
                            f"Maximum concurrent calls reached "
                            f"({self.config.max_concurrent_per_user})")
            return True

        # Check hub-wide limit
        if len(self._calls) >= self.config.max_concurrent_hub:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_HUB_LIMIT,
                            f"Hub call limit reached ({self.config.max_concurrent_hub})")
            return True

        # Target must be online
        all_nicks = self._get_all_nicks()
        if target not in all_nicks:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_TARGET_NOT_FOUND,
                            f"User {target} is not online")
            return True

        # Target must support NMDCpb
        if not self._is_pb_user(target):
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_TARGET_NO_PB,
                            f"User {target} does not support NMDCpb")
            return True

        # Check target class
        target_class = self._get_user_class(target)
        if target_class < self.config.min_class:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_CLASS,
                            f"User {target} has insufficient class for calls")
            return True

        # Check target concurrent calls
        if self._user_call_count(target) >= self.config.max_concurrent_per_user:
            # Send BUSY back to caller
            busy_env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=target,
                to_nick=sender,
            )
            busy_env.call_end.call_id = offer.call_id
            busy_env.call_end.reason = PbCallEnd.BUSY
            busy_env.timestamp = int(time.time() * 1000)
            self._send_envelope(busy_env, sender)
            return True

        # Generate call_id if not provided
        call_id = offer.call_id or str(uuid.uuid4())

        # Create call session
        session = CallSession(
            call_id=call_id,
            initiator=sender,
            is_group=offer.is_group,
            group_id=offer.group_id,
            media=list(offer.media),
            max_participants=self.config.max_participants,
        )
        session.participants[sender] = True  # Initiator is implicitly active
        session.participants[target] = False  # Target is ringing

        self._calls[call_id] = session
        log.info(f"Call {call_id}: {sender} → {target} "
                 f"(media={list(offer.media)}, group={offer.is_group})")

        # Forward the offer to target
        # Ensure envelope is addressed correctly
        fwd = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick=target,
        )
        fwd.call_offer.CopyFrom(offer)
        fwd.call_offer.call_id = call_id  # Ensure hub-assigned ID
        fwd.timestamp = int(time.time() * 1000)
        self._send_envelope(fwd, target)

        return True

    # ------------------------------------------------------------------
    # Public: call answer
    # ------------------------------------------------------------------

    def handle_call_answer(self, sender: str, env: PbEnvelope) -> bool:
        """Process PbCallAnswer from target → forward to initiator."""
        answer = env.call_answer
        call_id = answer.call_id

        session = self._calls.get(call_id)
        if session is None:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_CALL_NOT_FOUND,
                            f"Call {call_id} not found")
            return True

        if not session.is_participant(sender):
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_NOT_PARTICIPANT,
                            f"You are not a participant in call {call_id}")
            return True

        if answer.accepted:
            session.participants[sender] = True
            session.answered_at = time.time()
            self.stats["calls_answered"] += 1
            log.info(f"Call {call_id}: {sender} accepted")
        else:
            self.stats["calls_rejected"] += 1
            reason = answer.reject_reason or "declined"
            log.info(f"Call {call_id}: {sender} rejected ({reason})")

        # Forward answer to initiator (and other peers in group calls)
        fwd = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick=session.initiator,
        )
        fwd.call_answer.CopyFrom(answer)
        fwd.timestamp = int(time.time() * 1000)

        if session.is_group:
            # Group call: forward to all peers
            self._forward_to_call_peers(session, sender, fwd)
        else:
            # 1-to-1: forward to initiator
            self._send_envelope(fwd, session.initiator)

        # If rejected in a 1-to-1 call, clean up
        if not answer.accepted and not session.is_group:
            self._cleanup_call(call_id)

        return True

    # ------------------------------------------------------------------
    # Public: ICE candidate exchange
    # ------------------------------------------------------------------

    def handle_call_candidate(self, sender: str, env: PbEnvelope) -> bool:
        """Forward PbCallCandidate to call peers."""
        cand = env.call_candidate
        call_id = cand.call_id

        session = self._calls.get(call_id)
        if session is None:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_CALL_NOT_FOUND,
                            f"Call {call_id} not found")
            return True

        if not session.is_participant(sender):
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_NOT_PARTICIPANT,
                            f"You are not a participant in call {call_id}")
            return True

        # Forward candidate to peers
        fwd = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick="",
        )
        fwd.call_candidate.CopyFrom(cand)
        fwd.timestamp = int(time.time() * 1000)
        sent = self._forward_to_call_peers(session, sender, fwd)
        self.stats["candidates_forwarded"] += sent

        return True

    # ------------------------------------------------------------------
    # Public: call end
    # ------------------------------------------------------------------

    def handle_call_end(self, sender: str, env: PbEnvelope) -> bool:
        """Process PbCallEnd — notify peers and clean up session."""
        end = env.call_end
        call_id = end.call_id

        session = self._calls.get(call_id)
        if session is None:
            # Tolerate end for unknown call (race condition)
            log.debug(f"Call {call_id}: end from {sender} — call not found (ok)")
            return True

        if not session.is_participant(sender):
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_NOT_PARTICIPANT,
                            f"You are not a participant in call {call_id}")
            return True

        duration = session.duration_sec
        log.info(f"Call {call_id}: {sender} ended "
                 f"(reason={end.reason}, duration={duration}s)")

        # Build end envelope with duration
        fwd = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick="",
        )
        fwd.call_end.call_id = call_id
        fwd.call_end.reason = end.reason
        fwd.call_end.duration_sec = duration
        fwd.timestamp = int(time.time() * 1000)

        # Forward to all peers
        self._forward_to_call_peers(session, sender, fwd)

        # For group calls, only remove the leaving participant
        if session.is_group and session.active_count > 2:
            del session.participants[sender]
            log.info(f"Call {call_id}: {sender} left group, "
                     f"{session.active_count} remaining")
        else:
            # 1-to-1 or last peer — end the call entirely
            self._cleanup_call(call_id)

        self.stats["calls_ended"] += 1
        return True

    # ------------------------------------------------------------------
    # Public: media control
    # ------------------------------------------------------------------

    def handle_call_media_control(self, sender: str, env: PbEnvelope) -> bool:
        """Forward PbCallMediaControl to call peers."""
        ctrl = env.call_media_control
        call_id = ctrl.call_id

        session = self._calls.get(call_id)
        if session is None:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_CALL_NOT_FOUND,
                            f"Call {call_id} not found")
            return True

        if not session.is_participant(sender):
            self._status_fn(sender, PbStatus.ERROR, self.ERR_VV_NOT_PARTICIPANT,
                            f"You are not a participant in call {call_id}")
            return True

        # Forward to peers
        fwd = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick=sender,
            to_nick="",
        )
        fwd.call_media_control.CopyFrom(ctrl)
        fwd.timestamp = int(time.time() * 1000)
        sent = self._forward_to_call_peers(session, sender, fwd)
        self.stats["media_control_forwarded"] += sent

        return True

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def prune_expired(self) -> int:
        """Remove calls that exceeded offer timeout or max duration.

        Returns number of calls pruned.
        """
        now = time.time()
        expired_ids: list[str] = []

        for call_id, session in self._calls.items():
            # Unanswered offer timeout
            if session.answered_at == 0.0:
                if (now - session.created_at) > self.config.offer_timeout_sec:
                    expired_ids.append(call_id)
                    continue
            # Max duration exceeded
            if session.answered_at > 0.0:
                if (now - session.answered_at) > self.config.max_duration_sec:
                    expired_ids.append(call_id)

        for call_id in expired_ids:
            session = self._calls.get(call_id)
            if session is None:
                continue

            if session.answered_at == 0.0:
                reason = PbCallEnd.TIMEOUT
                self.stats["calls_timed_out"] += 1
                log.info(f"Call {call_id}: offer timed out")
            else:
                reason = PbCallEnd.TIMEOUT
                self.stats["calls_ended"] += 1
                log.info(f"Call {call_id}: max duration exceeded")

            # Notify all participants
            end_env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick="",
                to_nick="",
            )
            end_env.call_end.call_id = call_id
            end_env.call_end.reason = reason
            end_env.call_end.duration_sec = session.duration_sec
            end_env.timestamp = int(time.time() * 1000)

            for nick in session.all_nicks:
                if self._is_pb_user(nick):
                    self._send_envelope(end_env, nick)

            self._cleanup_call(call_id)

        return len(expired_ids)

    def handle_user_disconnect(self, nick: str) -> None:
        """Clean up calls when a user disconnects."""
        # Find all calls this user is in
        call_ids = [
            cid for cid, s in self._calls.items() if s.is_participant(nick)
        ]
        for call_id in call_ids:
            session = self._calls.get(call_id)
            if session is None:
                continue

            # Notify peers that call ended due to disconnect
            end_env = WireCodec.make_envelope(
                route=PbEnvelope.DIRECT,
                from_nick=nick,
                to_nick="",
            )
            end_env.call_end.call_id = call_id
            end_env.call_end.reason = PbCallEnd.ERROR
            end_env.call_end.duration_sec = session.duration_sec
            end_env.timestamp = int(time.time() * 1000)
            self._forward_to_call_peers(session, nick, end_env)

            # Remove from group call or delete 1-to-1
            if session.is_group and session.active_count > 2:
                del session.participants[nick]
            else:
                self._cleanup_call(call_id)

    def _cleanup_call(self, call_id: str) -> None:
        """Remove a call session."""
        self._calls.pop(call_id, None)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_active_calls(self) -> list[CallSession]:
        """Get all active call sessions."""
        return list(self._calls.values())

    def get_call(self, call_id: str) -> CallSession | None:
        """Get a specific call session."""
        return self._calls.get(call_id)

    def get_call_count(self) -> int:
        """Total active calls."""
        return len(self._calls)

    def get_stats_summary(self) -> str:
        """Human-readable stats string."""
        return (
            f"Active calls: {len(self._calls)}\n"
            f"  Offered:     {self.stats['calls_offered']}\n"
            f"  Answered:    {self.stats['calls_answered']}\n"
            f"  Rejected:    {self.stats['calls_rejected']}\n"
            f"  Ended:       {self.stats['calls_ended']}\n"
            f"  Timed out:   {self.stats['calls_timed_out']}\n"
            f"  Candidates:  {self.stats['candidates_forwarded']}\n"
            f"  Media ctrl:  {self.stats['media_control_forwarded']}"
        )


# ============================================================================
# StreamManager
# ============================================================================

class StreamManager:
    """Hub-side stream broadcast manager.

    Parameters
    ----------
    config : StreamConfig
        Hub-level stream settings.
    send_fn : callable(data: str, nick: str) -> bool
        Send raw wire data to a user.
    status_fn : callable(nick: str, severity: int, code: int, message: str) -> None
        Send a PbStatus message to a user.
    get_user_class_fn : callable(nick: str) -> int
        Retrieve a user's class level.
    is_pb_user_fn : callable(nick: str) -> bool
        Check if a user supports NMDCpb.
    get_pb_nicks_fn : callable() -> list[str]
        Get all NMDCpb-capable nicks.
    """

    # Status codes for streams — 120-139 range
    ERR_STREAM_DISABLED = 120
    ERR_STREAM_CLASS = 121
    ERR_STREAM_LIMIT = 122
    ERR_STREAM_NOT_FOUND = 123
    ERR_STREAM_FULL = 124
    ERR_STREAM_ALREADY = 125
    ERR_STREAM_NOT_BROADCASTER = 126
    ERR_STREAM_NOT_VIEWER = 127

    def __init__(
        self,
        config: StreamConfig | None = None,
        send_fn: Callable[[str, str], bool] | None = None,
        status_fn: Callable[[str, int, int, str], None] | None = None,
        get_user_class_fn: Callable[[str], int] | None = None,
        is_pb_user_fn: Callable[[str], bool] | None = None,
        get_pb_nicks_fn: Callable[[], list[str]] | None = None,
    ):
        self.config = config or StreamConfig()
        self._send_fn = send_fn or self._noop_send
        self._status_fn = status_fn or self._noop_status
        self._get_user_class = get_user_class_fn or (lambda _: 1)
        self._is_pb_user = is_pb_user_fn or (lambda _: True)
        self._get_pb_nicks = get_pb_nicks_fn or (lambda: [])

        # stream_id → StreamSession
        self._streams: dict[str, StreamSession] = {}

        self.stats: dict[str, int] = {
            "streams_started": 0,
            "streams_stopped": 0,
            "stream_joins": 0,
            "stream_leaves": 0,
            "stream_updates": 0,
        }

    @staticmethod
    def _noop_send(data: str, nick: str) -> bool:
        return True

    @staticmethod
    def _noop_status(nick: str, severity: int, code: int, message: str) -> None:
        pass

    def _broadcast_to_pb(self, env: PbEnvelope, exclude: str = "") -> int:
        """Send envelope to all NMDCpb users (except exclude)."""
        wire = WireCodec.encode_text(env)
        sent = 0
        for nick in self._get_pb_nicks():
            if nick != exclude:
                if self._send_fn(wire, nick):
                    sent += 1
        return sent

    def _send_to_viewers(
        self, stream: StreamSession, env: PbEnvelope, exclude: str = ""
    ) -> int:
        """Send envelope to all stream viewers."""
        wire = WireCodec.encode_text(env)
        sent = 0
        for nick in stream.viewers:
            if nick != exclude and self._is_pb_user(nick):
                if self._send_fn(wire, nick):
                    sent += 1
        return sent

    # ------------------------------------------------------------------
    # Hub stream actions
    # ------------------------------------------------------------------

    def handle_hub_stream(self, sender: str, env: PbEnvelope) -> bool:
        """Route a PbHubStream message based on its action.

        Returns True if handled.
        """
        hs = env.hub_stream
        action = hs.action

        if action == PbHubStream.START_STREAM:
            return self._handle_start_stream(sender, hs)
        elif action == PbHubStream.STOP_STREAM:
            return self._handle_stop_stream(sender, hs)
        elif action == PbHubStream.JOIN_STREAM:
            return self._handle_join_stream(sender, hs)
        elif action == PbHubStream.LEAVE_STREAM:
            return self._handle_leave_stream(sender, hs)
        elif action == PbHubStream.STREAM_UPDATE:
            return self._handle_stream_update(sender, hs)
        else:
            # STREAM_AVAILABLE and STREAM_ENDED are hub→client only
            self._status_fn(sender, PbStatus.WARNING, 130,
                            f"Unexpected stream action from client: {action}")
            return True

    def _handle_start_stream(self, sender: str, hs: PbHubStream) -> bool:
        """Broadcaster requests to start a stream."""
        if not self.config.enabled:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_DISABLED,
                            "Hub streams are not enabled")
            return True

        # Check class
        user_class = self._get_user_class(sender)
        if user_class < self.config.min_class_broadcast:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_CLASS,
                            f"Insufficient class ({user_class}) to broadcast "
                            f"(need {self.config.min_class_broadcast})")
            return True

        # Check concurrent limit
        if len(self._streams) >= self.config.max_concurrent:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_LIMIT,
                            f"Hub stream limit reached ({self.config.max_concurrent})")
            return True

        # Check sender not already broadcasting
        for s in self._streams.values():
            if s.broadcaster == sender:
                self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_ALREADY,
                                "You are already broadcasting a stream")
                return True

        # Create stream
        stream_id = hs.stream_id or str(uuid.uuid4())
        session = StreamSession(
            stream_id=stream_id,
            broadcaster=sender,
            title=hs.title,
            description=hs.description,
            media=list(hs.media),
            bitrate=min(hs.bitrate, self.config.max_bitrate) if hs.bitrate else 0,
            max_viewers=min(hs.max_viewers, self.config.max_viewers) if hs.max_viewers
            else self.config.max_viewers,
        )
        self._streams[stream_id] = session
        self.stats["streams_started"] += 1
        log.info(f"Stream {stream_id}: {sender} started '{hs.title}'")

        # Announce STREAM_AVAILABLE to all PB users
        announce = WireCodec.make_envelope(
            route=PbEnvelope.BROADCAST,
            from_nick="",
        )
        announce.hub_stream.action = PbHubStream.STREAM_AVAILABLE
        announce.hub_stream.stream_id = stream_id
        announce.hub_stream.title = session.title
        announce.hub_stream.broadcaster_nick = sender
        announce.hub_stream.description = session.description
        announce.hub_stream.bitrate = session.bitrate
        announce.hub_stream.max_viewers = session.max_viewers
        announce.hub_stream.viewer_count = 0
        for mt in session.media:
            announce.hub_stream.media.append(mt)
        for c in hs.codecs:
            ci = announce.hub_stream.codecs.add()
            ci.CopyFrom(c)
        announce.timestamp = int(time.time() * 1000)
        self._broadcast_to_pb(announce)

        return True

    def _handle_stop_stream(self, sender: str, hs: PbHubStream) -> bool:
        """Broadcaster stops their stream."""
        stream_id = hs.stream_id
        session = self._streams.get(stream_id)

        if session is None:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_NOT_FOUND,
                            f"Stream {stream_id} not found")
            return True

        if session.broadcaster != sender:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_NOT_BROADCASTER,
                            "Only the broadcaster can stop the stream")
            return True

        log.info(f"Stream {stream_id}: {sender} stopped "
                 f"({session.viewer_count} viewers)")

        # Announce STREAM_ENDED to all PB users
        ended = WireCodec.make_envelope(
            route=PbEnvelope.BROADCAST,
            from_nick="",
        )
        ended.hub_stream.action = PbHubStream.STREAM_ENDED
        ended.hub_stream.stream_id = stream_id
        ended.hub_stream.broadcaster_nick = sender
        ended.hub_stream.viewer_count = session.viewer_count
        ended.timestamp = int(time.time() * 1000)
        self._broadcast_to_pb(ended)

        self._streams.pop(stream_id, None)
        self.stats["streams_stopped"] += 1
        return True

    def _handle_join_stream(self, sender: str, hs: PbHubStream) -> bool:
        """Viewer joins a stream."""
        stream_id = hs.stream_id
        session = self._streams.get(stream_id)

        if session is None:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_NOT_FOUND,
                            f"Stream {stream_id} not found")
            return True

        # Check viewer class
        user_class = self._get_user_class(sender)
        if user_class < self.config.min_class_view:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_CLASS,
                            f"Insufficient class to view stream")
            return True

        # Check capacity
        if session.viewer_count >= session.max_viewers:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_FULL,
                            f"Stream is full ({session.max_viewers} viewers max)")
            return True

        if sender in session.viewers:
            self._status_fn(sender, PbStatus.WARNING, self.ERR_STREAM_ALREADY,
                            "Already watching this stream")
            return True

        session.viewers.add(sender)
        self.stats["stream_joins"] += 1
        log.debug(f"Stream {stream_id}: {sender} joined "
                  f"({session.viewer_count} viewers)")

        # Send STREAM_UPDATE to broadcaster with new viewer count
        update = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="",
            to_nick=session.broadcaster,
        )
        update.hub_stream.action = PbHubStream.STREAM_UPDATE
        update.hub_stream.stream_id = stream_id
        update.hub_stream.viewer_count = session.viewer_count
        update.timestamp = int(time.time() * 1000)
        self._send_fn(WireCodec.encode_text(update), session.broadcaster)

        return True

    def _handle_leave_stream(self, sender: str, hs: PbHubStream) -> bool:
        """Viewer leaves a stream."""
        stream_id = hs.stream_id
        session = self._streams.get(stream_id)

        if session is None:
            # Tolerate leave for unknown stream
            return True

        if sender not in session.viewers:
            return True

        session.viewers.discard(sender)
        self.stats["stream_leaves"] += 1
        log.debug(f"Stream {stream_id}: {sender} left "
                  f"({session.viewer_count} viewers)")

        # Notify broadcaster
        update = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="",
            to_nick=session.broadcaster,
        )
        update.hub_stream.action = PbHubStream.STREAM_UPDATE
        update.hub_stream.stream_id = stream_id
        update.hub_stream.viewer_count = session.viewer_count
        update.timestamp = int(time.time() * 1000)
        self._send_fn(WireCodec.encode_text(update), session.broadcaster)

        return True

    def _handle_stream_update(self, sender: str, hs: PbHubStream) -> bool:
        """Broadcaster updates stream metadata (title, bitrate, etc.)."""
        stream_id = hs.stream_id
        session = self._streams.get(stream_id)

        if session is None:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_NOT_FOUND,
                            f"Stream {stream_id} not found")
            return True

        if session.broadcaster != sender:
            self._status_fn(sender, PbStatus.ERROR, self.ERR_STREAM_NOT_BROADCASTER,
                            "Only the broadcaster can update the stream")
            return True

        # Apply updates
        if hs.title:
            session.title = hs.title
        if hs.description:
            session.description = hs.description
        if hs.bitrate:
            session.bitrate = min(hs.bitrate, self.config.max_bitrate)

        self.stats["stream_updates"] += 1

        # Broadcast update to viewers
        upd = WireCodec.make_envelope(
            route=PbEnvelope.BROADCAST,
            from_nick="",
        )
        upd.hub_stream.action = PbHubStream.STREAM_UPDATE
        upd.hub_stream.stream_id = stream_id
        upd.hub_stream.title = session.title
        upd.hub_stream.description = session.description
        upd.hub_stream.broadcaster_nick = sender
        upd.hub_stream.viewer_count = session.viewer_count
        upd.hub_stream.bitrate = session.bitrate
        upd.timestamp = int(time.time() * 1000)
        self._broadcast_to_pb(upd, exclude=sender)

        return True

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def handle_user_disconnect(self, nick: str) -> None:
        """Clean up streams when a user disconnects."""
        # Check if user is a broadcaster
        broadcaster_streams = [
            sid for sid, s in self._streams.items() if s.broadcaster == nick
        ]
        for stream_id in broadcaster_streams:
            session = self._streams.get(stream_id)
            if session is None:
                continue
            # Announce STREAM_ENDED
            ended = WireCodec.make_envelope(
                route=PbEnvelope.BROADCAST,
                from_nick="",
            )
            ended.hub_stream.action = PbHubStream.STREAM_ENDED
            ended.hub_stream.stream_id = stream_id
            ended.hub_stream.broadcaster_nick = nick
            ended.hub_stream.viewer_count = session.viewer_count
            ended.timestamp = int(time.time() * 1000)
            self._broadcast_to_pb(ended)
            self._streams.pop(stream_id, None)

        # Remove from viewer lists
        for session in self._streams.values():
            session.viewers.discard(nick)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_active_streams(self) -> list[StreamSession]:
        """Get all active stream sessions."""
        return list(self._streams.values())

    def get_stream(self, stream_id: str) -> StreamSession | None:
        """Get a specific stream session."""
        return self._streams.get(stream_id)

    def get_stream_count(self) -> int:
        """Total active streams."""
        return len(self._streams)

    def get_stats_summary(self) -> str:
        """Human-readable stats string."""
        return (
            f"Active streams: {len(self._streams)}\n"
            f"  Started:  {self.stats['streams_started']}\n"
            f"  Stopped:  {self.stats['streams_stopped']}\n"
            f"  Joins:    {self.stats['stream_joins']}\n"
            f"  Leaves:   {self.stats['stream_leaves']}\n"
            f"  Updates:  {self.stats['stream_updates']}"
        )
