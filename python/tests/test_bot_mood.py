"""
Tests for the dynamic mood engine (verlihub.bot_mood).

Covers:
- Mood definitions and the mood grid
- BotMoodEngine interaction recording and rate computation
- BotMoodEngine user ratio computation
- Mood determination across the 3×3 matrix
- Mood text output
- Configurable thresholds
- Data pruning on window expiry
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from verlihub.bot.mood import (
    MOOD_BORED,
    MOOD_CHEERFUL,
    MOOD_CURIOUS,
    MOOD_ECSTATIC,
    MOOD_HAPPY,
    MOOD_LONELY,
    MOOD_MELANCHOLIC,
    MOOD_NEUTRAL,
    MOOD_WISTFUL,
    BotMoodEngine,
    Mood,
    _MOOD_GRID,
)


# ---------------------------------------------------------------------------
# Mood dataclass
# ---------------------------------------------------------------------------

class TestMoodDataclass:

    def test_mood_fields(self):
        m = Mood(name="test", emoji="🧪", prompt_text="testing")
        assert m.name == "test"
        assert m.emoji == "🧪"
        assert m.prompt_text == "testing"

    def test_mood_is_frozen(self):
        with pytest.raises(AttributeError):
            MOOD_NEUTRAL.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Mood grid
# ---------------------------------------------------------------------------

class TestMoodGrid:

    def test_grid_dimensions(self):
        assert len(_MOOD_GRID) == 3
        for row in _MOOD_GRID:
            assert len(row) == 3

    def test_grid_corners(self):
        assert _MOOD_GRID[0][0] is MOOD_LONELY
        assert _MOOD_GRID[0][2] is MOOD_WISTFUL
        assert _MOOD_GRID[2][0] is MOOD_CURIOUS
        assert _MOOD_GRID[2][2] is MOOD_ECSTATIC

    def test_grid_center(self):
        assert _MOOD_GRID[1][1] is MOOD_NEUTRAL

    def test_neutral_has_empty_prompt(self):
        assert MOOD_NEUTRAL.prompt_text == ""

    def test_all_non_neutral_have_prompt(self):
        for row in _MOOD_GRID:
            for mood in row:
                if mood is not MOOD_NEUTRAL:
                    assert mood.prompt_text, f"{mood.name} should have prompt_text"


# ---------------------------------------------------------------------------
# BotMoodEngine — initialization
# ---------------------------------------------------------------------------

class TestMoodEngineInit:

    def test_default_thresholds(self):
        engine = BotMoodEngine()
        assert engine._interaction_window == 3600
        assert engine._user_history_window == 86400
        assert engine._low_int == 2.0
        assert engine._high_int == 10.0
        assert engine._low_usr == 0.5
        assert engine._high_usr == 1.5

    def test_custom_thresholds(self):
        engine = BotMoodEngine(
            interaction_window=1800,
            user_history_window=43200,
            low_interaction_threshold=1.0,
            high_interaction_threshold=20.0,
            low_user_ratio=0.3,
            high_user_ratio=2.0,
        )
        assert engine._interaction_window == 1800
        assert engine._user_history_window == 43200
        assert engine._low_int == 1.0
        assert engine._high_int == 20.0
        assert engine._low_usr == 0.3
        assert engine._high_usr == 2.0


# ---------------------------------------------------------------------------
# BotMoodEngine — interaction rate
# ---------------------------------------------------------------------------

class TestInteractionRate:

    def test_zero_with_no_interactions(self):
        engine = BotMoodEngine()
        assert engine.get_interaction_rate() == 0.0

    def test_rate_calculation(self):
        engine = BotMoodEngine(interaction_window=3600)
        # Record 10 interactions
        for _ in range(10):
            engine.record_interaction()
        # Rate should be 10/hr
        assert engine.get_interaction_rate() == pytest.approx(10.0, abs=0.5)

    def test_old_interactions_pruned(self):
        """Interactions outside the window should be discarded."""
        engine = BotMoodEngine(interaction_window=60)
        now = time.time()
        # Inject old interactions manually
        engine._interactions.append(now - 120)  # 2 mins ago, outside 60s window
        engine._interactions.append(now - 10)   # 10 secs ago, inside window
        # After pruning, only 1 should remain
        rate = engine.get_interaction_rate()
        # 1 interaction in 60s = 60 msgs/hr
        assert rate == pytest.approx(60.0, abs=1.0)


# ---------------------------------------------------------------------------
# BotMoodEngine — user ratio
# ---------------------------------------------------------------------------

class TestUserRatio:

    def test_none_with_no_samples(self):
        engine = BotMoodEngine()
        assert engine.get_user_ratio() is None

    def test_ratio_single_sample(self):
        engine = BotMoodEngine()
        engine.sample_user_count(100)
        # Single sample: current/avg = 100/100 = 1.0
        assert engine.get_user_ratio() == pytest.approx(1.0)

    def test_ratio_above_average(self):
        engine = BotMoodEngine()
        engine.sample_user_count(50)
        engine.sample_user_count(50)
        engine.sample_user_count(150)  # current
        # avg = (50+50+150)/3 ≈ 83.3, ratio = 150/83.3 ≈ 1.8
        ratio = engine.get_user_ratio()
        assert ratio is not None
        assert ratio > 1.5

    def test_ratio_below_average(self):
        engine = BotMoodEngine()
        engine.sample_user_count(200)
        engine.sample_user_count(200)
        engine.sample_user_count(50)  # current
        # avg ≈ 150, ratio ≈ 0.33
        ratio = engine.get_user_ratio()
        assert ratio is not None
        assert ratio < 0.5

    def test_ratio_zero_average(self):
        engine = BotMoodEngine()
        engine.sample_user_count(0)
        assert engine.get_user_ratio() is None

    def test_old_samples_pruned(self):
        engine = BotMoodEngine(user_history_window=60)
        now = time.time()
        engine._user_samples.append((now - 120, 200))  # old, should be pruned
        engine._user_samples.append((now - 10, 100))    # recent
        ratio = engine.get_user_ratio()
        # Only 1 sample left → ratio = 100/100 = 1.0
        assert ratio == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# BotMoodEngine — mood computation (the 3×3 matrix)
# ---------------------------------------------------------------------------

class TestMoodComputation:

    def test_default_neutral(self):
        """With no data, mood should be bored (low interaction, normal users)."""
        engine = BotMoodEngine()
        # No interactions → rate 0 (low), no user samples → assume normal
        assert engine.get_mood() is MOOD_BORED

    def test_lonely_low_users_low_interaction(self):
        """Low users + low interaction → lonely."""
        engine = BotMoodEngine(
            low_interaction_threshold=5.0,
            high_interaction_threshold=20.0,
            low_user_ratio=0.5,
            high_user_ratio=1.5,
        )
        # Low user ratio
        engine.sample_user_count(100)
        engine.sample_user_count(100)
        engine.sample_user_count(20)  # ratio = 20/73.3 ≈ 0.27
        # No interactions → rate = 0
        assert engine.get_mood() is MOOD_LONELY

    def test_ecstatic_high_users_high_interaction(self):
        """High users + high interaction → ecstatic."""
        engine = BotMoodEngine(
            interaction_window=3600,
            low_interaction_threshold=5.0,
            high_interaction_threshold=20.0,
            low_user_ratio=0.5,
            high_user_ratio=1.5,
        )
        # High user ratio
        engine.sample_user_count(50)
        engine.sample_user_count(50)
        engine.sample_user_count(200)  # ratio = 200/100 = 2.0
        # Many interactions (>20/hr)
        for _ in range(25):
            engine.record_interaction()
        assert engine.get_mood() is MOOD_ECSTATIC

    def test_bored_normal_users_low_interaction(self):
        engine = BotMoodEngine()
        engine.sample_user_count(100)
        engine.sample_user_count(100)
        engine.sample_user_count(100)  # ratio = 1.0 (normal)
        # No interactions → low
        assert engine.get_mood() is MOOD_BORED

    def test_cheerful_normal_users_high_interaction(self):
        engine = BotMoodEngine(interaction_window=3600)
        engine.sample_user_count(100)
        engine.sample_user_count(100)
        engine.sample_user_count(100)  # ratio = 1.0 (normal)
        # 15 interactions → >10/hr (high)
        for _ in range(15):
            engine.record_interaction()
        assert engine.get_mood() is MOOD_CHEERFUL

    def test_happy_high_users_normal_interaction(self):
        engine = BotMoodEngine(interaction_window=3600)
        engine.sample_user_count(50)
        engine.sample_user_count(50)
        engine.sample_user_count(200)  # ratio = 2.0 (high)
        # 5 interactions → between 2 and 10 (normal)
        for _ in range(5):
            engine.record_interaction()
        assert engine.get_mood() is MOOD_HAPPY

    def test_wistful_low_users_high_interaction(self):
        engine = BotMoodEngine(interaction_window=3600)
        engine.sample_user_count(100)
        engine.sample_user_count(100)
        engine.sample_user_count(20)  # ratio ≈ 0.27 (low)
        for _ in range(15):
            engine.record_interaction()
        assert engine.get_mood() is MOOD_WISTFUL

    def test_melancholic_low_users_normal_interaction(self):
        engine = BotMoodEngine(interaction_window=3600)
        engine.sample_user_count(100)
        engine.sample_user_count(100)
        engine.sample_user_count(20)  # ratio ≈ 0.27 (low)
        for _ in range(5):
            engine.record_interaction()
        assert engine.get_mood() is MOOD_MELANCHOLIC

    def test_curious_high_users_low_interaction(self):
        engine = BotMoodEngine()
        engine.sample_user_count(50)
        engine.sample_user_count(50)
        engine.sample_user_count(200)  # ratio = 2.0 (high)
        # No interactions
        assert engine.get_mood() is MOOD_CURIOUS


# ---------------------------------------------------------------------------
# BotMoodEngine — mood text
# ---------------------------------------------------------------------------

class TestMoodText:

    def test_neutral_empty_text(self):
        engine = BotMoodEngine()
        # Need normal interaction and normal users for neutral
        engine.sample_user_count(100)
        engine.sample_user_count(100)
        for _ in range(5):
            engine.record_interaction()
        assert engine.get_mood_text() == ""

    def test_non_neutral_has_text(self):
        engine = BotMoodEngine()
        engine.sample_user_count(100)
        engine.sample_user_count(100)
        engine.sample_user_count(20)  # → low users
        # No interactions → lonely
        text = engine.get_mood_text()
        assert len(text) > 0
        assert "lonely" in text.lower() or "quiet" in text.lower()


# ---------------------------------------------------------------------------
# Configurable thresholds affect mood
# ---------------------------------------------------------------------------

class TestConfigurableThresholds:

    def test_wider_normal_range(self):
        """With very wide thresholds, everything should be 'normal'."""
        engine = BotMoodEngine(
            low_interaction_threshold=0.001,
            high_interaction_threshold=99999.0,
            low_user_ratio=0.001,
            high_user_ratio=99999.0,
        )
        engine.sample_user_count(100)
        for _ in range(5):
            engine.record_interaction()
        assert engine.get_mood() is MOOD_NEUTRAL

    def test_tight_thresholds(self):
        """With very tight thresholds, any activity is 'high'."""
        engine = BotMoodEngine(
            interaction_window=3600,
            low_interaction_threshold=0.001,
            high_interaction_threshold=0.5,
            low_user_ratio=0.001,
            high_user_ratio=0.5,
        )
        engine.sample_user_count(100)
        engine.sample_user_count(100)
        engine.record_interaction()
        assert engine.get_mood() is MOOD_ECSTATIC
