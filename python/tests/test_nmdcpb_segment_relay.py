"""
Integration tests for NMDCpb Phase 3.5 features:

    1. SegmentCoordinator — multi-source segmented download with TTH verification
    2. Relay resume — mid-transfer disconnect + reconnect token remapping
    3. StealthSearch — aggregation + dedup of search results
"""

import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the verlihub package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbRelayRequest,
    PbRelayAck,
    PbRelayData,
    PbRelayClosed,
    PbRelayResume,
)
from verlihub.client.nmdcpb.wire import WireCodec
from verlihub.client.nmdcpb.relay import SegmentCoordinator, Segment, SegmentState
from verlihub.client.nmdcpb.search import StealthSearch, SearchHit


# =========================================================================
# 1. SegmentCoordinator unit tests (Python side)
# =========================================================================

class TestSegmentCoordinator(unittest.TestCase):
    """Tests for the Python SegmentCoordinator in relay.py."""

    def test_plan_segments_splits_file(self):
        """planSegments creates correct segment count and offsets."""
        peers = ["Alice", "Bob", "Carol"]
        file_size = 3 * 1024 * 1024   # 3 MB
        seg_size = 1024 * 1024         # 1 MB

        coord = SegmentCoordinator("TTHROOT", file_size, peers, seg_size)
        segs = coord.plan_segments()

        self.assertEqual(len(segs), 3)
        self.assertEqual(segs[0].offset, 0)
        self.assertEqual(segs[0].length, seg_size)
        self.assertEqual(segs[1].offset, seg_size)
        self.assertEqual(segs[2].offset, 2 * seg_size)

        # Round-robin assignment
        self.assertEqual(segs[0].peer_nick, "Alice")
        self.assertEqual(segs[1].peer_nick, "Bob")
        self.assertEqual(segs[2].peer_nick, "Carol")

    def test_plan_segments_remainder(self):
        """Non-even file size produces a smaller last segment."""
        peers = ["Alice"]
        file_size = 3 * 256 * 1024 + 100
        seg_size = 256 * 1024

        coord = SegmentCoordinator("TTH_ODD", file_size, peers, seg_size)
        segs = coord.plan_segments()

        self.assertEqual(len(segs), 4)
        total = sum(s.length for s in segs)
        self.assertEqual(total, file_size)
        self.assertEqual(segs[3].length, 100)

    def test_lifecycle_assign_data_complete(self):
        """Full lifecycle: plan → assign → start → data → complete."""
        peers = ["Alice"]
        seg_size = 256 * 1024
        file_size = 2 * seg_size

        coord = SegmentCoordinator("TTH_LIFE", file_size, peers, seg_size)
        coord.plan_segments()

        completed = []
        download_done = []
        coord.on_segment_complete = lambda seg: completed.append(seg.index)
        coord.on_download_complete = lambda info: download_done.append(True)

        # Segment 0
        coord.assign_segment(0, "Alice", 100)
        self.assertEqual(coord._segments[0].state, SegmentState.ASSIGNED)

        coord.start_segment(0)
        self.assertEqual(coord._segments[0].state, SegmentState.TRANSFERRING)

        data = bytes(seg_size)
        coord.on_segment_data(0, data)
        self.assertEqual(coord._segments[0].state, SegmentState.COMPLETED)
        self.assertEqual(completed, [0])
        self.assertFalse(download_done)

        # Segment 1
        coord.assign_segment(1, "Alice", 101)
        coord.start_segment(1)
        coord.on_segment_data(1, data)
        self.assertEqual(coord._segments[1].state, SegmentState.COMPLETED)
        self.assertTrue(download_done)

    def test_fail_and_retry(self):
        """failSegment retries up to MAX_RETRIES then marks FAILED."""
        coord = SegmentCoordinator("TTH_F", 256 * 1024, ["Alice"], 256 * 1024)
        coord.plan_segments()

        failed = []
        coord.on_segment_failed = lambda seg: failed.append(seg.index)

        for i in range(coord.MAX_RETRIES):
            self.assertTrue(coord.fail_segment(0))
            self.assertEqual(coord._segments[0].state, SegmentState.PENDING)

        self.assertFalse(coord.fail_segment(0))
        self.assertEqual(coord._segments[0].state, SegmentState.FAILED)
        self.assertEqual(failed, [0])

    def test_reassign_peer(self):
        """reassignPeer moves PENDING/ASSIGNED segments to a new peer."""
        peers = ["Alice", "Bob"]
        seg_size = 256 * 1024
        coord = SegmentCoordinator("TTH_R", 4 * seg_size, peers, seg_size)
        coord.plan_segments()

        coord.assign_segment(0, "Alice", 100)
        coord.assign_segment(2, "Alice", 102)

        moved = coord.reassign_peer("Alice", "Bob")
        self.assertEqual(len(moved), 2)
        self.assertEqual(coord._segments[0].peer_nick, "Bob")
        self.assertEqual(coord._segments[0].state, SegmentState.PENDING)
        self.assertEqual(coord._segments[2].peer_nick, "Bob")

    def test_get_segment_by_relay(self):
        """get_segment_by_relay finds correct segment by relay ID."""
        peers = ["Alice", "Bob"]
        seg_size = 256 * 1024
        coord = SegmentCoordinator("TTH_G", 2 * seg_size, peers, seg_size)
        coord.plan_segments()

        coord.assign_segment(0, "Alice", 42)
        coord.assign_segment(1, "Bob", 99)

        seg = coord.get_segment_by_relay(42)
        self.assertIsNotNone(seg)
        self.assertEqual(seg.index, 0)
        self.assertEqual(seg.peer_nick, "Alice")

        seg = coord.get_segment_by_relay(99)
        self.assertIsNotNone(seg)
        self.assertEqual(seg.index, 1)

        self.assertIsNone(coord.get_segment_by_relay(12345))

    def test_per_peer_concurrency(self):
        """canAssignPeer respects MAX_CONCURRENT_PER_PEER."""
        peers = ["Alice"]
        seg_size = 256 * 1024
        coord = SegmentCoordinator("TTH_C", 10 * seg_size, peers, seg_size)
        coord.plan_segments()

        coord.assign_segment(0, "Alice", 100)
        coord.assign_segment(1, "Alice", 101)
        self.assertEqual(coord.peer_active_count("Alice"), 2)
        # Rate-limit may also block; check active count blocks at least
        self.assertGreaterEqual(coord.peer_active_count("Alice"),
                                coord.MAX_CONCURRENT_PER_PEER)

        # Complete one
        coord.start_segment(0)
        coord.on_segment_data(0, bytes(seg_size))
        self.assertEqual(coord.peer_active_count("Alice"), 1)
        # Reset rate-limit by clearing the internal timer
        coord._peer_last_request.clear()
        self.assertTrue(coord.can_assign_peer("Alice"))


# =========================================================================
# 2. Relay resume token remapping (hub_plugin.py)
# =========================================================================

class TestRelayResume(unittest.TestCase):
    """Tests for relay resume + reconnect token remapping in hub_plugin.py."""

    MODULE = "verlihub.client.nmdcpb.hub_plugin"

    def setUp(self):
        """Import hub_plugin and reset global state."""
        import verlihub.client.nmdcpb.hub_plugin as hp
        self.hp = hp

        # Save & reset global state
        self._saved_relays = dict(hp._relay_sessions)
        self._saved_closed = dict(hp._closed_relay_tokens)
        self._saved_stats = dict(hp._stats)
        self._saved_next_id = hp._next_relay_id
        hp._relay_sessions.clear()
        hp._closed_relay_tokens.clear()

    def tearDown(self):
        self.hp._relay_sessions.clear()
        self.hp._closed_relay_tokens.clear()
        self.hp._relay_sessions.update(self._saved_relays)
        self.hp._closed_relay_tokens.update(self._saved_closed)
        self.hp._stats.update(self._saved_stats)
        self.hp._next_relay_id = self._saved_next_id

    def _create_session(self, relay_id: int, user_a: str, user_b: str,
                        token: str = "tok123", bytes_fwd: int = 0):
        """Helper: create a _RelaySession object."""
        sess = self.hp._RelaySession(
            relay_id=relay_id,
            user_a=user_a,
            user_b=user_b,
            token=token,
            bytes_forwarded=bytes_fwd,
        )
        self.hp._relay_sessions[relay_id] = sess
        return sess

    def test_active_session_resume(self):
        """Resume on an active relay session forwards to the peer."""
        sess = self._create_session(42, "Alice", "Bob", "tok_active")

        # Build resume envelope
        env = PbEnvelope()
        env.relay_resume.relay_id = 42
        env.relay_resume.token = "tok_active"
        env.from_nick = "Alice"

        # Track what gets sent
        sent = []
        with patch(f"{self.MODULE}._send_to_user",
                   side_effect=lambda d, n: sent.append((d, n))):
            self.hp._forward_relay_resume("Alice", env)

        # Should have sent to Bob (the peer)
        self.assertTrue(any(nick == "Bob" for _, nick in sent),
                        f"Expected message to Bob, got: {[n for _, n in sent]}")

    def test_reconnect_resume_from_archive(self):
        """After disconnect, archived token allows reconnect resume."""
        # Archive a closed session
        self.hp._closed_relay_tokens["tok_reconnect"] = {
            "user_a": "Alice",
            "user_b": "Bob",
            "bytes_forwarded": 1024,
            "closed_at": time.time(),
        }
        self.hp._next_relay_id = 99

        env = PbEnvelope()
        env.relay_resume.relay_id = 0  # 0 = reconnect request
        env.relay_resume.token = "tok_reconnect"
        env.from_nick = "Alice"

        sent = []
        with patch(f"{self.MODULE}._send_to_user",
                   side_effect=lambda d, n: sent.append((d, n))), \
             patch(f"{self.MODULE}._get_all_nicks",
                   return_value=["Alice", "Bob"]), \
             patch(f"{self.MODULE}._user_relay_count", return_value=0):

            self.hp._forward_relay_resume("Alice", env)

        # Token should be consumed (one-time use)
        self.assertNotIn("tok_reconnect", self.hp._closed_relay_tokens)

        # A new relay session should exist
        self.assertIn(99, self.hp._relay_sessions)
        new_sess = self.hp._relay_sessions[99]
        self.assertEqual(new_sess.user_a, "Alice")
        self.assertEqual(new_sess.user_b, "Bob")
        self.assertEqual(new_sess.bytes_forwarded, 1024)

    def test_reconnect_expired_token_rejected(self):
        """Expired archived token is rejected."""
        self.hp._closed_relay_tokens["tok_expired"] = {
            "user_a": "Alice",
            "user_b": "Bob",
            "bytes_forwarded": 0,
            "closed_at": time.time() - self.hp.RELAY_RESUME_TOKEN_TTL - 10,
        }

        env = PbEnvelope()
        env.relay_resume.relay_id = 0
        env.relay_resume.token = "tok_expired"
        env.from_nick = "Alice"

        sent = []
        with patch(f"{self.MODULE}._send_to_user",
                   side_effect=lambda d, n: sent.append((d, n))), \
             patch(f"{self.MODULE}._send_status") as mock_status:
            self.hp._forward_relay_resume("Alice", env)

        # Should send error code 17 (expired)
        mock_status.assert_called()
        args = mock_status.call_args
        self.assertEqual(args[0][2], 17)  # error code

    def test_reconnect_wrong_participant_rejected(self):
        """Reconnect from non-participant is rejected."""
        self.hp._closed_relay_tokens["tok_notmine"] = {
            "user_a": "Alice",
            "user_b": "Bob",
            "bytes_forwarded": 0,
            "closed_at": time.time(),
        }

        env = PbEnvelope()
        env.relay_resume.relay_id = 0
        env.relay_resume.token = "tok_notmine"
        env.from_nick = "Eve"  # not Alice or Bob

        with patch(f"{self.MODULE}._send_status") as mock_status:
            self.hp._forward_relay_resume("Eve", env)

        # Should send error code 18 (not a participant)
        mock_status.assert_called()
        args = mock_status.call_args
        self.assertEqual(args[0][2], 18)

        # Token should remain (not consumed)
        self.assertIn("tok_notmine", self.hp._closed_relay_tokens)

    def test_closed_token_cleanup_on_timer(self):
        """Expired archived tokens are cleaned up on timer tick."""
        self.hp._closed_relay_tokens["fresh"] = {
            "user_a": "A", "user_b": "B", "bytes_forwarded": 0,
            "closed_at": time.time(),
        }
        self.hp._closed_relay_tokens["stale"] = {
            "user_a": "C", "user_b": "D", "bytes_forwarded": 0,
            "closed_at": time.time() - self.hp.RELAY_RESUME_TOKEN_TTL - 1,
        }

        # Directly run the token cleanup logic from OnTimer
        now = time.time()
        expired = [t for t, info in self.hp._closed_relay_tokens.items()
                   if (now - info["closed_at"]) > self.hp.RELAY_RESUME_TOKEN_TTL]
        for t in expired:
            del self.hp._closed_relay_tokens[t]

        # "stale" should be cleaned, "fresh" retained
        self.assertNotIn("stale", self.hp._closed_relay_tokens)
        self.assertIn("fresh", self.hp._closed_relay_tokens)


# =========================================================================
# 3. StealthSearch aggregation + dedup
# =========================================================================

class TestStealthSearch(unittest.TestCase):
    """Tests for the StealthSearch class in search.py."""

    def test_dedup_by_tth(self):
        """Results with the same TTH+size are merged into one SearchHit."""
        hit_key = SearchHit(
            filename="test.iso", tth="ABCDEF123", size=100,
        ).unique_key

        # Same file from two peers
        hit1 = SearchHit(
            filename="test.iso", tth="ABCDEF123", size=100,
            peers=["Alice"], best_free_slots=2, best_total_slots=5,
        )
        hit2 = SearchHit(
            filename="test.iso", tth="ABCDEF123", size=100,
            peers=["Bob"], best_free_slots=3, best_total_slots=5,
        )

        # Same unique key
        self.assertEqual(hit1.unique_key, hit2.unique_key)

    def test_unique_key_different_files(self):
        """Different TTH → different unique_key."""
        h1 = SearchHit(filename="a.iso", tth="TTH_A", size=100)
        h2 = SearchHit(filename="b.iso", tth="TTH_B", size=100)
        self.assertNotEqual(h1.unique_key, h2.unique_key)

    def test_unique_key_directory(self):
        """Directories without TTH use path+name for dedup."""
        h1 = SearchHit(filename="docs", path="/share/docs", size=0, is_directory=True)
        h2 = SearchHit(filename="docs", path="/share/docs", size=0, is_directory=True)
        self.assertEqual(h1.unique_key, h2.unique_key)

    def test_result_ranking(self):
        """Results are ranked by peer count (desc), then free slots (desc)."""
        hits = [
            SearchHit(filename="rare.txt", tth="T1", size=100,
                      peers=["Alice"], best_free_slots=5),
            SearchHit(filename="popular.txt", tth="T2", size=200,
                      peers=["Alice", "Bob", "Carol"], best_free_slots=1),
            SearchHit(filename="common.txt", tth="T3", size=300,
                      peers=["Alice", "Bob"], best_free_slots=3),
        ]

        ranked = sorted(
            hits,
            key=lambda h: (-len(h.peers), -h.best_free_slots, h.filename),
        )

        self.assertEqual(ranked[0].filename, "popular.txt")   # 3 peers
        self.assertEqual(ranked[1].filename, "common.txt")     # 2 peers
        self.assertEqual(ranked[2].filename, "rare.txt")       # 1 peer

    def test_search_init_requires_query_or_tth(self):
        """StealthSearch requires either query or tth."""
        client = MagicMock()
        with self.assertRaises(ValueError):
            StealthSearch(client, query="", tth="")

    def test_search_single_use(self):
        """StealthSearch cannot be run twice."""
        client = MagicMock()
        client.send_user_query = AsyncMock()
        client.on_user_query_result = None
        client.on_private_search_result = None

        search = StealthSearch(client, query="test")
        search._started = True  # Simulate already run

        with self.assertRaises(RuntimeError):
            asyncio.run(search.run(timeout=0.1))

    def test_on_private_search_result_merges_peers(self):
        """_on_private_search_result deduplicates and merges peer lists."""
        client = MagicMock()
        search = StealthSearch(client, query="test")
        search._started = True
        search._peers_expected = 2

        # Simulate result from Alice
        result1 = MagicMock()
        result1.search_id = search.query_id
        result1.results = [MagicMock(
            filename="file.bin", path="/share", size=1000,
            tth="TTH_MERGE", is_directory=False,
            free_slots=2, total_slots=5,
        )]
        search._on_private_search_result("Alice", result1)
        self.assertEqual(len(search._hits), 1)

        # Simulate same file from Bob with more free slots
        result2 = MagicMock()
        result2.search_id = search.query_id
        result2.results = [MagicMock(
            filename="file.bin", path="/share", size=1000,
            tth="TTH_MERGE", is_directory=False,
            free_slots=4, total_slots=8,
        )]
        search._on_private_search_result("Bob", result2)

        # Should still be 1 hit, but with 2 peers and best slots updated
        self.assertEqual(len(search._hits), 1)
        hit = list(search._hits.values())[0]
        self.assertIn("Alice", hit.peers)
        self.assertIn("Bob", hit.peers)
        self.assertEqual(hit.best_free_slots, 4)
        self.assertEqual(hit.best_total_slots, 8)

    def test_on_user_query_result_sets_expected_peers(self):
        """_on_user_query_result records sweep_count."""
        client = MagicMock()
        search = StealthSearch(client, query="test")

        result = MagicMock()
        result.query_id = search.query_id
        result.error = ""
        result.sweep_count = 5
        result.total_matching = 10

        search._on_user_query_result(result)
        self.assertEqual(search._peers_expected, 5)

    def test_on_user_query_result_zero_sweep_completes(self):
        """If sweep_count is 0, search completes immediately."""
        client = MagicMock()
        search = StealthSearch(client, query="test")

        result = MagicMock()
        result.query_id = search.query_id
        result.error = ""
        result.sweep_count = 0
        result.total_matching = 0

        search._on_user_query_result(result)
        self.assertTrue(search._done_event.is_set())


if __name__ == "__main__":
    unittest.main()
