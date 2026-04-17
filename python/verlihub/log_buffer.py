"""
Thread-safe in-memory ring buffer for hub log entries.

Stores the last N log entries so the dashboard can display historical
logs on page load, while the WebSocket streams new entries in real time.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional


# C++ HubContext::Log levels → dashboard level strings
_LEVEL_MAP = {
    0: "info",      # Critical / startup info
    1: "info",      # General info
    2: "debug",     # Debug
    3: "debug",     # Trace
    4: "debug",     # Verbose trace
}


def _level_str(level: int) -> str:
    """Map C++ integer log level to dashboard string."""
    return _LEVEL_MAP.get(level, "debug")


class LogEntry:
    """A single log entry."""
    __slots__ = ("level", "level_int", "message", "time", "log_type")

    def __init__(
        self,
        level: str,
        message: str,
        log_type: str = "system",
        time: Optional[str] = None,
        level_int: int = 0,
    ) -> None:
        self.level = level
        self.level_int = level_int
        self.message = message
        self.log_type = log_type
        self.time = time or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "type": "log",
            "level": self.level,
            "message": self.message,
            "log_type": self.log_type,
            "time": self.time,
        }


class LogRingBuffer:
    """
    Thread-safe ring buffer for log entries.

    Uses ``collections.deque(maxlen=...)`` for O(1) append with
    automatic eviction of the oldest entry when full.
    """

    DEFAULT_CAPACITY = 2000

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._buf: deque[LogEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._buf.maxlen  # type: ignore[return-value]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    # ----- Write -----

    def append(self, entry: LogEntry) -> None:
        """Append a log entry (thread-safe, O(1))."""
        with self._lock:
            self._buf.append(entry)

    def add(
        self,
        level: str,
        message: str,
        log_type: str = "system",
        level_int: int = 0,
    ) -> LogEntry:
        """Create and append a log entry in one call.  Returns the entry."""
        entry = LogEntry(level=level, message=message, log_type=log_type, level_int=level_int)
        self.append(entry)
        return entry

    def add_from_cpp(self, level_int: int, message: str) -> LogEntry:
        """Add a log entry originating from the C++ core's ``OnLog`` callback."""
        return self.add(
            level=_level_str(level_int),
            message=message,
            log_type="core",
            level_int=level_int,
        )

    # ----- Read -----

    def get_all(self) -> list[dict]:
        """Return *all* buffered entries as dicts (oldest first)."""
        with self._lock:
            return [e.to_dict() for e in self._buf]

    def get_recent(self, n: int = 200) -> list[dict]:
        """Return the *n* most recent entries as dicts (oldest first)."""
        with self._lock:
            if n >= len(self._buf):
                return [e.to_dict() for e in self._buf]
            # Slice the last n items from the deque
            items = list(self._buf)[-n:]
            return [e.to_dict() for e in items]

    # ----- Admin -----

    def clear(self) -> int:
        """Clear all entries. Returns the number of entries removed."""
        with self._lock:
            count = len(self._buf)
            self._buf.clear()
            return count


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_log_buffer: Optional[LogRingBuffer] = None


def get_log_buffer() -> LogRingBuffer:
    """Get (or create) the global log ring buffer singleton."""
    global _log_buffer
    if _log_buffer is None:
        _log_buffer = LogRingBuffer()
    return _log_buffer
