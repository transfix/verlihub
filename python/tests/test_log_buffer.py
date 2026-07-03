"""
Tests for verlihub.log_buffer — LogEntry, LogRingBuffer, singleton.

Covers: entry creation, to_dict, ring buffer capacity/eviction, add/add_from_cpp,
get_all/get_recent, clear, thread safety, singleton, level mapping.
"""
from __future__ import annotations

import threading
import time
from collections import Counter

import pytest


# ======================================================================
# LogEntry
# ======================================================================


class TestLogEntry:

    def test_basic_creation(self):
        from verlihub.log_buffer import LogEntry
        e = LogEntry(level="info", message="hello")
        assert e.level == "info"
        assert e.message == "hello"
        assert e.log_type == "system"
        assert e.level_int == 0
        assert e.time  # non-empty ISO string

    def test_custom_fields(self):
        from verlihub.log_buffer import LogEntry
        e = LogEntry(
            level="debug",
            message="trace msg",
            log_type="core",
            time="2025-01-01T00:00:00Z",
            level_int=3,
        )
        assert e.level == "debug"
        assert e.log_type == "core"
        assert e.level_int == 3
        assert e.time == "2025-01-01T00:00:00Z"

    def test_to_dict(self):
        from verlihub.log_buffer import LogEntry
        e = LogEntry(level="info", message="test", log_type="core", time="T")
        d = e.to_dict()
        assert d == {
            "type": "log",
            "level": "info",
            "message": "test",
            "log_type": "core",
            "time": "T",
        }

    def test_to_dict_has_type_log(self):
        from verlihub.log_buffer import LogEntry
        d = LogEntry(level="debug", message="x").to_dict()
        assert d["type"] == "log"


# ======================================================================
# Level mapping
# ======================================================================


class TestLevelMap:

    def test_level_0_is_info(self):
        from verlihub.log_buffer import _level_str
        assert _level_str(0) == "info"

    def test_level_1_is_info(self):
        from verlihub.log_buffer import _level_str
        assert _level_str(1) == "info"

    def test_level_2_is_debug(self):
        from verlihub.log_buffer import _level_str
        assert _level_str(2) == "debug"

    def test_level_3_is_debug(self):
        from verlihub.log_buffer import _level_str
        assert _level_str(3) == "debug"

    def test_level_4_is_debug(self):
        from verlihub.log_buffer import _level_str
        assert _level_str(4) == "debug"

    def test_unknown_level_defaults_to_debug(self):
        from verlihub.log_buffer import _level_str
        assert _level_str(99) == "debug"
        assert _level_str(-1) == "debug"


# ======================================================================
# LogRingBuffer — basic
# ======================================================================


class TestLogRingBuffer:

    @pytest.fixture
    def buf(self):
        from verlihub.log_buffer import LogRingBuffer
        return LogRingBuffer(capacity=10)

    def test_empty_buffer(self, buf):
        assert len(buf) == 0
        assert buf.get_all() == []

    def test_capacity(self, buf):
        assert buf.capacity == 10

    def test_add_single_entry(self, buf):
        entry = buf.add(level="info", message="hello")
        assert len(buf) == 1
        assert entry.message == "hello"

    def test_add_returns_entry(self, buf):
        from verlihub.log_buffer import LogEntry
        entry = buf.add(level="info", message="m", log_type="core", level_int=2)
        assert isinstance(entry, LogEntry)
        assert entry.level == "info"
        assert entry.log_type == "core"

    def test_append(self, buf):
        from verlihub.log_buffer import LogEntry
        e = LogEntry(level="debug", message="x")
        buf.append(e)
        assert len(buf) == 1
        assert buf.get_all()[0]["message"] == "x"

    def test_get_all_ordered(self, buf):
        buf.add(level="info", message="first")
        buf.add(level="info", message="second")
        buf.add(level="info", message="third")
        items = buf.get_all()
        assert [i["message"] for i in items] == ["first", "second", "third"]

    def test_get_recent_subset(self, buf):
        for i in range(8):
            buf.add(level="info", message=f"msg-{i}")
        recent = buf.get_recent(3)
        assert len(recent) == 3
        assert [r["message"] for r in recent] == ["msg-5", "msg-6", "msg-7"]

    def test_get_recent_more_than_available(self, buf):
        buf.add(level="info", message="only")
        recent = buf.get_recent(100)
        assert len(recent) == 1

    def test_get_recent_returns_dicts(self, buf):
        buf.add(level="info", message="m")
        item = buf.get_recent(1)[0]
        assert isinstance(item, dict)
        assert "type" in item

    def test_clear(self, buf):
        for i in range(5):
            buf.add(level="info", message=f"m{i}")
        cleared = buf.clear()
        assert cleared == 5
        assert len(buf) == 0
        assert buf.get_all() == []

    def test_clear_empty_buffer(self, buf):
        cleared = buf.clear()
        assert cleared == 0


# ======================================================================
# Ring buffer eviction (capacity)
# ======================================================================


class TestRingBufferEviction:

    def test_eviction_at_capacity(self):
        from verlihub.log_buffer import LogRingBuffer
        buf = LogRingBuffer(capacity=3)
        buf.add(level="info", message="a")
        buf.add(level="info", message="b")
        buf.add(level="info", message="c")
        buf.add(level="info", message="d")  # evicts "a"
        assert len(buf) == 3
        msgs = [e["message"] for e in buf.get_all()]
        assert msgs == ["b", "c", "d"]

    def test_heavy_eviction(self):
        from verlihub.log_buffer import LogRingBuffer
        buf = LogRingBuffer(capacity=5)
        for i in range(100):
            buf.add(level="info", message=f"m-{i}")
        assert len(buf) == 5
        msgs = [e["message"] for e in buf.get_all()]
        assert msgs == ["m-95", "m-96", "m-97", "m-98", "m-99"]


# ======================================================================
# add_from_cpp
# ======================================================================


class TestAddFromCpp:

    def test_adds_with_core_log_type(self):
        from verlihub.log_buffer import LogRingBuffer
        buf = LogRingBuffer(capacity=10)
        entry = buf.add_from_cpp(0, "[2025-01-01] [L0] [hub.cpp:42] Hub started")
        assert entry.log_type == "core"
        assert entry.level == "info"
        assert entry.level_int == 0

    def test_debug_level(self):
        from verlihub.log_buffer import LogRingBuffer
        buf = LogRingBuffer(capacity=10)
        entry = buf.add_from_cpp(2, "debug trace")
        assert entry.level == "debug"
        assert entry.level_int == 2

    def test_persisted_in_buffer(self):
        from verlihub.log_buffer import LogRingBuffer
        buf = LogRingBuffer(capacity=10)
        buf.add_from_cpp(1, "some message")
        items = buf.get_all()
        assert len(items) == 1
        assert items[0]["message"] == "some message"
        assert items[0]["log_type"] == "core"


# ======================================================================
# Thread safety
# ======================================================================


class TestThreadSafety:

    def test_concurrent_writes(self):
        from verlihub.log_buffer import LogRingBuffer
        buf = LogRingBuffer(capacity=5000)
        errors: list[Exception] = []

        def writer(prefix: str, count: int):
            try:
                for i in range(count):
                    buf.add(level="info", message=f"{prefix}-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(f"t{t}", 500))
            for t in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent writes: {errors}"
        # All 2000 entries should be present
        assert len(buf) == 2000

    def test_concurrent_read_write(self):
        from verlihub.log_buffer import LogRingBuffer
        buf = LogRingBuffer(capacity=100)
        stop = threading.Event()
        errors: list[Exception] = []

        def writer():
            try:
                i = 0
                while not stop.is_set():
                    buf.add(level="info", message=f"w-{i}")
                    i += 1
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                while not stop.is_set():
                    buf.get_all()
                    buf.get_recent(10)
                    len(buf)
            except Exception as exc:
                errors.append(exc)

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        time.sleep(0.2)
        stop.set()
        w.join()
        r.join()
        assert not errors, f"Errors during concurrent r/w: {errors}"


# ======================================================================
# Singleton
# ======================================================================


class TestSingleton:

    def test_get_log_buffer_returns_same_instance(self):
        from verlihub.log_buffer import get_log_buffer
        a = get_log_buffer()
        b = get_log_buffer()
        assert a is b

    def test_default_capacity(self):
        from verlihub.log_buffer import get_log_buffer, LogRingBuffer
        buf = get_log_buffer()
        assert buf.capacity == LogRingBuffer.DEFAULT_CAPACITY
