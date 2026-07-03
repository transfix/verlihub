"""
Tests for the verlihub.enrichment module.

Covers:
- GeoIP lookup with MaxMind fallback to ip-api.com
- IP classification (private/local)
- Hostname resolution
- Clone / NAT detection
- Share statistics
- Geographic distribution
- Full enrich_user_list pipeline
- MaxMind database discovery
- Cache TTL behaviour
"""
import importlib
import os
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from verlihub import enrichment
from verlihub.enrichment import (
    _is_private_ip,
    _normalize_geo,
    _lookup_maxmind,
    _find_mmdb,
    _get_mmdb_reader,
    lookup_geo,
    lookup_geo_batch,
    lookup_hostname,
    lookup_hostnames_batch,
    detect_clones,
    CloneInfo,
    compute_share_stats,
    ShareStats,
    compute_geo_distribution,
    enrich_user_list,
    _format_bytes,
    _mmdb_localized,
)


# =====================================================================
# IP classification
# =====================================================================

class TestIsPrivateIP:
    """Test _is_private_ip for various address types."""

    def test_localhost_v4(self):
        assert _is_private_ip("127.0.0.1")

    def test_localhost_v6(self):
        assert _is_private_ip("::1")

    def test_private_10(self):
        assert _is_private_ip("10.0.0.1")

    def test_private_172(self):
        assert _is_private_ip("172.16.0.1")

    def test_private_192(self):
        assert _is_private_ip("192.168.1.1")

    def test_link_local(self):
        assert _is_private_ip("169.254.0.1")

    def test_public_ip(self):
        assert not _is_private_ip("8.8.8.8")

    def test_public_ip_1111(self):
        assert not _is_private_ip("1.1.1.1")

    def test_empty_string(self):
        assert _is_private_ip("")

    def test_garbage(self):
        assert _is_private_ip("not_an_ip")


# =====================================================================
# MaxMind database discovery
# =====================================================================

class TestFindMMDB:
    """Test _find_mmdb database file search."""

    def test_no_db_found(self):
        """When no .mmdb files exist, returns None."""
        with patch("os.path.isfile", return_value=False):
            with patch.dict(os.environ, {}, clear=True):
                assert _find_mmdb() is None

    def test_env_var_override(self):
        """GEOIP_DB_PATH env var is checked."""
        fake_path = "/custom/path/GeoLite2-City.mmdb"
        with patch("os.path.isfile", side_effect=lambda p: p == fake_path):
            with patch.dict(os.environ, {"GEOIP_DB_PATH": fake_path}):
                assert _find_mmdb() == fake_path

    def test_standard_path_found(self):
        """Standard system path search works."""
        target = "/usr/share/GeoIP/GeoLite2-City.mmdb"
        with patch("os.path.isfile", side_effect=lambda p: p == target):
            with patch.dict(os.environ, {}, clear=True):
                assert _find_mmdb() == target


# =====================================================================
# MaxMind lookup
# =====================================================================

class TestLookupMaxMind:
    """Test _lookup_maxmind when maxminddb is (not) available."""

    def test_returns_none_when_no_reader(self):
        """No reader → returns None (so ip-api fallback triggers)."""
        with patch("verlihub.enrichment._get_mmdb_reader", return_value=None):
            assert _lookup_maxmind("8.8.8.8") is None

    def test_returns_none_on_no_record(self):
        """Reader returns None for IP → returns None."""
        mock_reader = MagicMock()
        mock_reader.get.return_value = None
        with patch("verlihub.enrichment._get_mmdb_reader", return_value=mock_reader):
            assert _lookup_maxmind("8.8.8.8") is None

    def test_extracts_fields_from_record(self):
        """Full MaxMind record is normalized correctly."""
        mock_reader = MagicMock()
        mock_reader.get.return_value = {
            "country": {"iso_code": "US", "names": {"en": "United States"}},
            "city": {"names": {"en": "Mountain View"}},
            "subdivisions": [{"names": {"en": "California"}, "iso_code": "CA"}],
            "continent": {"code": "NA", "names": {"en": "North America"}},
            "location": {"latitude": 37.386, "longitude": -122.084, "time_zone": "America/Los_Angeles"},
            "traits": {
                "isp": "Google LLC",
                "organization": "Google LLC",
                "autonomous_system_number": 15169,
                "autonomous_system_organization": "Google LLC",
            },
        }
        with patch("verlihub.enrichment._get_mmdb_reader", return_value=mock_reader):
            result = _lookup_maxmind("8.8.8.8")
        assert result is not None
        assert result["country_code"] == "US"
        assert result["country"] == "United States"
        assert result["city"] == "Mountain View"
        assert result["region"] == "California"
        assert result["region_code"] == "CA"
        assert result["timezone"] == "America/Los_Angeles"
        assert result["isp"] == "Google LLC"
        assert result["as_number"] == "15169"
        assert result["as_name"] == "Google LLC"
        assert result["_source"] == "maxmind"

    def test_handles_reader_exception(self):
        """Exception in reader.get() → returns None."""
        mock_reader = MagicMock()
        mock_reader.get.side_effect = Exception("corrupt db")
        with patch("verlihub.enrichment._get_mmdb_reader", return_value=mock_reader):
            assert _lookup_maxmind("8.8.8.8") is None


# =====================================================================
# _mmdb_localized helper
# =====================================================================

class TestMmdbLocalized:
    def test_prefers_en_name(self):
        obj = {"names": {"en": "United States", "de": "Vereinigte Staaten"}}
        assert _mmdb_localized(obj, "name") == "United States"

    def test_falls_back_to_key(self):
        obj = {"name": "Fallback"}
        assert _mmdb_localized(obj, "name") == "Fallback"

    def test_empty_names(self):
        assert _mmdb_localized({}, "name") == ""


# =====================================================================
# GeoIP normalize
# =====================================================================

class TestNormalizeGeo:
    """Test _normalize_geo ip-api.com → standard dict."""

    def test_full_record(self):
        raw = {
            "country": "Germany",
            "countryCode": "DE",
            "regionName": "Bavaria",
            "region": "BY",
            "city": "Munich",
            "timezone": "Europe/Berlin",
            "isp": "Deutsche Telekom AG",
            "org": "DT AG",
            "as": "AS3320 Deutsche Telekom AG",
            "continent": "Europe",
            "continentCode": "EU",
            "lat": 48.1351,
            "lon": 11.582,
        }
        result = _normalize_geo(raw)
        assert result["country_code"] == "DE"
        assert result["country"] == "Germany"
        assert result["city"] == "Munich"
        assert result["region"] == "Bavaria"
        assert result["as_number"] == "AS3320"
        assert result["as_name"] == "Deutsche Telekom AG"
        assert "_ts" in result

    def test_missing_fields(self):
        result = _normalize_geo({})
        assert result["country_code"] == ""
        assert result["country"] == ""

    def test_as_field_no_space(self):
        result = _normalize_geo({"as": "AS1234"})
        assert result["as_number"] == "AS1234"
        assert result["as_name"] == ""


# =====================================================================
# lookup_geo (with cache)
# =====================================================================

class TestLookupGeo:
    """Test lookup_geo caching and MaxMind-first strategy."""

    def setup_method(self):
        # Clear the cache before each test
        with enrichment._geo_cache_lock:
            enrichment._geo_cache.clear()

    def test_private_ip_returns_empty(self):
        result = lookup_geo("192.168.1.1")
        assert result["country_code"] == ""

    def test_empty_ip_returns_empty(self):
        result = lookup_geo("")
        assert result["country_code"] == ""

    def test_maxmind_first_then_cache(self):
        """MaxMind result gets cached."""
        fake = {"country_code": "DE", "country": "Germany", "city": "Berlin",
                "isp": "Test", "_ts": time.time(), "_source": "maxmind"}
        with patch("verlihub.enrichment._lookup_maxmind", return_value=fake):
            r1 = lookup_geo("1.2.3.4")
            assert r1["country_code"] == "DE"
        # Second call should hit cache (no lookup)
        with patch("verlihub.enrichment._lookup_maxmind", side_effect=AssertionError("should not be called")):
            r2 = lookup_geo("1.2.3.4")
            assert r2["country_code"] == "DE"

    def test_ipapi_fallback_when_no_maxmind(self):
        """Falls back to ip-api.com when MaxMind returns None."""
        fake_ipapi = {"country_code": "US", "country": "United States",
                      "city": "Ashburn", "isp": "Cloudflare", "_ts": time.time()}
        with patch("verlihub.enrichment._lookup_maxmind", return_value=None):
            with patch("verlihub.enrichment._fetch_geo_ipapi", return_value=fake_ipapi):
                r = lookup_geo("1.1.1.1")
                assert r["country_code"] == "US"


# =====================================================================
# lookup_geo_batch
# =====================================================================

class TestLookupGeoBatch:
    def setup_method(self):
        with enrichment._geo_cache_lock:
            enrichment._geo_cache.clear()

    def test_private_ips_skip_lookup(self):
        result = lookup_geo_batch(["10.0.0.1", "192.168.1.1"])
        for ip, geo in result.items():
            assert geo["country_code"] == ""

    def test_batch_uses_maxmind_first(self):
        fake = {"country_code": "FR", "country": "France", "city": "Paris",
                "isp": "", "_ts": time.time(), "_source": "maxmind"}
        with patch("verlihub.enrichment._lookup_maxmind", return_value=fake):
            with patch("verlihub.enrichment._fetch_geo_batch_ipapi") as mock_batch:
                result = lookup_geo_batch(["5.6.7.8"])
                mock_batch.assert_not_called()  # MaxMind succeeded, no fallback
                assert result["5.6.7.8"]["country_code"] == "FR"

    def test_batch_falls_back_to_ipapi(self):
        fetched = {"9.8.7.6": {"country_code": "JP", "country": "Japan",
                                "city": "Tokyo", "isp": "", "_ts": time.time()}}
        with patch("verlihub.enrichment._lookup_maxmind", return_value=None):
            with patch("verlihub.enrichment._fetch_geo_batch_ipapi", return_value=fetched):
                result = lookup_geo_batch(["9.8.7.6"])
                assert result["9.8.7.6"]["country_code"] == "JP"


# =====================================================================
# Hostname resolution
# =====================================================================

class TestLookupHostname:
    def setup_method(self):
        with enrichment._host_cache_lock:
            enrichment._host_cache.clear()
            enrichment._host_cache_ts.clear()

    def test_private_ip_returns_none(self):
        assert lookup_hostname("192.168.1.1") is None

    def test_empty_ip_returns_none(self):
        assert lookup_hostname("") is None

    def test_caches_result(self):
        with patch("socket.getfqdn", return_value="dns.google"):
            r1 = lookup_hostname("8.8.8.8")
            assert r1 == "dns.google"
        # Should come from cache now
        with patch("socket.getfqdn", side_effect=AssertionError("should not call")):
            r2 = lookup_hostname("8.8.8.8")
            assert r2 == "dns.google"

    def test_returns_none_when_fqdn_equals_ip(self):
        """getfqdn returns the IP itself when no PTR record exists."""
        with patch("socket.getfqdn", return_value="1.2.3.4"):
            assert lookup_hostname("1.2.3.4") is None


class TestLookupHostnamesBatch:
    def setup_method(self):
        with enrichment._host_cache_lock:
            enrichment._host_cache.clear()
            enrichment._host_cache_ts.clear()

    def test_batch_resolves_multiple(self):
        def fake_fqdn(ip):
            return {"8.8.8.8": "dns.google", "1.1.1.1": "one.one.one.one"}.get(ip, ip)
        with patch("socket.getfqdn", side_effect=fake_fqdn):
            result = lookup_hostnames_batch(["8.8.8.8", "1.1.1.1"])
            assert result["8.8.8.8"] == "dns.google"
            assert result["1.1.1.1"] == "one.one.one.one"


# =====================================================================
# Clone / NAT detection
# =====================================================================

class TestDetectClones:
    def test_no_users(self):
        assert detect_clones([]) == {}

    def test_no_clones(self):
        users = [
            {"nick": "alice", "ip": "1.1.1.1", "share": 100},
            {"nick": "bob", "ip": "2.2.2.2", "share": 200},
        ]
        result = detect_clones(users)
        assert not result["alice"].is_clone
        assert not result["bob"].is_clone
        assert result["alice"].same_ip_nicks == []

    def test_same_ip_different_share_is_nat(self):
        users = [
            {"nick": "alice", "ip": "5.5.5.5", "share": 100},
            {"nick": "bob", "ip": "5.5.5.5", "share": 200},
        ]
        result = detect_clones(users)
        assert not result["alice"].is_clone
        assert result["alice"].same_ip_nicks == ["bob"]
        assert result["bob"].same_ip_nicks == ["alice"]

    def test_same_ip_same_share_is_clone(self):
        users = [
            {"nick": "alice", "ip": "5.5.5.5", "share": 100},
            {"nick": "clone1", "ip": "5.5.5.5", "share": 100},
        ]
        result = detect_clones(users)
        assert result["alice"].is_clone
        assert result["alice"].clone_nicks == ["clone1"]
        assert result["clone1"].clone_nicks == ["alice"]

    def test_mixed_clone_and_nat(self):
        users = [
            {"nick": "a", "ip": "5.5.5.5", "share": 100},
            {"nick": "b", "ip": "5.5.5.5", "share": 100},
            {"nick": "c", "ip": "5.5.5.5", "share": 999},
        ]
        result = detect_clones(users)
        assert result["a"].is_clone
        assert "b" in result["a"].clone_nicks
        assert not result["c"].is_clone
        # All three share the same IP
        assert set(result["a"].same_ip_nicks) == {"b", "c"}


# =====================================================================
# Share statistics
# =====================================================================

class TestComputeShareStats:
    def test_empty_users(self):
        stats = compute_share_stats([])
        assert stats.total_bytes == 0
        assert stats.user_count == 0

    def test_basic_stats(self):
        users = [
            {"nick": "a", "share": 1000},
            {"nick": "b", "share": 2000},
            {"nick": "c", "share": 3000},
        ]
        stats = compute_share_stats(users)
        assert stats.total_bytes == 6000
        assert stats.user_count == 3
        assert stats.average_bytes == 2000
        assert stats.median_bytes == 2000
        assert stats.max_bytes == 3000
        assert stats.max_nick == "c"
        assert stats.zero_share_count == 0

    def test_zero_shares(self):
        users = [{"nick": "a", "share": 0}, {"nick": "b", "share": 0}]
        stats = compute_share_stats(users)
        assert stats.zero_share_count == 2
        assert stats.total_bytes == 0

    def test_single_user(self):
        stats = compute_share_stats([{"nick": "solo", "share": 42}])
        assert stats.total_bytes == 42
        assert stats.median_bytes == 42
        assert stats.max_nick == "solo"


# =====================================================================
# Geographic distribution
# =====================================================================

class TestComputeGeoDistribution:
    def test_empty(self):
        assert compute_geo_distribution([]) == []

    def test_counts_countries(self):
        users = [
            {"country_code": "US", "country_name": "United States"},
            {"country_code": "US", "country_name": "United States"},
            {"country_code": "DE", "country_name": "Germany"},
        ]
        dist = compute_geo_distribution(users)
        assert dist[0]["country_code"] == "US"
        assert dist[0]["count"] == 2
        assert dist[1]["country_code"] == "DE"
        assert dist[1]["count"] == 1

    def test_missing_country_uses_unknown(self):
        users = [{"country_code": "", "country_name": ""}]
        dist = compute_geo_distribution(users)
        assert dist[0]["country_code"] == "??"


# =====================================================================
# Full enrichment pipeline
# =====================================================================

class TestEnrichUserList:
    def setup_method(self):
        with enrichment._geo_cache_lock:
            enrichment._geo_cache.clear()
        with enrichment._host_cache_lock:
            enrichment._host_cache.clear()
            enrichment._host_cache_ts.clear()

    def test_empty_list(self):
        assert enrich_user_list([]) == []

    def test_enriches_in_place(self):
        users = [{"nick": "alice", "ip": "10.0.0.1", "share": 100}]
        result = enrich_user_list(users, fetch_geo=False, fetch_hostnames=False)
        assert result is users  # modified in place
        assert "is_clone" in result[0]
        assert "same_ip_nicks" in result[0]

    def test_geo_and_hostname_added(self):
        fake_geo = {"5.5.5.5": {
            "country_code": "XX", "country": "Testland", "city": "Testville",
            "region": "", "timezone": "", "isp": "", "as_number": "", "as_name": "",
            "lat": None, "lon": None, "_ts": time.time(),
        }}
        with patch("verlihub.enrichment.lookup_geo_batch", return_value=fake_geo):
            with patch("verlihub.enrichment.lookup_hostnames_batch",
                       return_value={"5.5.5.5": "test.example.com"}):
                users = [{"nick": "bob", "ip": "5.5.5.5", "share": 0}]
                enrich_user_list(users)
                assert users[0]["country_code"] == "XX"
                assert users[0]["country_name"] == "Testland"
                assert users[0]["hostname"] == "test.example.com"

    def test_clone_detection_integrated(self):
        users = [
            {"nick": "a", "ip": "5.5.5.5", "share": 100},
            {"nick": "b", "ip": "5.5.5.5", "share": 100},
        ]
        enrich_user_list(users, fetch_geo=False, fetch_hostnames=False)
        assert users[0]["is_clone"] is True
        assert "b" in users[0]["clone_nicks"]

    def test_handles_geo_exception(self):
        """GeoIP batch failure should not crash enrichment."""
        with patch("verlihub.enrichment.lookup_geo_batch", side_effect=Exception("boom")):
            users = [{"nick": "c", "ip": "8.8.8.8", "share": 0}]
            result = enrich_user_list(users, fetch_hostnames=False)
            assert result[0]["country_code"] == ""  # graceful fallback


# =====================================================================
# Utility functions
# =====================================================================

class TestFormatBytes:
    def test_zero(self):
        assert _format_bytes(0) == "0 B"

    def test_bytes(self):
        assert _format_bytes(500) == "500.0 B"

    def test_kilobytes(self):
        assert "KB" in _format_bytes(2048)

    def test_gigabytes(self):
        assert "GB" in _format_bytes(2 * 1024**3)

    def test_terabytes(self):
        assert "TB" in _format_bytes(5 * 1024**4)
