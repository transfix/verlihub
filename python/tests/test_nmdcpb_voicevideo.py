"""
Unit and integration tests for NMDCpb VoiceVideo (Phase 5).

Tests the CallManager, StreamManager, and hub routing for:
  - Voice/video call signaling (offer/answer/candidate/end/media-control)
  - Hub-wide stream broadcasting (start/stop/join/leave/update)
  - Permission checks, concurrent limits, timeouts, disconnect cleanup
  - Admin commands

Test classes:
  TestCallConfig             — CallConfig / StreamConfig defaults & overrides
  TestCallManager            — Core call lifecycle: offer → answer → end
  TestCallPermissions        — Class, concurrent, hub-wide limits
  TestCallBusy               — Target busy when at max concurrent
  TestCallGroupCalls         — Group call SFU signaling
  TestCallTimeout            — Offer timeout and max duration pruning
  TestCallDisconnect         — User disconnect cleanup
  TestCallCandidate          — ICE candidate forwarding
  TestCallMediaControl       — In-call mute/unmute forwarding
  TestStreamManager          — Stream lifecycle: start → join → leave → stop
  TestStreamPermissions      — Broadcast/view class checks, limits
  TestStreamDisconnect       — Broadcaster/viewer disconnect cleanup
  TestStreamUpdate           — Broadcaster metadata updates
  TestHubRoutingCalls        — _route_call_signaling integration
  TestHubRoutingStreams      — _route_hub_stream integration
  TestHubAdminCommands       — +nmdcpb calls / streams admin output
  TestHubTimerPruning        — OnTimer call pruning
  TestHubOnUnLoad            — Cleanup on unload
"""

import time
import unittest
from unittest.mock import patch, MagicMock

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbCallOffer,
    PbCallAnswer,
    PbCallCandidate,
    PbCallEnd,
    PbCallMediaControl,
    PbHubStream,
    PbStatus,
    CodecInfo,
)
from verlihub.client.nmdcpb.wire import WireCodec
from verlihub.client.nmdcpb.call_manager import (
    CallManager,
    CallConfig,
    CallSession,
    StreamManager,
    StreamConfig,
    StreamSession,
)


HP = "verlihub.client.nmdcpb.hub_plugin"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class VVTestHelper:
    """Captures messages sent to users during VoiceVideo operations."""

    def __init__(self):
        self.sent: dict[str, list[str]] = {}
        self.statuses: dict[str, list[tuple]] = {}
        self.user_classes: dict[str, int] = {}
        self.pb_users: set[str] = set()

    def send_fn(self, data: str, nick: str) -> bool:
        self.sent.setdefault(nick, []).append(data)
        return True

    def status_fn(self, nick: str, severity: int, code: int, message: str) -> None:
        self.statuses.setdefault(nick, []).append((severity, code, message))

    def get_user_class(self, nick: str) -> int:
        return self.user_classes.get(nick, 1)

    def is_pb_user(self, nick: str) -> bool:
        return nick in self.pb_users

    def get_all_nicks(self) -> list[str]:
        return list(self.pb_users)

    def get_pb_nicks(self) -> list[str]:
        return list(self.pb_users)

    def clear(self):
        self.sent.clear()
        self.statuses.clear()

    def decode_last(self, nick: str) -> PbEnvelope | None:
        msgs = self.sent.get(nick, [])
        if not msgs:
            return None
        return WireCodec.decode(msgs[-1])

    def decode_all(self, nick: str) -> list[PbEnvelope]:
        result = []
        for wire in self.sent.get(nick, []):
            env = WireCodec.decode(wire)
            if env:
                result.append(env)
        return result


def _make_call_manager(helper: VVTestHelper, **overrides) -> CallManager:
    """Create a CallManager with test defaults."""
    cfg = CallConfig(enabled=True, **overrides)
    return CallManager(
        config=cfg,
        send_fn=helper.send_fn,
        status_fn=helper.status_fn,
        get_user_class_fn=helper.get_user_class,
        is_pb_user_fn=helper.is_pb_user,
        get_all_nicks_fn=helper.get_all_nicks,
    )


def _make_stream_manager(helper: VVTestHelper, **overrides) -> StreamManager:
    """Create a StreamManager with test defaults."""
    cfg = StreamConfig(enabled=True, **overrides)
    return StreamManager(
        config=cfg,
        send_fn=helper.send_fn,
        status_fn=helper.status_fn,
        get_user_class_fn=helper.get_user_class,
        is_pb_user_fn=helper.is_pb_user,
        get_pb_nicks_fn=helper.get_pb_nicks,
    )


def _make_offer_env(sender: str, target: str, call_id: str = "test-call-1",
                    is_group: bool = False) -> PbEnvelope:
    """Build a PbCallOffer envelope."""
    env = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT,
        from_nick=sender,
        to_nick=target,
    )
    env.call_offer.target_nick = target
    env.call_offer.call_id = call_id
    env.call_offer.is_group = is_group
    env.call_offer.media.append(PbCallOffer.AUDIO)
    c = env.call_offer.codecs.add()
    c.name = "opus"
    c.clock_rate = 48000
    c.channels = 2
    env.timestamp = int(time.time() * 1000)
    return env


def _make_answer_env(sender: str, initiator: str, call_id: str,
                     accepted: bool = True) -> PbEnvelope:
    """Build a PbCallAnswer envelope."""
    env = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT,
        from_nick=sender,
        to_nick=initiator,
    )
    env.call_answer.call_id = call_id
    env.call_answer.accepted = accepted
    if not accepted:
        env.call_answer.reject_reason = "busy"
    env.timestamp = int(time.time() * 1000)
    return env


def _make_end_env(sender: str, call_id: str,
                  reason: int = PbCallEnd.NORMAL) -> PbEnvelope:
    """Build a PbCallEnd envelope."""
    env = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT,
        from_nick=sender,
    )
    env.call_end.call_id = call_id
    env.call_end.reason = reason
    env.timestamp = int(time.time() * 1000)
    return env


def _make_candidate_env(sender: str, call_id: str,
                        candidate: str = "candidate:1 1 udp 2130706431 192.168.1.1 5000 typ host") -> PbEnvelope:
    env = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT,
        from_nick=sender,
    )
    env.call_candidate.call_id = call_id
    env.call_candidate.candidate = candidate
    env.timestamp = int(time.time() * 1000)
    return env


def _make_media_control_env(sender: str, call_id: str,
                            audio_muted: bool = False,
                            video_muted: bool = False) -> PbEnvelope:
    env = WireCodec.make_envelope(
        route=PbEnvelope.DIRECT,
        from_nick=sender,
    )
    env.call_media_control.call_id = call_id
    env.call_media_control.audio_muted = audio_muted
    env.call_media_control.video_muted = video_muted
    env.timestamp = int(time.time() * 1000)
    return env


def _make_stream_env(sender: str, action: int, stream_id: str = "",
                     title: str = "", **kwargs) -> PbEnvelope:
    """Build a PbHubStream envelope."""
    env = WireCodec.make_envelope(
        route=PbEnvelope.HUB,
        from_nick=sender,
    )
    env.hub_stream.action = action
    if stream_id:
        env.hub_stream.stream_id = stream_id
    if title:
        env.hub_stream.title = title
    for k, v in kwargs.items():
        setattr(env.hub_stream, k, v)
    env.timestamp = int(time.time() * 1000)
    return env


# ===========================================================================
# CallConfig / StreamConfig
# ===========================================================================

class TestCallConfig(unittest.TestCase):
    """Test dataclass defaults and overrides."""

    def test_call_config_defaults(self):
        cfg = CallConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.max_participants, 8)
        self.assertEqual(cfg.min_class, 1)
        self.assertEqual(cfg.max_concurrent_per_user, 2)
        self.assertEqual(cfg.max_concurrent_hub, 20)
        self.assertEqual(cfg.max_duration_sec, 7200)
        self.assertEqual(cfg.max_bitrate, 512_000)
        self.assertEqual(cfg.offer_timeout_sec, 30)

    def test_call_config_overrides(self):
        cfg = CallConfig(enabled=True, max_participants=16, min_class=3)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.max_participants, 16)
        self.assertEqual(cfg.min_class, 3)

    def test_stream_config_defaults(self):
        cfg = StreamConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.max_concurrent, 3)
        self.assertEqual(cfg.max_viewers, 100)
        self.assertEqual(cfg.min_class_broadcast, 3)
        self.assertEqual(cfg.min_class_view, 0)
        self.assertEqual(cfg.max_bitrate, 256_000)

    def test_stream_config_overrides(self):
        cfg = StreamConfig(enabled=True, max_concurrent=10)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.max_concurrent, 10)


# ===========================================================================
# CallManager — core lifecycle
# ===========================================================================

class TestCallManager(unittest.TestCase):
    """Test basic call lifecycle: offer → answer → end."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"alice", "bob", "charlie"}
        self.h.user_classes = {"alice": 3, "bob": 3, "charlie": 3}
        self.cm = _make_call_manager(self.h)

    def test_offer_creates_session(self):
        env = _make_offer_env("alice", "bob", "call-1")
        result = self.cm.handle_call_offer("alice", env)
        self.assertTrue(result)
        self.assertEqual(self.cm.get_call_count(), 1)
        session = self.cm.get_call("call-1")
        self.assertIsNotNone(session)
        self.assertEqual(session.initiator, "alice")
        self.assertTrue(session.is_participant("alice"))
        self.assertTrue(session.is_participant("bob"))
        # alice is active (answered), bob is ringing
        self.assertTrue(session.is_active_participant("alice"))
        self.assertFalse(session.is_active_participant("bob"))

    def test_offer_forwarded_to_target(self):
        env = _make_offer_env("alice", "bob", "call-1")
        self.cm.handle_call_offer("alice", env)
        # Bob should receive the offer
        bob_env = self.h.decode_last("bob")
        self.assertIsNotNone(bob_env)
        self.assertTrue(bob_env.HasField("call_offer"))
        self.assertEqual(bob_env.call_offer.call_id, "call-1")
        self.assertEqual(bob_env.call_offer.target_nick, "bob")

    def test_answer_accepted(self):
        offer = _make_offer_env("alice", "bob", "call-1")
        self.cm.handle_call_offer("alice", offer)
        self.h.clear()

        answer = _make_answer_env("bob", "alice", "call-1", accepted=True)
        self.cm.handle_call_answer("bob", answer)

        session = self.cm.get_call("call-1")
        self.assertIsNotNone(session)
        self.assertTrue(session.is_active_participant("bob"))
        self.assertGreater(session.answered_at, 0)

        # Alice should receive the answer
        alice_env = self.h.decode_last("alice")
        self.assertIsNotNone(alice_env)
        self.assertTrue(alice_env.HasField("call_answer"))
        self.assertTrue(alice_env.call_answer.accepted)

    def test_answer_rejected_cleans_up(self):
        offer = _make_offer_env("alice", "bob", "call-1")
        self.cm.handle_call_offer("alice", offer)
        self.h.clear()

        answer = _make_answer_env("bob", "alice", "call-1", accepted=False)
        self.cm.handle_call_answer("bob", answer)

        # 1-to-1 rejected call should be cleaned up
        self.assertIsNone(self.cm.get_call("call-1"))
        self.assertEqual(self.cm.get_call_count(), 0)
        self.assertEqual(self.cm.stats["calls_rejected"], 1)

    def test_end_call_cleanup(self):
        offer = _make_offer_env("alice", "bob", "call-1")
        self.cm.handle_call_offer("alice", offer)
        answer = _make_answer_env("bob", "alice", "call-1")
        self.cm.handle_call_answer("bob", answer)
        self.h.clear()

        end = _make_end_env("alice", "call-1")
        self.cm.handle_call_end("alice", end)

        self.assertIsNone(self.cm.get_call("call-1"))
        self.assertEqual(self.cm.stats["calls_ended"], 1)

        # Bob should receive call_end
        bob_env = self.h.decode_last("bob")
        self.assertIsNotNone(bob_env)
        self.assertTrue(bob_env.HasField("call_end"))
        self.assertEqual(bob_env.call_end.reason, PbCallEnd.NORMAL)

    def test_end_unknown_call_is_tolerated(self):
        """End for unknown call should be silently ignored (race condition)."""
        end = _make_end_env("alice", "nonexistent-call")
        result = self.cm.handle_call_end("alice", end)
        self.assertTrue(result)
        # No error status sent
        self.assertEqual(len(self.h.statuses.get("alice", [])), 0)

    def test_stats_summary(self):
        summary = self.cm.get_stats_summary()
        self.assertIn("Active calls:", summary)
        self.assertIn("Offered:", summary)

    def test_get_active_calls_empty(self):
        self.assertEqual(self.cm.get_active_calls(), [])

    def test_call_session_properties(self):
        s = CallSession(call_id="x", initiator="alice")
        self.assertEqual(s.active_count, 0)
        self.assertEqual(s.all_nicks, set())
        self.assertEqual(s.duration_sec, 0)
        self.assertFalse(s.is_participant("alice"))

        s.participants["alice"] = True
        s.participants["bob"] = False
        self.assertEqual(s.active_count, 1)
        self.assertEqual(s.all_nicks, {"alice", "bob"})
        self.assertTrue(s.is_active_participant("alice"))
        self.assertFalse(s.is_active_participant("bob"))


# ===========================================================================
# Call permissions
# ===========================================================================

class TestCallPermissions(unittest.TestCase):
    """Test class, concurrent, and hub-wide call limits."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"alice", "bob", "charlie"}
        self.h.user_classes = {"alice": 3, "bob": 3, "charlie": 1}

    def test_disabled_call_manager(self):
        cm = CallManager(config=CallConfig(enabled=False))
        env = _make_offer_env("alice", "bob")
        result = cm.handle_call_offer("alice", env)
        self.assertTrue(result)  # handled but rejected

    def test_caller_class_too_low(self):
        cm = _make_call_manager(self.h, min_class=5)
        env = _make_offer_env("alice", "bob", "call-1")
        cm.handle_call_offer("alice", env)
        statuses = self.h.statuses.get("alice", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("Insufficient class", statuses[0][2])

    def test_target_class_too_low(self):
        self.h.user_classes["bob"] = 0
        cm = _make_call_manager(self.h, min_class=1)
        env = _make_offer_env("alice", "bob", "call-1")
        cm.handle_call_offer("alice", env)
        statuses = self.h.statuses.get("alice", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("insufficient class", statuses[0][2].lower())

    def test_self_call_rejected(self):
        cm = _make_call_manager(self.h)
        env = _make_offer_env("alice", "alice", "call-1")
        cm.handle_call_offer("alice", env)
        statuses = self.h.statuses.get("alice", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("yourself", statuses[0][2])

    def test_target_not_online(self):
        cm = _make_call_manager(self.h)
        env = _make_offer_env("alice", "nobody", "call-1")
        cm.handle_call_offer("alice", env)
        statuses = self.h.statuses.get("alice", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("not online", statuses[0][2])

    def test_target_not_pb_user(self):
        self.h.pb_users = {"alice"}  # bob is in get_all_nicks but not pb user
        # Override get_all_nicks to include bob
        cm = _make_call_manager(self.h)
        cm._get_all_nicks = lambda: ["alice", "bob"]
        env = _make_offer_env("alice", "bob", "call-1")
        cm.handle_call_offer("alice", env)
        statuses = self.h.statuses.get("alice", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("NMDCpb", statuses[0][2])

    def test_per_user_concurrent_limit(self):
        cm = _make_call_manager(self.h, max_concurrent_per_user=1)
        # First call succeeds
        env1 = _make_offer_env("alice", "bob", "call-1")
        cm.handle_call_offer("alice", env1)
        self.assertEqual(cm.get_call_count(), 1)
        self.h.clear()

        # Second call should be rejected due to per-user limit
        env2 = _make_offer_env("alice", "charlie", "call-2")
        cm.handle_call_offer("alice", env2)
        statuses = self.h.statuses.get("alice", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("concurrent", statuses[0][2].lower())
        self.assertEqual(cm.get_call_count(), 1)  # still just one

    def test_hub_wide_limit(self):
        cm = _make_call_manager(self.h, max_concurrent_hub=1)
        # Bob calls alice
        env1 = _make_offer_env("bob", "alice", "call-1")
        cm.handle_call_offer("bob", env1)
        self.assertEqual(cm.get_call_count(), 1)
        self.h.clear()

        # Charlie tries to call alice — hub limit reached
        # (even though charlie isn't in any call himself)
        self.h.pb_users.add("dave")
        self.h.user_classes["dave"] = 3
        env2 = _make_offer_env("charlie", "dave", "call-2")
        cm._get_all_nicks = lambda: ["alice", "bob", "charlie", "dave"]
        cm.handle_call_offer("charlie", env2)
        statuses = self.h.statuses.get("charlie", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("Hub call limit", statuses[0][2])

    def test_answer_for_nonexistent_call(self):
        cm = _make_call_manager(self.h)
        answer = _make_answer_env("bob", "alice", "no-such-call")
        cm.handle_call_answer("bob", answer)
        statuses = self.h.statuses.get("bob", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("not found", statuses[0][2])

    def test_answer_from_non_participant(self):
        cm = _make_call_manager(self.h)
        offer = _make_offer_env("alice", "bob", "call-1")
        cm.handle_call_offer("alice", offer)
        self.h.clear()

        answer = _make_answer_env("charlie", "alice", "call-1")
        cm.handle_call_answer("charlie", answer)
        statuses = self.h.statuses.get("charlie", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("not a participant", statuses[0][2])


# ===========================================================================
# Call busy
# ===========================================================================

class TestCallBusy(unittest.TestCase):
    """Test BUSY response when target is at max concurrent calls."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"alice", "bob", "charlie"}
        self.h.user_classes = {"alice": 3, "bob": 3, "charlie": 3}

    def test_target_busy_sends_busy_end(self):
        cm = _make_call_manager(self.h, max_concurrent_per_user=1)
        # Bob is already in a call
        offer1 = _make_offer_env("charlie", "bob", "call-1")
        cm.handle_call_offer("charlie", offer1)
        self.h.clear()

        # Alice tries to call bob — should get BUSY
        offer2 = _make_offer_env("alice", "bob", "call-2")
        cm.handle_call_offer("alice", offer2)

        # Alice should receive a PbCallEnd with BUSY
        alice_env = self.h.decode_last("alice")
        self.assertIsNotNone(alice_env)
        self.assertTrue(alice_env.HasField("call_end"))
        self.assertEqual(alice_env.call_end.reason, PbCallEnd.BUSY)
        self.assertEqual(alice_env.call_end.call_id, "call-2")

        # Call-2 should NOT be created
        self.assertIsNone(cm.get_call("call-2"))
        self.assertEqual(cm.get_call_count(), 1)


# ===========================================================================
# Group calls
# ===========================================================================

class TestCallGroupCalls(unittest.TestCase):
    """Test group call (SFU) signaling."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"alice", "bob", "charlie", "dave"}
        for n in self.h.pb_users:
            self.h.user_classes[n] = 3
        self.cm = _make_call_manager(self.h)

    def test_group_offer_creates_session(self):
        env = _make_offer_env("alice", "bob", "group-1", is_group=True)
        env.call_offer.group_id = "my-group"
        self.cm.handle_call_offer("alice", env)
        session = self.cm.get_call("group-1")
        self.assertIsNotNone(session)
        self.assertTrue(session.is_group)
        self.assertEqual(session.group_id, "my-group")

    def test_group_answer_forwarded_to_all_peers(self):
        # Alice offers to bob (group call)
        env = _make_offer_env("alice", "bob", "group-1", is_group=True)
        self.cm.handle_call_offer("alice", env)

        # Manually add charlie to the group
        session = self.cm.get_call("group-1")
        session.participants["charlie"] = True
        self.h.clear()

        # Bob accepts
        answer = _make_answer_env("bob", "alice", "group-1", accepted=True)
        self.cm.handle_call_answer("bob", answer)

        # Both alice and charlie should receive the answer (group broadcast)
        for nick in ("alice", "charlie"):
            env = self.h.decode_last(nick)
            self.assertIsNotNone(env, f"{nick} should have received answer")
            self.assertTrue(env.HasField("call_answer"))

    def test_group_end_removes_only_leaving_participant(self):
        """In a group call with 3+ active, one leaving doesn't end the call."""
        env = _make_offer_env("alice", "bob", "group-1", is_group=True)
        self.cm.handle_call_offer("alice", env)

        session = self.cm.get_call("group-1")
        session.participants["charlie"] = True
        session.participants["bob"] = True
        session.answered_at = time.time()
        self.h.clear()

        # Charlie leaves
        end = _make_end_env("charlie", "group-1")
        self.cm.handle_call_end("charlie", end)

        # Call should still exist with alice and bob
        session = self.cm.get_call("group-1")
        self.assertIsNotNone(session)
        self.assertFalse(session.is_participant("charlie"))
        self.assertTrue(session.is_participant("alice"))
        self.assertTrue(session.is_participant("bob"))

    def test_group_last_peer_leaves_ends_call(self):
        """When only 2 remain in group and one ends, call is cleaned up."""
        env = _make_offer_env("alice", "bob", "group-1", is_group=True)
        self.cm.handle_call_offer("alice", env)
        session = self.cm.get_call("group-1")
        session.participants["bob"] = True
        session.answered_at = time.time()
        self.h.clear()

        # Bob ends (only alice + bob remain)
        end = _make_end_env("bob", "group-1")
        self.cm.handle_call_end("bob", end)
        self.assertIsNone(self.cm.get_call("group-1"))


# ===========================================================================
# Call timeout / duration pruning
# ===========================================================================

class TestCallTimeout(unittest.TestCase):
    """Test offer timeout and max duration pruning."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"alice", "bob"}
        self.h.user_classes = {"alice": 3, "bob": 3}

    def test_unanswered_offer_times_out(self):
        cm = _make_call_manager(self.h, offer_timeout_sec=5)
        env = _make_offer_env("alice", "bob", "call-1")
        cm.handle_call_offer("alice", env)
        session = cm.get_call("call-1")
        # Fake the created_at to be 10 seconds ago
        session.created_at = time.time() - 10
        self.h.clear()

        pruned = cm.prune_expired()
        self.assertEqual(pruned, 1)
        self.assertIsNone(cm.get_call("call-1"))
        self.assertEqual(cm.stats["calls_timed_out"], 1)

        # Both alice and bob should receive TIMEOUT call_end
        for nick in ("alice", "bob"):
            envs = self.h.decode_all(nick)
            self.assertTrue(any(
                e.HasField("call_end") and e.call_end.reason == PbCallEnd.TIMEOUT
                for e in envs
            ), f"{nick} should have received TIMEOUT")

    def test_max_duration_exceeded(self):
        cm = _make_call_manager(self.h, max_duration_sec=60)
        env = _make_offer_env("alice", "bob", "call-1")
        cm.handle_call_offer("alice", env)
        answer = _make_answer_env("bob", "alice", "call-1")
        cm.handle_call_answer("bob", answer)

        session = cm.get_call("call-1")
        session.answered_at = time.time() - 120  # 2 min ago
        self.h.clear()

        pruned = cm.prune_expired()
        self.assertEqual(pruned, 1)
        self.assertIsNone(cm.get_call("call-1"))

    def test_no_pruning_when_within_limits(self):
        cm = _make_call_manager(self.h, offer_timeout_sec=30, max_duration_sec=7200)
        env = _make_offer_env("alice", "bob", "call-1")
        cm.handle_call_offer("alice", env)
        pruned = cm.prune_expired()
        self.assertEqual(pruned, 0)
        self.assertIsNotNone(cm.get_call("call-1"))


# ===========================================================================
# Call disconnect
# ===========================================================================

class TestCallDisconnect(unittest.TestCase):
    """Test user disconnect cleanup."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"alice", "bob", "charlie"}
        for n in self.h.pb_users:
            self.h.user_classes[n] = 3
        self.cm = _make_call_manager(self.h)

    def test_disconnect_ends_1to1_call(self):
        offer = _make_offer_env("alice", "bob", "call-1")
        self.cm.handle_call_offer("alice", offer)
        answer = _make_answer_env("bob", "alice", "call-1")
        self.cm.handle_call_answer("bob", answer)
        self.h.clear()

        self.cm.handle_user_disconnect("alice")

        self.assertIsNone(self.cm.get_call("call-1"))
        # Bob should receive ERROR call_end
        bob_env = self.h.decode_last("bob")
        self.assertIsNotNone(bob_env)
        self.assertTrue(bob_env.HasField("call_end"))
        self.assertEqual(bob_env.call_end.reason, PbCallEnd.ERROR)

    def test_disconnect_removes_from_group_call(self):
        """Disconnect from group with 3+ active just removes participant."""
        offer = _make_offer_env("alice", "bob", "group-1", is_group=True)
        self.cm.handle_call_offer("alice", offer)
        session = self.cm.get_call("group-1")
        session.participants["charlie"] = True
        session.participants["bob"] = True
        session.answered_at = time.time()
        self.h.clear()

        self.cm.handle_user_disconnect("charlie")

        session = self.cm.get_call("group-1")
        self.assertIsNotNone(session)
        self.assertFalse(session.is_participant("charlie"))
        self.assertTrue(session.is_participant("alice"))

    def test_disconnect_user_not_in_any_call(self):
        """Disconnect for user not in any call is a no-op."""
        self.cm.handle_user_disconnect("charlie")
        self.assertEqual(self.cm.get_call_count(), 0)


# ===========================================================================
# Candidate forwarding
# ===========================================================================

class TestCallCandidate(unittest.TestCase):
    """Test ICE candidate forwarding."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"alice", "bob"}
        self.h.user_classes = {"alice": 3, "bob": 3}
        self.cm = _make_call_manager(self.h)
        # Establish a call
        offer = _make_offer_env("alice", "bob", "call-1")
        self.cm.handle_call_offer("alice", offer)
        answer = _make_answer_env("bob", "alice", "call-1")
        self.cm.handle_call_answer("bob", answer)
        self.h.clear()

    def test_candidate_forwarded_to_peer(self):
        cand = _make_candidate_env("alice", "call-1")
        self.cm.handle_call_candidate("alice", cand)

        bob_env = self.h.decode_last("bob")
        self.assertIsNotNone(bob_env)
        self.assertTrue(bob_env.HasField("call_candidate"))
        self.assertIn("candidate:1", bob_env.call_candidate.candidate)
        self.assertEqual(self.cm.stats["candidates_forwarded"], 1)

    def test_candidate_unknown_call(self):
        cand = _make_candidate_env("alice", "no-such-call")
        self.cm.handle_call_candidate("alice", cand)
        statuses = self.h.statuses.get("alice", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("not found", statuses[0][2])

    def test_candidate_non_participant(self):
        self.h.pb_users.add("charlie")
        self.h.user_classes["charlie"] = 3
        cand = _make_candidate_env("charlie", "call-1")
        self.cm.handle_call_candidate("charlie", cand)
        statuses = self.h.statuses.get("charlie", [])
        self.assertIn("not a participant", statuses[0][2])


# ===========================================================================
# Media control forwarding
# ===========================================================================

class TestCallMediaControl(unittest.TestCase):
    """Test in-call mute/unmute forwarding."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"alice", "bob"}
        self.h.user_classes = {"alice": 3, "bob": 3}
        self.cm = _make_call_manager(self.h)
        offer = _make_offer_env("alice", "bob", "call-1")
        self.cm.handle_call_offer("alice", offer)
        answer = _make_answer_env("bob", "alice", "call-1")
        self.cm.handle_call_answer("bob", answer)
        self.h.clear()

    def test_audio_mute_forwarded(self):
        ctrl = _make_media_control_env("alice", "call-1", audio_muted=True)
        self.cm.handle_call_media_control("alice", ctrl)

        bob_env = self.h.decode_last("bob")
        self.assertIsNotNone(bob_env)
        self.assertTrue(bob_env.HasField("call_media_control"))
        self.assertTrue(bob_env.call_media_control.audio_muted)
        self.assertEqual(self.cm.stats["media_control_forwarded"], 1)

    def test_video_mute_forwarded(self):
        ctrl = _make_media_control_env("bob", "call-1", video_muted=True)
        self.cm.handle_call_media_control("bob", ctrl)

        alice_env = self.h.decode_last("alice")
        self.assertIsNotNone(alice_env)
        self.assertTrue(alice_env.call_media_control.video_muted)

    def test_media_control_unknown_call(self):
        ctrl = _make_media_control_env("alice", "no-call")
        self.cm.handle_call_media_control("alice", ctrl)
        statuses = self.h.statuses.get("alice", [])
        self.assertIn("not found", statuses[0][2])


# ===========================================================================
# StreamManager — core lifecycle
# ===========================================================================

class TestStreamManager(unittest.TestCase):
    """Test stream lifecycle: start → join → leave → stop."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"streamer", "viewer1", "viewer2"}
        self.h.user_classes = {"streamer": 5, "viewer1": 1, "viewer2": 1}
        self.sm = _make_stream_manager(self.h)

    def test_start_stream(self):
        env = _make_stream_env("streamer", PbHubStream.START_STREAM,
                               stream_id="stream-1", title="My Stream",
                               description="Test broadcast")
        env.hub_stream.media.append(PbCallOffer.AUDIO)
        env.hub_stream.bitrate = 128000
        result = self.sm.handle_hub_stream("streamer", env)
        self.assertTrue(result)
        self.assertEqual(self.sm.get_stream_count(), 1)
        session = self.sm.get_stream("stream-1")
        self.assertIsNotNone(session)
        self.assertEqual(session.broadcaster, "streamer")
        self.assertEqual(session.title, "My Stream")
        self.assertEqual(self.sm.stats["streams_started"], 1)

    def test_start_announces_to_all(self):
        env = _make_stream_env("streamer", PbHubStream.START_STREAM,
                               stream_id="stream-1", title="My Stream")
        self.sm.handle_hub_stream("streamer", env)

        # All PB users (except streamer) should receive STREAM_AVAILABLE
        for nick in ("viewer1", "viewer2"):
            envs = self.h.decode_all(nick)
            available = [e for e in envs if e.HasField("hub_stream")
                         and e.hub_stream.action == PbHubStream.STREAM_AVAILABLE]
            self.assertEqual(len(available), 1, f"{nick} should get STREAM_AVAILABLE")
            self.assertEqual(available[0].hub_stream.stream_id, "stream-1")

    def test_join_stream(self):
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="stream-1", title="My Stream")
        self.sm.handle_hub_stream("streamer", start)
        self.h.clear()

        join = _make_stream_env("viewer1", PbHubStream.JOIN_STREAM,
                                stream_id="stream-1")
        self.sm.handle_hub_stream("viewer1", join)

        session = self.sm.get_stream("stream-1")
        self.assertIn("viewer1", session.viewers)
        self.assertEqual(session.viewer_count, 1)
        self.assertEqual(self.sm.stats["stream_joins"], 1)

        # Streamer should be notified of viewer count update
        streamer_env = self.h.decode_last("streamer")
        self.assertIsNotNone(streamer_env)
        self.assertEqual(streamer_env.hub_stream.action, PbHubStream.STREAM_UPDATE)
        self.assertEqual(streamer_env.hub_stream.viewer_count, 1)

    def test_leave_stream(self):
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="stream-1", title="My Stream")
        self.sm.handle_hub_stream("streamer", start)
        join = _make_stream_env("viewer1", PbHubStream.JOIN_STREAM,
                                stream_id="stream-1")
        self.sm.handle_hub_stream("viewer1", join)
        self.h.clear()

        leave = _make_stream_env("viewer1", PbHubStream.LEAVE_STREAM,
                                 stream_id="stream-1")
        self.sm.handle_hub_stream("viewer1", leave)

        session = self.sm.get_stream("stream-1")
        self.assertNotIn("viewer1", session.viewers)
        self.assertEqual(session.viewer_count, 0)
        self.assertEqual(self.sm.stats["stream_leaves"], 1)

    def test_stop_stream(self):
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="stream-1", title="My Stream")
        self.sm.handle_hub_stream("streamer", start)
        join = _make_stream_env("viewer1", PbHubStream.JOIN_STREAM,
                                stream_id="stream-1")
        self.sm.handle_hub_stream("viewer1", join)
        self.h.clear()

        stop = _make_stream_env("streamer", PbHubStream.STOP_STREAM,
                                stream_id="stream-1")
        self.sm.handle_hub_stream("streamer", stop)

        self.assertIsNone(self.sm.get_stream("stream-1"))
        self.assertEqual(self.sm.stats["streams_stopped"], 1)

        # All PB users should receive STREAM_ENDED
        for nick in ("viewer1", "viewer2"):
            envs = self.h.decode_all(nick)
            ended = [e for e in envs if e.HasField("hub_stream")
                     and e.hub_stream.action == PbHubStream.STREAM_ENDED]
            self.assertEqual(len(ended), 1)

    def test_stop_stream_by_non_broadcaster(self):
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="stream-1", title="My Stream")
        self.sm.handle_hub_stream("streamer", start)
        self.h.clear()

        stop = _make_stream_env("viewer1", PbHubStream.STOP_STREAM,
                                stream_id="stream-1")
        self.sm.handle_hub_stream("viewer1", stop)
        statuses = self.h.statuses.get("viewer1", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("broadcaster", statuses[0][2].lower())
        self.assertIsNotNone(self.sm.get_stream("stream-1"))

    def test_stream_session_properties(self):
        s = StreamSession(stream_id="x", broadcaster="alice")
        self.assertEqual(s.viewer_count, 0)
        s.viewers.add("bob")
        self.assertEqual(s.viewer_count, 1)

    def test_get_active_empty(self):
        self.assertEqual(self.sm.get_active_streams(), [])

    def test_stats_summary(self):
        summary = self.sm.get_stats_summary()
        self.assertIn("Active streams:", summary)


# ===========================================================================
# Stream permissions
# ===========================================================================

class TestStreamPermissions(unittest.TestCase):
    """Test stream broadcast and view class checks, concurrent limits."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"streamer", "viewer", "low_class"}
        self.h.user_classes = {"streamer": 5, "viewer": 1, "low_class": 0}

    def test_disabled_streams(self):
        sm = StreamManager(config=StreamConfig(enabled=False))
        env = _make_stream_env("streamer", PbHubStream.START_STREAM, title="test")
        sm.handle_hub_stream("streamer", env)
        # Should not crash, just return

    def test_broadcast_class_too_low(self):
        sm = _make_stream_manager(self.h, min_class_broadcast=5)
        env = _make_stream_env("viewer", PbHubStream.START_STREAM,
                               stream_id="s1", title="test")
        sm.handle_hub_stream("viewer", env)
        statuses = self.h.statuses.get("viewer", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("Insufficient class", statuses[0][2])

    def test_view_class_too_low(self):
        sm = _make_stream_manager(self.h, min_class_view=1)
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s1", title="test")
        sm.handle_hub_stream("streamer", start)
        self.h.clear()

        join = _make_stream_env("low_class", PbHubStream.JOIN_STREAM,
                                stream_id="s1")
        sm.handle_hub_stream("low_class", join)
        statuses = self.h.statuses.get("low_class", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("Insufficient class", statuses[0][2])

    def test_concurrent_stream_limit(self):
        sm = _make_stream_manager(self.h, max_concurrent=1)
        start1 = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                  stream_id="s1", title="First")
        sm.handle_hub_stream("streamer", start1)
        self.h.clear()

        self.h.pb_users.add("streamer2")
        self.h.user_classes["streamer2"] = 5
        start2 = _make_stream_env("streamer2", PbHubStream.START_STREAM,
                                  stream_id="s2", title="Second")
        sm.handle_hub_stream("streamer2", start2)
        statuses = self.h.statuses.get("streamer2", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("limit", statuses[0][2].lower())

    def test_viewer_capacity_full(self):
        sm = _make_stream_manager(self.h, max_viewers=1)
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s1", title="test")
        sm.handle_hub_stream("streamer", start)

        join1 = _make_stream_env("viewer", PbHubStream.JOIN_STREAM,
                                 stream_id="s1")
        sm.handle_hub_stream("viewer", join1)
        self.assertEqual(sm.get_stream("s1").viewer_count, 1)
        self.h.clear()

        self.h.pb_users.add("viewer2")
        self.h.user_classes["viewer2"] = 1
        join2 = _make_stream_env("viewer2", PbHubStream.JOIN_STREAM,
                                 stream_id="s1")
        sm.handle_hub_stream("viewer2", join2)
        statuses = self.h.statuses.get("viewer2", [])
        self.assertIn("full", statuses[0][2].lower())

    def test_already_watching(self):
        sm = _make_stream_manager(self.h)
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s1", title="test")
        sm.handle_hub_stream("streamer", start)
        join = _make_stream_env("viewer", PbHubStream.JOIN_STREAM,
                                stream_id="s1")
        sm.handle_hub_stream("viewer", join)
        self.h.clear()

        sm.handle_hub_stream("viewer", join)
        statuses = self.h.statuses.get("viewer", [])
        self.assertIn("Already watching", statuses[0][2])

    def test_already_broadcasting(self):
        sm = _make_stream_manager(self.h)
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s1", title="test")
        sm.handle_hub_stream("streamer", start)
        self.h.clear()

        start2 = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                  stream_id="s2", title="second")
        sm.handle_hub_stream("streamer", start2)
        statuses = self.h.statuses.get("streamer", [])
        self.assertIn("already broadcasting", statuses[0][2].lower())

    def test_join_nonexistent_stream(self):
        sm = _make_stream_manager(self.h)
        join = _make_stream_env("viewer", PbHubStream.JOIN_STREAM,
                                stream_id="no-such-stream")
        sm.handle_hub_stream("viewer", join)
        statuses = self.h.statuses.get("viewer", [])
        self.assertIn("not found", statuses[0][2])

    def test_stop_nonexistent_stream(self):
        sm = _make_stream_manager(self.h)
        stop = _make_stream_env("streamer", PbHubStream.STOP_STREAM,
                                stream_id="no-such")
        sm.handle_hub_stream("streamer", stop)
        statuses = self.h.statuses.get("streamer", [])
        self.assertIn("not found", statuses[0][2])


# ===========================================================================
# Stream disconnect
# ===========================================================================

class TestStreamDisconnect(unittest.TestCase):
    """Test stream cleanup on user disconnect."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"streamer", "viewer1", "viewer2"}
        self.h.user_classes = {"streamer": 5, "viewer1": 1, "viewer2": 1}
        self.sm = _make_stream_manager(self.h)

    def test_broadcaster_disconnect_ends_stream(self):
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s1", title="test")
        self.sm.handle_hub_stream("streamer", start)
        join = _make_stream_env("viewer1", PbHubStream.JOIN_STREAM,
                                stream_id="s1")
        self.sm.handle_hub_stream("viewer1", join)
        self.h.clear()

        self.sm.handle_user_disconnect("streamer")
        self.assertIsNone(self.sm.get_stream("s1"))

        # All PB users should receive STREAM_ENDED
        envs = self.h.decode_all("viewer1")
        ended = [e for e in envs if e.HasField("hub_stream")
                 and e.hub_stream.action == PbHubStream.STREAM_ENDED]
        self.assertTrue(len(ended) >= 1)

    def test_viewer_disconnect_removes_from_viewers(self):
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s1", title="test")
        self.sm.handle_hub_stream("streamer", start)
        join = _make_stream_env("viewer1", PbHubStream.JOIN_STREAM,
                                stream_id="s1")
        self.sm.handle_hub_stream("viewer1", join)
        self.assertEqual(self.sm.get_stream("s1").viewer_count, 1)

        self.sm.handle_user_disconnect("viewer1")
        self.assertEqual(self.sm.get_stream("s1").viewer_count, 0)

    def test_disconnect_user_not_in_any_stream(self):
        self.sm.handle_user_disconnect("nobody")
        # Should not crash


# ===========================================================================
# Stream update
# ===========================================================================

class TestStreamUpdate(unittest.TestCase):
    """Test broadcaster metadata updates."""

    def setUp(self):
        self.h = VVTestHelper()
        self.h.pb_users = {"streamer", "viewer1"}
        self.h.user_classes = {"streamer": 5, "viewer1": 1}
        self.sm = _make_stream_manager(self.h)
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s1", title="Original")
        self.sm.handle_hub_stream("streamer", start)
        self.h.clear()

    def test_update_title(self):
        upd = _make_stream_env("streamer", PbHubStream.STREAM_UPDATE,
                               stream_id="s1", title="New Title")
        self.sm.handle_hub_stream("streamer", upd)
        session = self.sm.get_stream("s1")
        self.assertEqual(session.title, "New Title")
        self.assertEqual(self.sm.stats["stream_updates"], 1)

    def test_update_by_non_broadcaster(self):
        upd = _make_stream_env("viewer1", PbHubStream.STREAM_UPDATE,
                               stream_id="s1", title="Hacked")
        self.sm.handle_hub_stream("viewer1", upd)
        session = self.sm.get_stream("s1")
        self.assertEqual(session.title, "Original")  # unchanged
        statuses = self.h.statuses.get("viewer1", [])
        self.assertIn("broadcaster", statuses[0][2].lower())

    def test_update_nonexistent_stream(self):
        upd = _make_stream_env("streamer", PbHubStream.STREAM_UPDATE,
                               stream_id="no-such")
        self.sm.handle_hub_stream("streamer", upd)
        statuses = self.h.statuses.get("streamer", [])
        self.assertIn("not found", statuses[0][2])

    def test_update_bitrate_capped(self):
        sm = _make_stream_manager(self.h, max_bitrate=100_000)
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s2", title="test")
        sm.handle_hub_stream("streamer", start)
        self.h.clear()

        upd = _make_stream_env("streamer", PbHubStream.STREAM_UPDATE,
                               stream_id="s2", bitrate=500_000)
        sm.handle_hub_stream("streamer", upd)
        session = sm.get_stream("s2")
        self.assertEqual(session.bitrate, 100_000)  # capped

    def test_unexpected_action_from_client(self):
        """STREAM_AVAILABLE sent from client should be rejected."""
        sm = _make_stream_manager(self.h)
        env = _make_stream_env("viewer1", PbHubStream.STREAM_AVAILABLE,
                               stream_id="s1")
        sm.handle_hub_stream("viewer1", env)
        statuses = self.h.statuses.get("viewer1", [])
        self.assertEqual(len(statuses), 1)
        self.assertIn("Unexpected", statuses[0][2])


# ===========================================================================
# Hub routing integration — calls
# ===========================================================================

class TestHubRoutingCalls(unittest.TestCase):
    """Test _route_call_signaling in hub_plugin."""

    def _import_route(self):
        from verlihub.client.nmdcpb.hub_plugin import _route_call_signaling
        return _route_call_signaling

    @patch(f"{HP}.ENABLE_VOICEVIDEO", False)
    @patch(f"{HP}._send_status")
    def test_disabled_returns_error(self, mock_status):
        route = self._import_route()
        env = _make_offer_env("alice", "bob")
        route("alice", env, "call_offer")
        mock_status.assert_called()
        args = mock_status.call_args[0]
        self.assertEqual(args[0], "alice")
        self.assertIn("not enabled", args[3])

    @patch(f"{HP}.ENABLE_VOICEVIDEO", True)
    @patch(f"{HP}._get_call_manager")
    @patch(f"{HP}._stats", {"calls_offered": 0, "calls_answered": 0, "calls_ended": 0})
    def test_call_offer_routed(self, mock_get_cm):
        mock_cm = MagicMock()
        mock_cm.handle_call_offer.return_value = True
        mock_get_cm.return_value = mock_cm

        route = self._import_route()
        env = _make_offer_env("alice", "bob")
        route("alice", env, "call_offer")
        mock_cm.handle_call_offer.assert_called_once_with("alice", env)

    @patch(f"{HP}.ENABLE_VOICEVIDEO", True)
    @patch(f"{HP}._get_call_manager")
    @patch(f"{HP}._stats", {"calls_offered": 0, "calls_answered": 0, "calls_ended": 0})
    def test_call_answer_routed(self, mock_get_cm):
        mock_cm = MagicMock()
        mock_cm.handle_call_answer.return_value = True
        mock_get_cm.return_value = mock_cm

        route = self._import_route()
        env = _make_answer_env("bob", "alice", "call-1", accepted=True)
        route("bob", env, "call_answer")
        mock_cm.handle_call_answer.assert_called_once()

    @patch(f"{HP}.ENABLE_VOICEVIDEO", True)
    @patch(f"{HP}._get_call_manager")
    @patch(f"{HP}._stats", {"calls_offered": 0, "calls_answered": 0, "calls_ended": 0})
    def test_call_end_routed(self, mock_get_cm):
        mock_cm = MagicMock()
        mock_cm.handle_call_end.return_value = True
        mock_get_cm.return_value = mock_cm

        route = self._import_route()
        env = _make_end_env("alice", "call-1")
        route("alice", env, "call_end")
        mock_cm.handle_call_end.assert_called_once()

    @patch(f"{HP}.ENABLE_VOICEVIDEO", True)
    @patch(f"{HP}._get_call_manager")
    @patch(f"{HP}._stats", {"calls_offered": 0, "calls_answered": 0, "calls_ended": 0})
    def test_call_candidate_routed(self, mock_get_cm):
        mock_cm = MagicMock()
        mock_cm.handle_call_candidate.return_value = True
        mock_get_cm.return_value = mock_cm

        route = self._import_route()
        env = _make_candidate_env("alice", "call-1")
        route("alice", env, "call_candidate")
        mock_cm.handle_call_candidate.assert_called_once()

    @patch(f"{HP}.ENABLE_VOICEVIDEO", True)
    @patch(f"{HP}._get_call_manager")
    @patch(f"{HP}._stats", {"calls_offered": 0, "calls_answered": 0, "calls_ended": 0})
    def test_call_media_control_routed(self, mock_get_cm):
        mock_cm = MagicMock()
        mock_cm.handle_call_media_control.return_value = True
        mock_get_cm.return_value = mock_cm

        route = self._import_route()
        env = _make_media_control_env("alice", "call-1", audio_muted=True)
        route("alice", env, "call_media_control")
        mock_cm.handle_call_media_control.assert_called_once()


# ===========================================================================
# Hub routing integration — streams
# ===========================================================================

class TestHubRoutingStreams(unittest.TestCase):
    """Test _route_hub_stream in hub_plugin."""

    def _import_route(self):
        from verlihub.client.nmdcpb.hub_plugin import _route_hub_stream
        return _route_hub_stream

    @patch(f"{HP}.ENABLE_HUB_STREAMS", False)
    @patch(f"{HP}._send_status")
    def test_disabled_returns_error(self, mock_status):
        route = self._import_route()
        env = _make_stream_env("streamer", PbHubStream.START_STREAM, title="test")
        route("streamer", env)
        mock_status.assert_called()
        args = mock_status.call_args[0]
        self.assertIn("not enabled", args[3])

    @patch(f"{HP}.ENABLE_HUB_STREAMS", True)
    @patch(f"{HP}._get_stream_manager")
    @patch(f"{HP}._stats", {"streams_started": 0, "streams_stopped": 0, "stream_joins": 0})
    def test_start_stream_routed(self, mock_get_sm):
        mock_sm = MagicMock()
        mock_sm.handle_hub_stream.return_value = True
        mock_get_sm.return_value = mock_sm

        route = self._import_route()
        env = _make_stream_env("streamer", PbHubStream.START_STREAM,
                               stream_id="s1", title="test")
        route("streamer", env)
        mock_sm.handle_hub_stream.assert_called_once()

    @patch(f"{HP}.ENABLE_HUB_STREAMS", True)
    @patch(f"{HP}._get_stream_manager")
    @patch(f"{HP}._stats", {"streams_started": 0, "streams_stopped": 0, "stream_joins": 0})
    def test_join_stream_routed(self, mock_get_sm):
        mock_sm = MagicMock()
        mock_get_sm.return_value = mock_sm

        route = self._import_route()
        env = _make_stream_env("viewer", PbHubStream.JOIN_STREAM,
                               stream_id="s1")
        route("viewer", env)
        mock_sm.handle_hub_stream.assert_called_once()


# ===========================================================================
# Hub admin commands
# ===========================================================================

class TestHubAdminCommands(unittest.TestCase):
    """Test +nmdcpb calls and +nmdcpb streams admin output."""

    @patch(f"{HP}.ENABLE_VOICEVIDEO", True)
    @patch(f"{HP}._get_call_manager")
    def test_calls_command_no_active(self, mock_get_cm):
        mock_cm = MagicMock()
        mock_cm.get_stats_summary.return_value = "Active calls: 0\n  Offered: 0"
        mock_cm.get_active_calls.return_value = []
        mock_get_cm.return_value = mock_cm

        from verlihub.client.nmdcpb.hub_plugin import OnHubCommand
        with patch(f"{HP}.vh", None):
            result = OnHubCommand("admin", "nmdcpb calls", 10, 0, "+")
        self.assertEqual(result, 0)

    @patch(f"{HP}.ENABLE_VOICEVIDEO", False)
    @patch(f"{HP}._get_call_manager", return_value=None)
    def test_calls_disabled(self, _):
        from verlihub.client.nmdcpb.hub_plugin import OnHubCommand
        with patch(f"{HP}.vh", None):
            result = OnHubCommand("admin", "nmdcpb calls", 10, 0, "+")
        self.assertEqual(result, 0)

    @patch(f"{HP}.ENABLE_HUB_STREAMS", True)
    @patch(f"{HP}._get_stream_manager")
    def test_streams_command(self, mock_get_sm):
        mock_sm = MagicMock()
        mock_sm.get_stats_summary.return_value = "Active streams: 0"
        mock_sm.get_active_streams.return_value = []
        mock_get_sm.return_value = mock_sm

        from verlihub.client.nmdcpb.hub_plugin import OnHubCommand
        with patch(f"{HP}.vh", None):
            result = OnHubCommand("admin", "nmdcpb streams", 10, 0, "+")
        self.assertEqual(result, 0)


# ===========================================================================
# Hub timer pruning
# ===========================================================================

class TestHubTimerPruning(unittest.TestCase):
    """Test that OnTimer prunes expired calls."""

    @patch(f"{HP}.ENABLE_VOICEVIDEO", True)
    @patch(f"{HP}._call_manager")
    def test_timer_prunes_calls(self, mock_cm_attr):
        mock_cm = MagicMock()
        mock_cm.prune_expired.return_value = 2

        from verlihub.client.nmdcpb import hub_plugin
        original_cm = hub_plugin._call_manager
        hub_plugin._call_manager = mock_cm
        try:
            with patch.object(hub_plugin, 'ENABLE_VOICEVIDEO', True), \
                 patch.object(hub_plugin, 'ENABLE_HUBRELAY', False), \
                 patch.object(hub_plugin, 'ENABLE_MEDIASHARE', False), \
                 patch.object(hub_plugin, 'ENABLE_CHANNELS', False), \
                 patch.object(hub_plugin, '_rate_pb', {}), \
                 patch.object(hub_plugin, '_rate_e2epm', {}):
                hub_plugin.OnTimer(1000)
        finally:
            hub_plugin._call_manager = original_cm


# ===========================================================================
# Hub OnUnLoad
# ===========================================================================

class TestHubOnUnLoad(unittest.TestCase):
    """Test cleanup on unload clears managers."""

    def test_unload_clears_managers(self):
        from verlihub.client.nmdcpb import hub_plugin
        # Set up managers
        hub_plugin._call_manager = MagicMock()
        hub_plugin._stream_manager = MagicMock()
        hub_plugin._channel_manager = MagicMock()

        hub_plugin.OnUnLoad(0)

        self.assertIsNone(hub_plugin._call_manager)
        self.assertIsNone(hub_plugin._stream_manager)
        self.assertIsNone(hub_plugin._channel_manager)


# ===========================================================================
# Noop / default behavior
# ===========================================================================

class TestNoopDefaults(unittest.TestCase):
    """Test CallManager / StreamManager with default noop callbacks."""

    def test_call_manager_noop(self):
        cm = CallManager(config=CallConfig(enabled=True))
        # Methods should work without crashing
        self.assertTrue(CallManager._noop_send("data", "nick"))
        CallManager._noop_status("nick", 0, 0, "msg")

    def test_stream_manager_noop(self):
        sm = StreamManager(config=StreamConfig(enabled=True))
        self.assertTrue(StreamManager._noop_send("data", "nick"))
        StreamManager._noop_status("nick", 0, 0, "msg")

    def test_call_manager_default_user_class(self):
        cm = CallManager(config=CallConfig(enabled=True))
        # Default get_user_class returns 1
        self.assertEqual(cm._get_user_class("anyone"), 1)

    def test_stream_manager_default_user_class(self):
        sm = StreamManager(config=StreamConfig(enabled=True))
        self.assertEqual(sm._get_user_class("anyone"), 1)

    def test_call_auto_assigns_id(self):
        """If offer has no call_id, one is generated."""
        h = VVTestHelper()
        h.pb_users = {"alice", "bob"}
        h.user_classes = {"alice": 1, "bob": 1}
        cm = _make_call_manager(h)

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT,
            from_nick="alice",
            to_nick="bob",
        )
        env.call_offer.target_nick = "bob"
        env.call_offer.call_id = ""  # no ID
        env.call_offer.media.append(PbCallOffer.AUDIO)
        env.timestamp = int(time.time() * 1000)

        cm.handle_call_offer("alice", env)
        calls = cm.get_active_calls()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].call_id)  # auto-generated

    def test_stream_auto_assigns_id(self):
        """If stream has no stream_id, one is generated."""
        h = VVTestHelper()
        h.pb_users = {"streamer"}
        h.user_classes = {"streamer": 5}
        sm = _make_stream_manager(h)

        env = _make_stream_env("streamer", PbHubStream.START_STREAM,
                               title="auto-id test")
        # stream_id is empty string
        sm.handle_hub_stream("streamer", env)
        streams = sm.get_active_streams()
        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].stream_id)

    def test_leave_nonexistent_stream_tolerated(self):
        sm = StreamManager(config=StreamConfig(enabled=True))
        env = _make_stream_env("viewer", PbHubStream.LEAVE_STREAM,
                               stream_id="no-such")
        result = sm.handle_hub_stream("viewer", env)
        self.assertTrue(result)

    def test_leave_stream_not_a_viewer_tolerated(self):
        h = VVTestHelper()
        h.pb_users = {"streamer", "viewer"}
        h.user_classes = {"streamer": 5, "viewer": 1}
        sm = _make_stream_manager(h)
        start = _make_stream_env("streamer", PbHubStream.START_STREAM,
                                 stream_id="s1", title="test")
        sm.handle_hub_stream("streamer", start)

        leave = _make_stream_env("viewer", PbHubStream.LEAVE_STREAM,
                                 stream_id="s1")
        result = sm.handle_hub_stream("viewer", leave)
        self.assertTrue(result)


# ===========================================================================
# _route_direct integration — call payloads are intercepted
# ===========================================================================

class TestRouteDirectIntercept(unittest.TestCase):
    """Test that _route_direct intercepts call signaling payloads."""

    @patch(f"{HP}._route_call_signaling")
    @patch(f"{HP}._send_status")
    def test_call_offer_intercepted(self, mock_status, mock_route_call):
        from verlihub.client.nmdcpb.hub_plugin import _route_direct
        env = _make_offer_env("alice", "bob", "call-1")
        _route_direct("alice", env)
        mock_route_call.assert_called_once_with("alice", env, "call_offer")
        mock_status.assert_not_called()

    @patch(f"{HP}._route_call_signaling")
    @patch(f"{HP}._send_status")
    def test_call_end_intercepted(self, mock_status, mock_route_call):
        from verlihub.client.nmdcpb.hub_plugin import _route_direct
        env = _make_end_env("alice", "call-1")
        env.to_nick = "bob"
        _route_direct("alice", env)
        mock_route_call.assert_called_once_with("alice", env, "call_end")

    @patch(f"{HP}._route_call_signaling")
    def test_call_media_control_intercepted(self, mock_route_call):
        from verlihub.client.nmdcpb.hub_plugin import _route_direct
        env = _make_media_control_env("alice", "call-1")
        env.to_nick = "bob"
        _route_direct("alice", env)
        mock_route_call.assert_called_once_with("alice", env, "call_media_control")


# ===========================================================================
# _route_hub integration — hub_stream intercepted
# ===========================================================================

class TestRouteHubIntercept(unittest.TestCase):
    """Test that _route_hub intercepts hub_stream payloads."""

    @patch(f"{HP}._route_hub_stream")
    def test_hub_stream_intercepted(self, mock_route_stream):
        from verlihub.client.nmdcpb.hub_plugin import _route_hub
        env = _make_stream_env("streamer", PbHubStream.START_STREAM,
                               stream_id="s1", title="test")
        _route_hub("streamer", env)
        mock_route_stream.assert_called_once_with("streamer", env)


# ===========================================================================
# Bitrate capping
# ===========================================================================

class TestBitrateCapping(unittest.TestCase):
    """Test that stream bitrate is capped at config max."""

    def test_stream_bitrate_capped_on_start(self):
        h = VVTestHelper()
        h.pb_users = {"streamer"}
        h.user_classes = {"streamer": 5}
        sm = _make_stream_manager(h, max_bitrate=100_000)

        env = _make_stream_env("streamer", PbHubStream.START_STREAM,
                               stream_id="s1", title="test", bitrate=500_000)
        sm.handle_hub_stream("streamer", env)
        session = sm.get_stream("s1")
        self.assertEqual(session.bitrate, 100_000)

    def test_stream_bitrate_within_limit(self):
        h = VVTestHelper()
        h.pb_users = {"streamer"}
        h.user_classes = {"streamer": 5}
        sm = _make_stream_manager(h, max_bitrate=500_000)

        env = _make_stream_env("streamer", PbHubStream.START_STREAM,
                               stream_id="s1", title="test", bitrate=128_000)
        sm.handle_hub_stream("streamer", env)
        session = sm.get_stream("s1")
        self.assertEqual(session.bitrate, 128_000)


if __name__ == "__main__":
    unittest.main()
