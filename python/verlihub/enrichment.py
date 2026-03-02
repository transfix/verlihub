"""
User data enrichment module.

Provides geographic (GeoIP), hostname, clone detection, and share
statistics for online users.  Designed as a lightweight cache layer
that sits between the C++ core snapshots and the API / dashboard.

Thread safety
-------------
All caches are guarded by threading locks and the module is safe
to call from both the asyncio event loop and background threads.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GeoIP lookup (ip-api.com free batch endpoint, 45 req/min)
# ---------------------------------------------------------------------------

_geo_cache: dict[str, dict[str, Any]] = {}
_geo_cache_lock = threading.Lock()
_GEO_TTL = 3600  # 1 hour

# Hostname cache
_host_cache: dict[str, str | None] = {}
_host_cache_lock = threading.Lock()
_HOST_TTL = 1800  # 30 minutes
_host_cache_ts: dict[str, float] = {}

# Share / clone stats cache (recomputed from user list)
_stats_cache: dict[str, Any] = {}
_stats_lock = threading.Lock()


def _is_private_ip(ip: str) -> bool:
    """Return True for localhost / RFC-1918 / link-local addresses."""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# GeoIP
# ---------------------------------------------------------------------------

def lookup_geo(ip: str) -> dict[str, Any]:
    """Return cached GeoIP dict for *ip*.

    Keys: country, country_code, city, region, region_code, timezone,
          continent, lat, lon, isp, org, as_number, as_name.

    Returns empty-ish dict for private / unresolvable IPs.
    """
    if not ip or _is_private_ip(ip):
        return {"country_code": "", "country": "", "city": "", "isp": ""}

    with _geo_cache_lock:
        entry = _geo_cache.get(ip)
        if entry and time.time() - entry.get("_ts", 0) < _GEO_TTL:
            return entry

    # Try ip-api.com (free, 45 req/min per IP, no key required)
    result = _fetch_geo_ipapi(ip)
    with _geo_cache_lock:
        _geo_cache[ip] = result
    return result


def lookup_geo_batch(ips: list[str]) -> dict[str, dict[str, Any]]:
    """Batch GeoIP lookup – uses ip-api.com batch endpoint (max 100)."""
    fresh: dict[str, dict[str, Any]] = {}
    to_fetch: list[str] = []

    now = time.time()
    with _geo_cache_lock:
        for ip in ips:
            if not ip or _is_private_ip(ip):
                fresh[ip] = {"country_code": "", "country": "", "city": "", "isp": ""}
                continue
            entry = _geo_cache.get(ip)
            if entry and now - entry.get("_ts", 0) < _GEO_TTL:
                fresh[ip] = entry
            else:
                to_fetch.append(ip)

    if to_fetch:
        fetched = _fetch_geo_batch_ipapi(to_fetch)
        with _geo_cache_lock:
            _geo_cache.update(fetched)
        fresh.update(fetched)

    return fresh


def _fetch_geo_ipapi(ip: str) -> dict[str, Any]:
    """Single-IP fetch via ip-api.com."""
    import urllib.request
    import json as _json

    url = (
        f"http://ip-api.com/json/{ip}"
        "?fields=status,country,countryCode,regionName,region,"
        "city,zip,lat,lon,timezone,isp,org,as,query,continent,continentCode"
    )
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = _json.loads(resp.read())
        if data.get("status") == "success":
            return _normalize_geo(data)
    except Exception as exc:
        logger.debug("GeoIP lookup failed for %s: %s", ip, exc)
    return {"country_code": "", "country": "", "city": "", "isp": "", "_ts": time.time()}


def _fetch_geo_batch_ipapi(ips: list[str]) -> dict[str, dict[str, Any]]:
    """Batch fetch via ip-api.com/batch (POST, max 100)."""
    import urllib.request
    import json as _json

    results: dict[str, dict[str, Any]] = {}
    fields = (
        "status,country,countryCode,regionName,region,"
        "city,zip,lat,lon,timezone,isp,org,as,query,continent,continentCode"
    )
    url = f"http://ip-api.com/batch?fields={fields}"

    # Process in chunks of 100 (API limit)
    for i in range(0, len(ips), 100):
        chunk = ips[i : i + 100]
        body = _json.dumps(chunk).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data_list = _json.loads(resp.read())
            for entry in data_list:
                ip_key = entry.get("query", "")
                if entry.get("status") == "success" and ip_key:
                    results[ip_key] = _normalize_geo(entry)
                elif ip_key:
                    results[ip_key] = {"country_code": "", "country": "", "city": "", "isp": "", "_ts": time.time()}
        except Exception as exc:
            logger.debug("Batch GeoIP failed: %s", exc)
            for ip in chunk:
                results.setdefault(ip, {"country_code": "", "country": "", "city": "", "isp": "", "_ts": time.time()})

    return results


def _normalize_geo(raw: dict) -> dict[str, Any]:
    """Normalize ip-api.com response to our standard dict."""
    as_field = raw.get("as", "")
    as_number = ""
    as_name = ""
    if as_field:
        parts = as_field.split(" ", 1)
        as_number = parts[0] if parts else ""
        as_name = parts[1] if len(parts) > 1 else ""

    return {
        "country": raw.get("country", ""),
        "country_code": raw.get("countryCode", ""),
        "city": raw.get("city", ""),
        "region": raw.get("regionName", ""),
        "region_code": raw.get("region", ""),
        "timezone": raw.get("timezone", ""),
        "continent": raw.get("continent", ""),
        "continent_code": raw.get("continentCode", ""),
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "isp": raw.get("isp", ""),
        "org": raw.get("org", ""),
        "as_number": as_number,
        "as_name": as_name,
        "_ts": time.time(),
    }


# ---------------------------------------------------------------------------
# Hostname (reverse DNS)
# ---------------------------------------------------------------------------

def lookup_hostname(ip: str) -> str | None:
    """Return cached reverse-DNS hostname for *ip* (or None)."""
    if not ip or _is_private_ip(ip):
        return None

    with _host_cache_lock:
        ts = _host_cache_ts.get(ip, 0)
        if time.time() - ts < _HOST_TTL and ip in _host_cache:
            return _host_cache[ip]

    try:
        hostname = socket.getfqdn(ip)
        # getfqdn returns the IP itself if no name found
        if hostname == ip:
            hostname = None
    except Exception:
        hostname = None

    with _host_cache_lock:
        _host_cache[ip] = hostname
        _host_cache_ts[ip] = time.time()

    return hostname


def lookup_hostnames_batch(ips: list[str]) -> dict[str, str | None]:
    """Batch hostname resolution (sequential, but cached)."""
    results: dict[str, str | None] = {}
    for ip in ips:
        results[ip] = lookup_hostname(ip)
    return results


# ---------------------------------------------------------------------------
# Clone & NAT detection
# ---------------------------------------------------------------------------

@dataclass
class CloneInfo:
    """Clone / NAT detection results for a single user."""
    is_clone: bool = False
    clone_nicks: list[str] = field(default_factory=list)
    same_ip_nicks: list[str] = field(default_factory=list)


def detect_clones(users: list[dict]) -> dict[str, CloneInfo]:
    """Detect clones (same IP + share) and NAT siblings (same IP).

    Returns mapping nick → CloneInfo.
    """
    ip_to_nicks: dict[str, list[str]] = {}
    ip_share_to_nicks: dict[tuple[str, int], list[str]] = {}

    for u in users:
        ip = u.get("ip", "")
        nick = u.get("nick", "")
        share = u.get("share", 0)
        if ip:
            ip_to_nicks.setdefault(ip, []).append(nick)
            ip_share_to_nicks.setdefault((ip, share), []).append(nick)

    results: dict[str, CloneInfo] = {}
    for u in users:
        nick = u.get("nick", "")
        ip = u.get("ip", "")
        share = u.get("share", 0)

        clone_group = ip_share_to_nicks.get((ip, share), [])
        same_ip = ip_to_nicks.get(ip, [])

        results[nick] = CloneInfo(
            is_clone=len(clone_group) > 1,
            clone_nicks=[n for n in clone_group if n != nick],
            same_ip_nicks=[n for n in same_ip if n != nick],
        )
    return results


# ---------------------------------------------------------------------------
# Share statistics
# ---------------------------------------------------------------------------

@dataclass
class ShareStats:
    """Aggregate share statistics."""
    total_bytes: int = 0
    total_formatted: str = "0 B"
    user_count: int = 0
    average_bytes: int = 0
    average_formatted: str = "0 B"
    median_bytes: int = 0
    median_formatted: str = "0 B"
    max_bytes: int = 0
    max_formatted: str = "0 B"
    max_nick: str = ""
    zero_share_count: int = 0


def compute_share_stats(users: list[dict]) -> ShareStats:
    """Compute share statistics from user list."""
    shares = [u.get("share", 0) for u in users]
    if not shares:
        return ShareStats()

    total = sum(shares)
    count = len(shares)
    avg = total // count if count else 0
    sorted_shares = sorted(shares)
    median = sorted_shares[count // 2] if count else 0
    max_share = max(shares) if shares else 0
    max_nick = ""
    for u in users:
        if u.get("share", 0) == max_share and max_share > 0:
            max_nick = u.get("nick", "")
            break
    zero_count = sum(1 for s in shares if s == 0)

    return ShareStats(
        total_bytes=total,
        total_formatted=_format_bytes(total),
        user_count=count,
        average_bytes=avg,
        average_formatted=_format_bytes(avg),
        median_bytes=median,
        median_formatted=_format_bytes(median),
        max_bytes=max_share,
        max_formatted=_format_bytes(max_share),
        max_nick=max_nick,
        zero_share_count=zero_count,
    )


# ---------------------------------------------------------------------------
# Geographic distribution
# ---------------------------------------------------------------------------

def compute_geo_distribution(users: list[dict]) -> list[dict[str, Any]]:
    """Compute country distribution from enriched user list.

    Returns sorted list of {country_code, country, count} dicts.
    """
    country_counts: dict[str, dict[str, Any]] = {}
    for u in users:
        cc = u.get("country_code") or u.get("country") or ""
        if not cc or cc == "--":
            cc = "??"
        if cc not in country_counts:
            country_counts[cc] = {"country_code": cc, "country": u.get("country_name", cc), "count": 0}
        country_counts[cc]["count"] += 1

    return sorted(country_counts.values(), key=lambda x: x["count"], reverse=True)


# ---------------------------------------------------------------------------
# Main enrichment function
# ---------------------------------------------------------------------------

def enrich_user_list(users: list[dict], *, fetch_geo: bool = True, fetch_hostnames: bool = True) -> list[dict]:
    """Enrich a raw user list with geo, hostname, and clone data.

    Modifies dicts **in-place** and returns the same list.
    """
    if not users:
        return users

    ips = list({u.get("ip", "") for u in users if u.get("ip")})

    # GeoIP batch
    geo_map: dict[str, dict] = {}
    if fetch_geo and ips:
        try:
            geo_map = lookup_geo_batch(ips)
        except Exception as exc:
            logger.warning("GeoIP batch failed: %s", exc)

    # Hostname batch
    host_map: dict[str, str | None] = {}
    if fetch_hostnames and ips:
        try:
            host_map = lookup_hostnames_batch(ips)
        except Exception as exc:
            logger.warning("Hostname batch failed: %s", exc)

    # Clone detection
    clone_map = detect_clones(users)

    # Merge into user dicts
    for u in users:
        ip = u.get("ip", "")
        nick = u.get("nick", "")

        # Geo
        geo = geo_map.get(ip, {})
        u["country_code"] = geo.get("country_code", "") or u.get("country", "")
        u["country_name"] = geo.get("country", "")
        u["city"] = geo.get("city", "")
        u["region"] = geo.get("region", "")
        u["timezone"] = geo.get("timezone", "")
        u["isp"] = geo.get("isp", "")
        u["as_number"] = geo.get("as_number", "")
        u["as_name"] = geo.get("as_name", "")
        u["lat"] = geo.get("lat")
        u["lon"] = geo.get("lon")

        # Hostname
        u["hostname"] = host_map.get(ip) or ""

        # Clone / NAT
        ci = clone_map.get(nick, CloneInfo())
        u["is_clone"] = ci.is_clone
        u["clone_nicks"] = ci.clone_nicks
        u["same_ip_nicks"] = ci.same_ip_nicks

    return users


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _format_bytes(size: int) -> str:
    """Format bytes into human-readable string."""
    if size == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024  # type: ignore[assignment]
    return f"{size:.1f} EB"
