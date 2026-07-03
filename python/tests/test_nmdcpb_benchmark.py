"""
NMDCpb Performance Benchmarks
==============================

Measures throughput and latency for key operations:
- Wire codec encode/decode
- Opaque relay forwarding (wire-level byte surgery)
- E2EPM encrypt/decrypt
- Protobuf serialize/parse
- Hub relay forwarding simulation

Run with:
    python -m pytest tests/test_nmdcpb_benchmark.py -v --noconftest -s
"""

import os
import sys
import time
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verlihub.client.nmdcpb.wire import (
    WireCodec, _b64url_encode, _b64url_decode,
    _read_varint, _encode_varint, _skip_field,
    _extract_field_raw, _read_submsg_varint, _submsg_data_length,
    _FIELD_RELAY_DATA, _FIELD_RD_RELAY_ID, _FIELD_RD_DATA,
)
from verlihub.client.nmdcpb.nmdcpb_pb2 import PbEnvelope, PbRelayData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_relay_data_envelope(data_size: int = 32768,
                              relay_id: int = 42) -> PbEnvelope:
    """Build a realistic relay data envelope."""
    env = PbEnvelope()
    env.route = PbEnvelope.DIRECT
    env.from_nick = "alice"
    env.to_nick = "bob"
    env.timestamp = int(time.time() * 1000)
    rd = env.relay_data
    rd.relay_id = relay_id
    rd.data = os.urandom(data_size)
    rd.offset = 0
    return env


def _benchmark(func, iterations: int = 1000, label: str = "") -> dict:
    """Run func *iterations* times and report stats."""
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        func()
        t1 = time.perf_counter_ns()
        times.append(t1 - t0)

    mean_us = statistics.mean(times) / 1000
    median_us = statistics.median(times) / 1000
    p99_us = sorted(times)[int(len(times) * 0.99)] / 1000
    throughput = iterations / (sum(times) / 1e9)

    result = {
        "label": label,
        "iterations": iterations,
        "mean_us": mean_us,
        "median_us": median_us,
        "p99_us": p99_us,
        "throughput_ops_sec": throughput,
    }
    if label:
        print(f"  {label}: mean={mean_us:.1f}µs  median={median_us:.1f}µs  "
              f"p99={p99_us:.1f}µs  throughput={throughput:.0f} ops/s")
    return result


# ---------------------------------------------------------------------------
# Wire codec benchmarks
# ---------------------------------------------------------------------------

class TestWireCodecBenchmarks:
    """Benchmark encode/decode throughput for various payload sizes."""

    def test_encode_text_small(self):
        """Encode a small chat envelope (~100 bytes) as $PB text."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST, from_nick="alice")
        env.chat.text = "Hello, world!"
        print()
        _benchmark(lambda: WireCodec.encode_text(env), 5000,
                   "encode_text(chat ~100B)")

    def test_encode_text_relay_32k(self):
        """Encode a 32KB relay data envelope as $PB text."""
        env = _make_relay_data_envelope(32768)
        print()
        _benchmark(lambda: WireCodec.encode_text(env), 2000,
                   "encode_text(relay 32KB)")

    def test_encode_text_relay_64k(self):
        """Encode a 64KB relay data envelope as $PB text."""
        env = _make_relay_data_envelope(65536)
        print()
        _benchmark(lambda: WireCodec.encode_text(env), 1000,
                   "encode_text(relay 64KB)")

    def test_decode_text_small(self):
        """Decode a small $PB text message."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST, from_nick="alice")
        env.chat.text = "Hello, world!"
        wire = WireCodec.encode_text(env)
        print()
        _benchmark(lambda: WireCodec.decode(wire), 5000,
                   "decode(chat ~100B)")

    def test_decode_text_relay_32k(self):
        """Decode a 32KB relay data $PB message."""
        env = _make_relay_data_envelope(32768)
        wire = WireCodec.encode_text(env)
        print()
        _benchmark(lambda: WireCodec.decode(wire), 2000,
                   "decode(relay 32KB)")

    def test_decode_text_relay_64k(self):
        """Decode a 64KB relay data $PB message."""
        env = _make_relay_data_envelope(65536)
        wire = WireCodec.encode_text(env)
        print()
        _benchmark(lambda: WireCodec.decode(wire), 1000,
                   "decode(relay 64KB)")


# ---------------------------------------------------------------------------
# Opaque relay fast-path benchmarks
# ---------------------------------------------------------------------------

class TestOpaqueRelayBenchmarks:
    """Compare full parse+re-serialize vs opaque byte surgery."""

    def _forward_classic(self, wire: str) -> str:
        """Classic path: decode → modify nicks → encode."""
        env = WireCodec.decode(wire)
        assert env is not None
        env.from_nick = "alice"
        env.to_nick = "charlie"
        return WireCodec.encode_text(env)

    def _forward_opaque(self, wire: str) -> str:
        """Opaque path: decode_relay_opaque → build_relay_forward."""
        fast = WireCodec.decode_relay_opaque(wire)
        assert fast is not None
        _, relay_id, data_length, raw_pb = fast
        result = WireCodec.build_relay_forward(
            raw_pb, from_nick="alice", to_nick="charlie",
            timestamp=int(time.time() * 1000))
        return result

    def test_classic_vs_opaque_32k(self):
        """Compare classic vs opaque forwarding for 32KB relay data."""
        env = _make_relay_data_envelope(32768)
        wire = WireCodec.encode_text(env)
        print()
        classic = _benchmark(lambda: self._forward_classic(wire), 1000,
                             "classic_forward(32KB)")
        opaque = _benchmark(lambda: self._forward_opaque(wire), 1000,
                            "opaque_forward(32KB)")
        speedup = classic["mean_us"] / opaque["mean_us"]
        print(f"  → Speedup: {speedup:.2f}x")
        # Note: with protobuf C extension, speedup may be ~1.0x because
        # SerializeToString/ParseFromString are highly optimized in C.
        # The wire scanning utilities are in pure Python (~137K ops/s).
        # True speedup comes from skipping full WireCodec.decode() when
        # the hub fast-path uses decode_relay_opaque → build_relay_forward
        # directly, avoiding PbEnvelope allocation + dispatch overhead.

    def test_classic_vs_opaque_64k(self):
        """Compare classic vs opaque forwarding for 64KB relay data."""
        env = _make_relay_data_envelope(65536)
        wire = WireCodec.encode_text(env)
        print()
        classic = _benchmark(lambda: self._forward_classic(wire), 500,
                             "classic_forward(64KB)")
        opaque = _benchmark(lambda: self._forward_opaque(wire), 500,
                            "opaque_forward(64KB)")
        speedup = classic["mean_us"] / opaque["mean_us"]
        print(f"  → Speedup: {speedup:.2f}x")

    def test_opaque_correctness(self):
        """Verify opaque forwarding produces identical relay data."""
        env = _make_relay_data_envelope(1024, relay_id=99)
        wire = WireCodec.encode_text(env)

        # Classic forward
        classic_wire = self._forward_classic(wire)
        classic_env = WireCodec.decode(classic_wire)

        # Opaque forward
        opaque_wire = self._forward_opaque(wire)
        opaque_env = WireCodec.decode(opaque_wire)

        assert opaque_env is not None
        assert classic_env is not None
        assert opaque_env.relay_data.relay_id == classic_env.relay_data.relay_id == 99
        assert opaque_env.relay_data.data == classic_env.relay_data.data
        assert opaque_env.from_nick == classic_env.from_nick == "alice"
        assert opaque_env.to_nick == classic_env.to_nick == "charlie"

    def test_opaque_small_payload(self):
        """Opaque forwarding with tiny (64 byte) relay payload."""
        env = _make_relay_data_envelope(64)
        wire = WireCodec.encode_text(env)
        print()
        _benchmark(lambda: self._forward_opaque(wire), 5000,
                   "opaque_forward(64B)")

    def test_decode_relay_opaque_throughput(self):
        """Throughput of the fast-path opaque decoder alone."""
        env = _make_relay_data_envelope(32768)
        wire = WireCodec.encode_text(env)
        print()
        _benchmark(lambda: WireCodec.decode_relay_opaque(wire), 2000,
                   "decode_relay_opaque(32KB)")


# ---------------------------------------------------------------------------
# Wire format scanning benchmarks
# ---------------------------------------------------------------------------

class TestWireFormatScanBenchmarks:
    """Benchmark the raw protobuf wire format scanning utilities."""

    def test_extract_field_raw(self):
        """Find relay_data field in a 32KB envelope."""
        env = _make_relay_data_envelope(32768)
        raw = env.SerializeToString()
        print()
        _benchmark(lambda: _extract_field_raw(raw, _FIELD_RELAY_DATA), 5000,
                   "extract_field_raw(relay_data in 32KB)")

    def test_read_relay_id(self):
        """Read relay_id from raw bytes without full parse."""
        env = _make_relay_data_envelope(32768, relay_id=12345)
        raw = env.SerializeToString()
        print()
        result = _benchmark(
            lambda: _read_submsg_varint(raw, _FIELD_RELAY_DATA, _FIELD_RD_RELAY_ID),
            5000, "read_relay_id(32KB)")
        # Verify correctness
        rid = _read_submsg_varint(raw, _FIELD_RELAY_DATA, _FIELD_RD_RELAY_ID)
        assert rid == 12345

    def test_data_length_no_copy(self):
        """Read relay_data.data length without copying the payload."""
        env = _make_relay_data_envelope(32768)
        raw = env.SerializeToString()
        print()
        dlen = _submsg_data_length(raw, _FIELD_RELAY_DATA, _FIELD_RD_DATA)
        assert dlen == 32768
        _benchmark(
            lambda: _submsg_data_length(raw, _FIELD_RELAY_DATA, _FIELD_RD_DATA),
            5000, "data_length_no_copy(32KB)")


# ---------------------------------------------------------------------------
# E2EPM benchmarks
# ---------------------------------------------------------------------------

class TestE2EPMBenchmarks:
    """Benchmark E2EPM key exchange and encrypt/decrypt."""

    def test_key_exchange(self):
        """Full key exchange between two E2EPMManagers."""
        from verlihub.client.nmdcpb.e2epm import E2EPMManager
        print()

        def _exchange():
            mgr_a = E2EPMManager("alice")
            mgr_b = E2EPMManager("bob")
            kex_a = mgr_a.initiate_session("bob")
            kex_b = mgr_b.handle_key_exchange("alice", kex_a)
            mgr_a.handle_key_exchange("bob", kex_b)
            return mgr_a, mgr_b

        _benchmark(_exchange, 500, "full_key_exchange")

    def test_encrypt_decrypt_256b(self):
        """Encrypt+decrypt 256-byte message."""
        from verlihub.client.nmdcpb.e2epm import E2EPMManager
        mgr_a = E2EPMManager("alice")
        mgr_b = E2EPMManager("bob")
        kex_a = mgr_a.initiate_session("bob")
        kex_b = mgr_b.handle_key_exchange("alice", kex_a)
        mgr_a.handle_key_exchange("bob", kex_b)

        message = "A" * 256
        print()

        def _encrypt_decrypt():
            sess_a = mgr_a._sessions["bob"]
            epm = sess_a.encrypt_message(message)
            sess_b = mgr_b._sessions["alice"]
            sess_b.decrypt_message(epm)

        _benchmark(_encrypt_decrypt, 2000, "encrypt+decrypt(256B)")

    def test_encrypt_only_1k(self):
        """Encrypt-only 1KB message throughput."""
        from verlihub.client.nmdcpb.e2epm import E2EPMManager
        mgr_a = E2EPMManager("alice")
        mgr_b = E2EPMManager("bob")
        kex_a = mgr_a.initiate_session("bob")
        kex_b = mgr_b.handle_key_exchange("alice", kex_a)
        mgr_a.handle_key_exchange("bob", kex_b)

        message = "B" * 1024
        sess_a = mgr_a._sessions["bob"]
        print()
        _benchmark(lambda: sess_a.encrypt_message(message),
                   2000, "encrypt_only(1KB)")


# ---------------------------------------------------------------------------
# Hub relay simulation benchmark
# ---------------------------------------------------------------------------

class TestHubRelaySimulation:
    """Simulate hub relay forwarding with and without opaque optimization."""

    def test_relay_throughput_32k(self):
        """Simulate 32KB relay forwarding throughput."""
        from verlihub.client.nmdcpb import hub_plugin

        # Set up mock session
        hub_plugin._relay_sessions.clear()
        hub_plugin._relay_sessions[42] = hub_plugin._RelaySession(
            42, "alice", "bob", "tok123")

        env = _make_relay_data_envelope(32768, relay_id=42)
        wire = WireCodec.encode_text(env)

        # Decode + get raw bytes for opaque path
        raw_pb = _b64url_decode(
            wire[len("$PB "):].split(" ", 1)[1].rstrip("|"))

        print()

        # Classic forwarding (no raw_pb)
        def _forward_classic():
            e = WireCodec.decode(wire)
            e.from_nick = "alice"
            hub_plugin._forward_relay_data("alice", e, raw_pb=None)

        # Opaque forwarding (with raw_pb)
        def _forward_opaque():
            e = WireCodec.decode(wire)
            e.from_nick = "alice"
            hub_plugin._forward_relay_data("alice", e, raw_pb=raw_pb)

        classic = _benchmark(_forward_classic, 1000,
                             "hub_forward_classic(32KB)")
        opaque = _benchmark(_forward_opaque, 1000,
                            "hub_forward_opaque(32KB)")

        speedup = classic["mean_us"] / max(opaque["mean_us"], 1)
        print(f"  → Hub relay speedup: {speedup:.2f}x")

        # Cleanup
        hub_plugin._relay_sessions.clear()

    def test_envelope_pool(self):
        """Benchmark envelope pool get/return cycle."""
        from verlihub.client.nmdcpb.hub_plugin import (
            _get_envelope, _return_envelope,
        )
        print()

        def _pool_cycle():
            env = _get_envelope()
            env.route = PbEnvelope.DIRECT
            env.from_nick = "alice"
            env.to_nick = "bob"
            env.timestamp = 1234567890
            _return_envelope(env)

        _benchmark(_pool_cycle, 10000, "envelope_pool_get_return")
