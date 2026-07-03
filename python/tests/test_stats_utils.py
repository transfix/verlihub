"""
Tests for verlihub.api.routes.stats — utility functions and endpoint helpers.

Covers: format_bytes, format_uptime, get_class_name, get_country_name,
get/set_hub_start_time.
"""
from __future__ import annotations

import time

import pytest

from verlihub.api.routes.stats import (
    format_bytes,
    format_uptime,
    get_class_name,
    get_country_name,
    get_hub_start_time,
    set_hub_start_time,
)


# ======================================================================
# format_bytes
# ======================================================================


class TestFormatBytes:

    def test_zero(self):
        assert format_bytes(0) == "0.00 B"

    def test_bytes(self):
        assert format_bytes(500) == "500.00 B"

    def test_kilobytes(self):
        assert format_bytes(1024) == "1.00 KB"

    def test_megabytes(self):
        assert format_bytes(1024 * 1024) == "1.00 MB"

    def test_gigabytes(self):
        assert format_bytes(1024 ** 3) == "1.00 GB"

    def test_terabytes(self):
        assert format_bytes(1024 ** 4) == "1.00 TB"

    def test_petabytes(self):
        assert format_bytes(1024 ** 5) == "1.00 PB"

    def test_exabytes(self):
        assert format_bytes(1024 ** 6) == "1.00 EB"

    def test_fractional(self):
        result = format_bytes(1536)
        assert result == "1.50 KB"


# ======================================================================
# format_uptime
# ======================================================================


class TestFormatUptime:

    def test_zero_seconds(self):
        assert format_uptime(0) == "0s"

    def test_seconds_only(self):
        assert format_uptime(42) == "42s"

    def test_minutes_and_seconds(self):
        result = format_uptime(125)  # 2m 5s
        assert "2m" in result
        assert "5s" in result

    def test_hours(self):
        result = format_uptime(3665)  # 1h 1m 5s
        assert "1h" in result
        assert "1m" in result
        assert "5s" in result

    def test_days(self):
        result = format_uptime(86400 + 7200 + 180 + 5)  # 1d 2h 3m 5s
        assert "1d" in result
        assert "2h" in result
        assert "3m" in result
        assert "5s" in result

    def test_days_zero_hours(self):
        result = format_uptime(86400 + 60 + 1)  # 1d 0h 1m 1s
        assert "1d" in result
        assert "0h" in result  # zero hours still shown when days > 0

    def test_large_uptime(self):
        result = format_uptime(30 * 86400)  # 30 days
        assert "30d" in result


# ======================================================================
# get_class_name
# ======================================================================


class TestGetClassName:

    @pytest.mark.parametrize("class_num,expected", [
        (-1, "Disconnected"),
        (0, "Guest"),
        (1, "Regular"),
        (2, "VIP"),
        (3, "Operator"),
        (4, "Cheef"),
        (5, "Admin"),
        (10, "Master"),
    ])
    def test_known_classes(self, class_num, expected):
        assert get_class_name(class_num) == expected

    def test_unknown_class(self):
        assert get_class_name(99) == "Class99"

    def test_negative_unknown(self):
        assert get_class_name(-5) == "Class-5"


# ======================================================================
# get_country_name
# ======================================================================


class TestGetCountryName:

    def test_known_country(self):
        assert get_country_name("US") == "United States"
        assert get_country_name("GB") == "United Kingdom"
        assert get_country_name("DE") == "Germany"

    def test_case_insensitive(self):
        assert get_country_name("us") == "United States"
        assert get_country_name("Us") == "United States"

    def test_unknown_country(self):
        assert get_country_name("XX") == "XX"

    def test_empty_string(self):
        assert get_country_name("") == ""


# ======================================================================
# get/set_hub_start_time
# ======================================================================


class TestHubStartTime:

    def test_set_and_get(self):
        ts = 1700000000.0
        set_hub_start_time(ts)
        assert get_hub_start_time() == ts

    def test_get_auto_initializes(self):
        """If never set, get_hub_start_time should return current time."""
        import verlihub.api.routes.stats as stats_mod
        stats_mod._hub_start_time = None
        t = get_hub_start_time()
        assert abs(t - time.time()) < 2.0
