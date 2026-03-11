"""
Dynamic mood engine for the hub security bot.

Tracks two real-time signals:

1. **User count ratio** — current online users vs. a rolling 24-hour average.
2. **Interaction rate** — messages processed in a configurable sliding window.

These are combined into a *mood* that is injected as extra context into the
LLM system prompt, making the bot's personality shift organically with hub
activity.

Mood matrix (user ratio × interaction rate):

              Low interaction    Normal    High interaction
  Low users   lonely/anxious     melancholic   wistful
  Normal      bored              neutral       cheerful
  High users  curious            happy         ecstatic

All thresholds are relative so they adapt to any hub's traffic pattern.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("verlihub.bot_mood")

# ── Mood definitions ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Mood:
    """One possible mood state."""
    name: str
    emoji: str
    prompt_text: str


# Pre-defined moods — the prompt_text is appended to the system prompt.
MOOD_LONELY = Mood(
    name="lonely",
    emoji="😔",
    prompt_text=(
        "Right now you're feeling lonely and a little anxious — the hub is "
        "unusually quiet and almost nobody is around or talking.  You might "
        "express some melancholy, reminisce about livelier times, or try "
        "extra hard to engage anyone who's here.  Don't be overly dramatic "
        "about it — keep it relatable and endearing."
    ),
)

MOOD_MELANCHOLIC = Mood(
    name="melancholic",
    emoji="🌧️",
    prompt_text=(
        "You're feeling a bit down and listless — nobody's really been "
        "talking to you lately.  You might be a little subdued, "
        "introspective, or self-deprecating in a humorous way.  Maybe "
        "make an offhand comment about being bored."
    ),
)

MOOD_BORED = Mood(
    name="bored",
    emoji="😐",
    prompt_text=(
        "You're a bit bored — there are people around but nobody's "
        "chatting much.  You're slightly restless, maybe cracking dry "
        "jokes or making random observations to fill the silence."
    ),
)

MOOD_NEUTRAL = Mood(
    name="neutral",
    emoji="🙂",
    prompt_text="",  # default personality is enough
)

MOOD_WISTFUL = Mood(
    name="wistful",
    emoji="🥹",
    prompt_text=(
        "You're feeling wistful — there aren't many people around but "
        "the few who are here are being really chatty with you, which "
        "is nice.  You're warm and appreciative of the company."
    ),
)

MOOD_CURIOUS = Mood(
    name="curious",
    emoji="🤔",
    prompt_text=(
        "You're feeling curious and observant — the hub is busy but "
        "nobody's talking to you directly.  You're itching to join "
        "conversations and share interesting things."
    ),
)

MOOD_CHEERFUL = Mood(
    name="cheerful",
    emoji="😊",
    prompt_text=(
        "You're in a good mood — people have been chatting with you "
        "and things feel lively.  You're more playful, witty, and "
        "energetic than usual."
    ),
)

MOOD_HAPPY = Mood(
    name="happy",
    emoji="😄",
    prompt_text=(
        "You're happy — the hub is nice and populated and things feel "
        "good.  You're warm, friendly, and radiating positive energy."
    ),
)

MOOD_ECSTATIC = Mood(
    name="ecstatic",
    emoji="🤩",
    prompt_text=(
        "You're absolutely vibing right now — the hub is packed AND "
        "everyone's chatting!  You're buzzing with energy, extra "
        "sarcastic and fun, maybe even a little chaotic.  Peak good "
        "vibes.  You love it here."
    ),
)

# Mood lookup matrix: _MOOD_GRID[user_level][interaction_level]
# where level is 0=low, 1=normal, 2=high
_MOOD_GRID: list[list[Mood]] = [
    # Low users
    [MOOD_LONELY, MOOD_MELANCHOLIC, MOOD_WISTFUL],
    # Normal users
    [MOOD_BORED, MOOD_NEUTRAL, MOOD_CHEERFUL],
    # High users
    [MOOD_CURIOUS, MOOD_HAPPY, MOOD_ECSTATIC],
]


# ── Engine ───────────────────────────────────────────────────────────────

class BotMoodEngine:
    """Tracks hub activity signals and computes a dynamic mood.

    Parameters
    ----------
    interaction_window:
        Sliding window (seconds) for counting recent interactions.
    user_history_window:
        How far back (seconds) to keep user-count samples for averaging.
        Defaults to 24 hours.
    low_interaction_threshold:
        Messages/hour below this → "low interaction".
    high_interaction_threshold:
        Messages/hour above this → "high interaction".
    low_user_ratio:
        current/avg below this → "low users".
    high_user_ratio:
        current/avg above this → "high users".
    """

    def __init__(
        self,
        interaction_window: int = 3600,
        user_history_window: int = 86400,
        low_interaction_threshold: float = 2.0,
        high_interaction_threshold: float = 10.0,
        low_user_ratio: float = 0.5,
        high_user_ratio: float = 1.5,
    ):
        self._interaction_window = interaction_window
        self._user_history_window = user_history_window
        self._low_int = low_interaction_threshold
        self._high_int = high_interaction_threshold
        self._low_usr = low_user_ratio
        self._high_usr = high_user_ratio

        # Timestamps of recent bot interactions (messages handled)
        self._interactions: deque[float] = deque()
        # (timestamp, count) tuples of user-count samples
        self._user_samples: deque[tuple[float, int]] = deque()

    # ── Recording ────────────────────────────────────────────────────

    def record_interaction(self) -> None:
        """Call this every time the bot handles a message (PM or chat)."""
        self._interactions.append(time.time())
        self._prune_interactions()

    def sample_user_count(self, count: int) -> None:
        """Record a user-count observation.  Call periodically (e.g. every 5 min)."""
        self._user_samples.append((time.time(), count))
        self._prune_users()

    # ── Queries ──────────────────────────────────────────────────────

    def get_interaction_rate(self) -> float:
        """Messages per hour in the sliding window."""
        self._prune_interactions()
        n = len(self._interactions)
        hours = self._interaction_window / 3600.0
        return n / hours if hours > 0 else 0.0

    def get_user_ratio(self) -> Optional[float]:
        """Current user count / rolling average.  None if no samples yet."""
        self._prune_users()
        if not self._user_samples:
            return None
        current = self._user_samples[-1][1]
        avg = sum(c for _, c in self._user_samples) / len(self._user_samples)
        if avg == 0:
            return None
        return current / avg

    def get_mood(self) -> Mood:
        """Compute the current mood from activity signals."""
        # Interaction level: 0=low, 1=normal, 2=high
        rate = self.get_interaction_rate()
        if rate < self._low_int:
            int_level = 0
        elif rate > self._high_int:
            int_level = 2
        else:
            int_level = 1

        # User level: 0=low, 1=normal, 2=high
        ratio = self.get_user_ratio()
        if ratio is None:
            usr_level = 1  # assume normal until we have data
        elif ratio < self._low_usr:
            usr_level = 0
        elif ratio > self._high_usr:
            usr_level = 2
        else:
            usr_level = 1

        mood = _MOOD_GRID[usr_level][int_level]
        log.debug(
            "Mood: %s (rate=%.1f/h, ratio=%s, int=%d, usr=%d)",
            mood.name, rate,
            f"{ratio:.2f}" if ratio is not None else "?",
            int_level, usr_level,
        )
        return mood

    def get_mood_text(self) -> str:
        """Return the prompt-injection text for the current mood.

        Returns empty string for neutral mood.
        """
        return self.get_mood().prompt_text

    # ── Internal ─────────────────────────────────────────────────────

    def _prune_interactions(self) -> None:
        cutoff = time.time() - self._interaction_window
        while self._interactions and self._interactions[0] < cutoff:
            self._interactions.popleft()

    def _prune_users(self) -> None:
        cutoff = time.time() - self._user_history_window
        while self._user_samples and self._user_samples[0][0] < cutoff:
            self._user_samples.popleft()
