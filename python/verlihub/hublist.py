"""
Hub List registration client and server.

This module provides:

1. **Registration Client** — Periodically registers this hub on one or more
   external hublist servers (e.g. hublist.te-home.net) so it shows up in public
   hub directories.

2. **Hublist Server** — A FastAPI router that lets *other* hubs register on
   this Verlihub-py instance, turning it into a hublist directory itself.

3. **Hublist Administration** — Master-only endpoints for browsing, searching,
   and blocking hubs from the hublist.

The NMDC hublist "protocol" is a simple HTTP POST with form-encoded fields::

    Name, Host, Description, Users, Share, Minshare, Maxusers, Country,
    Encoding, Owner, Website, Status, Software

The response is either "OK" or an error message.  Public hublist requests
(clients downloading the list) use HTTP GET and expect XML, JSON, or plain
text.

Configuration lives in ``VerlihubConfig.hub.hublist_servers`` (list of
server URLs to register *on*) and ``VerlihubConfig.hublist`` (server
settings for hosting a hublist).
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.api.auth import RequireMaster
from verlihub.models import (
    HubListBlock,
    HubListBlockCreate,
    HubListBlockRead,
    HubListBlockType,
    HubListEntry,
    HubListEntryCreate,
    HubListEntryRead,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Public Constants
# =============================================================================

DEFAULT_REGISTRATION_INTERVAL = 600  # 10 minutes
STALE_HUB_TIMEOUT = 1800  # 30 minutes without a ping -> prune
HUBLIST_XML_CONTENT_TYPE = "text/xml; charset=utf-8"


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime (matching SQLite storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(dt: datetime) -> datetime:
    """Strip timezone info for safe cross-backend comparison."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt

# =============================================================================
# Registration Client -- register *this* hub on external hublist servers
# =============================================================================


class HubListRegistrationClient:
    """
    Periodically POST hub info to external hublist servers.

    Usage::

        client = HubListRegistrationClient(servers=["hublist.te-home.net"])
        await client.start(hub_info_callback)
        ...
        await client.stop()

    ``hub_info_callback`` is a callable that returns a dict with current hub
    info (name, address, users, share, etc.).
    """

    def __init__(
        self,
        servers: list[str] | None = None,
        interval: int = DEFAULT_REGISTRATION_INTERVAL,
    ) -> None:
        self.servers: list[str] = servers or []
        self.interval = interval
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_results: dict[str, str] = {}  # server -> last result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, hub_info_fn) -> None:
        """Start the background registration loop."""
        if self._running:
            return
        self._running = True
        self._hub_info_fn = hub_info_fn
        self._task = asyncio.create_task(self._loop(), name="hublist-registration")
        logger.info("Hublist registration client started (%d servers)", len(self.servers))

    async def stop(self) -> None:
        """Stop the background registration loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Hublist registration client stopped")

    @property
    def last_results(self) -> dict[str, str]:
        """Results of the most recent registration round."""
        return dict(self._last_results)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                info = self._hub_info_fn()
                await self._register_all(info)
            except Exception:
                logger.exception("Hublist registration round failed")
            await asyncio.sleep(self.interval)

    async def _register_all(self, info: dict) -> None:
        """Register on all configured servers."""
        for server in self.servers:
            try:
                result = await self._register_one(server, info)
                self._last_results[server] = result
                logger.debug("Registered on %s: %s", server, result)
            except Exception as exc:
                self._last_results[server] = f"error: {exc}"
                logger.warning("Failed to register on %s: %s", server, exc)

    @staticmethod
    async def _register_one(server: str, info: dict) -> str:
        """POST hub info to a single hublist server."""
        if server.startswith("http://") or server.startswith("https://"):
            url = server
        else:
            url = f"http://{server}"

        form = {
            "Name": info.get("name", ""),
            "Host": info.get("address", ""),
            "Description": info.get("description", ""),
            "Users": str(info.get("users", 0)),
            "Share": str(info.get("share", 0)),
            "Minshare": str(info.get("min_share", 0)),
            "Maxusers": str(info.get("max_users", 0)),
            "Country": info.get("country", ""),
            "Encoding": info.get("encoding", "UTF-8"),
            "Owner": info.get("owner", ""),
            "Website": info.get("website", ""),
            "Status": str(info.get("status", 1)),
            "Software": info.get("software", "Verlihub-py"),
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data=form)
            resp.raise_for_status()
            return resp.text.strip()[:200]


# =============================================================================
# Hublist Server -- let other hubs register on this instance
# =============================================================================

hublist_router = APIRouter()


async def _get_session():
    """Get database session for hublist endpoints."""
    from verlihub.models.database import get_database
    db = get_database()
    async with db._session_factory() as session:
        yield session


class HubListStats(BaseModel):
    """Basic stats about the hublist."""
    total_hubs: int
    total_users: int
    total_share: int  # bytes


# --------------------------------------------------------------------------
# Geo enrichment helpers
# --------------------------------------------------------------------------

def _extract_ip_from_address(address: str) -> str:
    """Extract the IP address from a hub address like dchub://1.2.3.4:411."""
    try:
        parsed = urlparse(address)
        host = parsed.hostname or ""
        try:
            socket.inet_aton(host)
            return host
        except OSError:
            pass
        try:
            socket.inet_pton(socket.AF_INET6, host)
            return host
        except OSError:
            pass
        try:
            info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            if info:
                return info[0][4][0]
        except Exception:
            pass
    except Exception:
        pass
    return ""


def _resolve_hostname(ip: str) -> str:
    """Reverse DNS lookup for an IP (best-effort)."""
    if not ip:
        return ""
    try:
        from verlihub.enrichment import lookup_hostname
        return lookup_hostname(ip) or ""
    except Exception:
        pass
    try:
        host, _, _ = socket.gethostbyaddr(ip)
        return host
    except Exception:
        return ""


def _lookup_geo_for_ip(ip: str) -> dict[str, Any]:
    """GeoIP lookup returning country_code, city, asn string."""
    if not ip:
        return {}
    try:
        from verlihub.enrichment import lookup_geo
        return lookup_geo(ip)
    except Exception:
        return {}


def _extract_domain(hostname: str) -> str:
    """Extract registrable domain from a hostname (last two labels)."""
    if not hostname:
        return ""
    parts = hostname.rstrip(".").split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return hostname


def _enrich_hub(hub: HubListEntry, request_ip: str | None = None) -> None:
    """Enrich a hub entry with GeoIP and hostname data (in-place)."""
    ip = request_ip or _extract_ip_from_address(hub.address)
    if ip:
        hub.ip = ip
        hub.hostname = _resolve_hostname(ip)
        geo = _lookup_geo_for_ip(ip)
        if geo:
            hub.city = geo.get("city", "")
            cc = geo.get("country_code", "")
            if cc and not hub.country:
                hub.country = cc
            as_num = geo.get("as_number", "")
            as_name = geo.get("as_name", "")
            hub.asn = f"{as_num} {as_name}".strip() if as_num else ""


# --------------------------------------------------------------------------
# Block checking helpers
# --------------------------------------------------------------------------

async def check_hub_blocked(
    session: AsyncSession,
    *,
    ip: str = "",
    hostname: str = "",
    address: str = "",
    country: str = "",
    city: str = "",
    asn: str = "",
) -> HubListBlock | None:
    """Return the first matching block rule, or None."""
    now = _utcnow()

    checks: list[tuple[HubListBlockType, str]] = []
    if ip:
        checks.append((HubListBlockType.IP, ip))
    if hostname:
        checks.append((HubListBlockType.HOSTNAME, hostname))
        domain = _extract_domain(hostname)
        if domain:
            checks.append((HubListBlockType.DOMAIN, domain))
    if not hostname:
        try:
            parsed = urlparse(address)
            host = parsed.hostname or ""
            domain = _extract_domain(host)
            if domain:
                checks.append((HubListBlockType.DOMAIN, domain))
        except Exception:
            pass
    if country:
        checks.append((HubListBlockType.COUNTRY, country.upper()))
    if city:
        checks.append((HubListBlockType.CITY, city))
    if asn:
        checks.append((HubListBlockType.ASN, asn))
        asn_number = asn.split()[0] if asn else ""
        if asn_number and asn_number != asn:
            checks.append((HubListBlockType.ASN, asn_number))

    for bt, bv in checks:
        q = select(HubListBlock).where(
            HubListBlock.block_type == bt,
            HubListBlock.value == bv,
        )
        result = await session.execute(q)
        block = result.scalar_one_or_none()
        if block:
            if block.expires_at and _as_naive(block.expires_at) < now:
                await session.delete(block)
                await session.commit()
                continue
            return block

    return None


# --------------------------------------------------------------------------
# WebSocket broadcast helper
# --------------------------------------------------------------------------

def _emit_hublist_event(event_type: str, data: dict) -> None:
    """Emit a hublist event via the WebSocket broadcast system."""
    try:
        from verlihub.dashboard.websocket import manager
        payload = {
            "type": event_type,
            "time": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        asyncio.ensure_future(manager.broadcast("hublist", payload))
    except Exception:
        pass


def _hub_to_dict(hub: HubListEntry) -> dict:
    """Convert a HubListEntry to a JSON-serializable dict."""
    return {
        "id": hub.id,
        "name": hub.name,
        "address": hub.address,
        "description": hub.description,
        "users": hub.users,
        "share": hub.share,
        "min_share": hub.min_share,
        "max_users": hub.max_users,
        "country": hub.country,
        "encoding": hub.encoding,
        "owner": hub.owner,
        "email": hub.email,
        "website": hub.website,
        "logo": hub.logo,
        "status": hub.status,
        "software": hub.software,
        "ip": hub.ip,
        "hostname": hub.hostname,
        "city": hub.city,
        "asn": hub.asn,
        "last_seen": hub.last_seen.isoformat() if hub.last_seen else None,
        "registered_at": hub.registered_at.isoformat() if hub.registered_at else None,
    }


# =============================================================================
# Public Endpoints (no auth required)
# =============================================================================


@hublist_router.get("/", summary="Download the hub list (XML)")
async def get_hublist(
    fmt: str = Query("xml", description="Response format: xml or json"),
    session: AsyncSession = Depends(_get_session),
) -> Response:
    """
    Public endpoint -- no authentication required.

    Returns the list of registered hubs in XML (default) or JSON format.
    Stale hubs (not pinged within the timeout) are excluded.
    """
    cutoff_dt = _utcnow() - timedelta(seconds=STALE_HUB_TIMEOUT)

    query = select(HubListEntry).where(HubListEntry.last_seen >= cutoff_dt)
    result = await session.execute(query)
    hubs = result.scalars().all()

    if fmt == "json":
        return Response(
            content=_hubs_to_json(hubs),
            media_type="application/json",
        )

    return Response(
        content=_hubs_to_xml(hubs),
        media_type=HUBLIST_XML_CONTENT_TYPE,
    )


@hublist_router.get("/stats", response_model=HubListStats, summary="Hublist stats")
async def hublist_stats(session: AsyncSession = Depends(_get_session)) -> HubListStats:
    """Public stats about the hublist directory."""
    cutoff_dt = _utcnow() - timedelta(seconds=STALE_HUB_TIMEOUT)

    query = select(HubListEntry).where(HubListEntry.last_seen >= cutoff_dt)
    result = await session.execute(query)
    hubs = result.scalars().all()

    return HubListStats(
        total_hubs=len(hubs),
        total_users=sum(h.users for h in hubs),
        total_share=sum(h.share for h in hubs),
    )


@hublist_router.post("/register", summary="Register / ping a hub")
async def register_hub(
    request: Request,
    session: AsyncSession = Depends(_get_session),
) -> dict:
    """
    Accept a hub registration (form-encoded or JSON).

    If a hub with the same ``address`` already exists, it is updated and
    its ``last_seen`` timestamp is refreshed.  Otherwise a new entry is
    created.

    No authentication -- any hub can register.  Stale entries are pruned
    on read.  Block rules are checked and will reject blocked hubs.
    """
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = {
            "name": form.get("Name", form.get("name", "")),
            "address": form.get("Host", form.get("address", "")),
            "description": form.get("Description", form.get("description", "")),
            "users": int(form.get("Users", form.get("users", 0))),
            "share": int(form.get("Share", form.get("share", 0))),
            "min_share": int(form.get("Minshare", form.get("min_share", 0))),
            "max_users": int(form.get("Maxusers", form.get("max_users", 0))),
            "country": form.get("Country", form.get("country", "")),
            "encoding": form.get("Encoding", form.get("encoding", "UTF-8")),
            "owner": form.get("Owner", form.get("owner", "")),
            "email": form.get("Email", form.get("email", "")),
            "website": form.get("Website", form.get("website", "")),
            "logo": form.get("Logo", form.get("logo", "")),
            "software": form.get("Software", form.get("software", "")),
        }

    address = str(data.get("address", "")).strip()
    name = str(data.get("name", "")).strip()
    if not address:
        raise HTTPException(status_code=400, detail="address is required")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    # ----- GeoIP enrichment for the registering hub -----
    request_ip = request.client.host if request.client else ""
    ip = request_ip or _extract_ip_from_address(address)
    hostname = _resolve_hostname(ip)
    geo = _lookup_geo_for_ip(ip) if ip else {}
    country = str(data.get("country", "")) or geo.get("country_code", "")
    city = geo.get("city", "")
    as_num = geo.get("as_number", "")
    as_name = geo.get("as_name", "")
    asn = f"{as_num} {as_name}".strip() if as_num else ""

    # ----- Block checking -----
    block = await check_hub_blocked(
        session,
        ip=ip,
        hostname=hostname,
        address=address,
        country=country,
        city=city,
        asn=asn,
    )
    if block:
        _emit_hublist_event("hublist_blocked", {
            "address": address,
            "name": name,
            "ip": ip,
            "block_type": block.block_type,
            "block_value": block.value,
            "block_reason": block.reason,
        })
        raise HTTPException(
            status_code=403,
            detail=f"Registration blocked: {block.block_type} rule on {block.value}",
        )

    # ----- Upsert by address -----
    existing = await session.execute(
        select(HubListEntry).where(HubListEntry.address == address)
    )
    hub = existing.scalar_one_or_none()

    now = _utcnow()
    is_new = hub is None
    if hub:
        hub.name = name
        hub.description = str(data.get("description", hub.description))
        hub.users = int(data.get("users", hub.users))
        hub.share = int(data.get("share", hub.share))
        hub.min_share = int(data.get("min_share", hub.min_share))
        hub.max_users = int(data.get("max_users", hub.max_users))
        hub.country = country or hub.country
        hub.encoding = str(data.get("encoding", hub.encoding))
        hub.owner = str(data.get("owner", hub.owner))
        hub.email = str(data.get("email", hub.email))
        hub.website = str(data.get("website", hub.website))
        hub.logo = str(data.get("logo", hub.logo))
        hub.software = str(data.get("software", hub.software))
        hub.ip = ip or hub.ip
        hub.hostname = hostname or hub.hostname
        hub.city = city or hub.city
        hub.asn = asn or hub.asn
        hub.last_seen = now
        hub.status = 1
        session.add(hub)
    else:
        hub = HubListEntry(
            name=name,
            address=address,
            description=str(data.get("description", "")),
            users=int(data.get("users", 0)),
            share=int(data.get("share", 0)),
            min_share=int(data.get("min_share", 0)),
            max_users=int(data.get("max_users", 0)),
            country=country,
            encoding=str(data.get("encoding", "UTF-8")),
            owner=str(data.get("owner", "")),
            email=str(data.get("email", "")),
            website=str(data.get("website", "")),
            logo=str(data.get("logo", "")),
            software=str(data.get("software", "")),
            ip=ip,
            hostname=hostname,
            city=city,
            asn=asn,
            last_seen=now,
            registered_at=now,
            status=1,
        )
        session.add(hub)

    await session.commit()
    await session.refresh(hub)

    # ----- WebSocket event -----
    event_type = "hublist_register" if is_new else "hublist_update"
    _emit_hublist_event(event_type, {"hub": _hub_to_dict(hub)})

    return {"status": "OK", "id": hub.id, "name": hub.name, "address": hub.address}


# =============================================================================
# Master-only Admin Endpoints
# =============================================================================


@hublist_router.get(
    "/all",
    response_model=list[HubListEntryRead],
    summary="All hubs (master only)",
)
async def get_all_hubs(
    _user: RequireMaster,
    session: AsyncSession = Depends(_get_session),
) -> list[HubListEntryRead]:
    """Return all hub entries including stale/offline ones (master-only)."""
    cutoff = _utcnow() - timedelta(seconds=STALE_HUB_TIMEOUT)
    result = await session.execute(select(HubListEntry))
    hubs = result.scalars().all()
    for h in hubs:
        if _as_naive(h.last_seen) < cutoff and h.status != 0:
            h.status = 0
            session.add(h)
    await session.commit()
    return [HubListEntryRead.model_validate(h) for h in hubs]


@hublist_router.get("/search", summary="Search hubs (master only)")
async def search_hubs(
    q: str = Query("", min_length=0, description="Search query"),
    _user: RequireMaster = None,
    session: AsyncSession = Depends(_get_session),
) -> list[dict]:
    """
    Search the hublist by name, address, owner, country, or city.
    Returns lightweight results for autocomplete.
    """
    result = await session.execute(select(HubListEntry))
    hubs = result.scalars().all()

    if not q:
        return [_hub_to_dict(h) for h in hubs]

    q_lower = q.lower()
    matches = []
    for h in hubs:
        searchable = " ".join([
            h.name, h.address, h.owner, h.description,
            h.country, h.city, h.hostname, h.software,
            h.asn, h.ip, h.email,
        ]).lower()
        if q_lower in searchable:
            matches.append(_hub_to_dict(h))
    return matches


@hublist_router.delete("/{hub_id}", summary="Remove a hub entry (master only)")
async def delete_hub_entry(
    hub_id: int,
    _user: RequireMaster,
    session: AsyncSession = Depends(_get_session),
) -> dict:
    """Remove a hub entry from the hublist (master-only)."""
    hub = await session.get(HubListEntry, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found")
    hub_data = _hub_to_dict(hub)
    await session.delete(hub)
    await session.commit()
    _emit_hublist_event("hublist_removed", {"hub": hub_data})
    return {"status": "OK", "deleted": hub_id}


# ------------- Block Rule CRUD (master-only) ---------------


@hublist_router.get(
    "/blocks",
    response_model=list[HubListBlockRead],
    summary="List block rules (master only)",
)
async def list_blocks(
    _user: RequireMaster,
    session: AsyncSession = Depends(_get_session),
) -> list[HubListBlockRead]:
    """Return all hublist block rules."""
    result = await session.execute(select(HubListBlock))
    blocks = result.scalars().all()
    return [HubListBlockRead.model_validate(b) for b in blocks]


@hublist_router.post(
    "/blocks",
    response_model=HubListBlockRead,
    status_code=201,
    summary="Create a block rule (master only)",
)
async def create_block(
    body: HubListBlockCreate,
    _user: RequireMaster,
    session: AsyncSession = Depends(_get_session),
) -> HubListBlockRead:
    """Create a new hublist block rule."""
    block = HubListBlock(
        block_type=body.block_type,
        value=body.value,
        reason=body.reason,
        created_by=_user.nick,
        expires_at=body.expires_at,
    )
    session.add(block)
    await session.commit()
    await session.refresh(block)
    _emit_hublist_event("hublist_block_added", {
        "block": {
            "id": block.id,
            "block_type": block.block_type,
            "value": block.value,
            "reason": block.reason,
            "created_by": block.created_by,
        },
    })
    return HubListBlockRead.model_validate(block)


@hublist_router.delete(
    "/blocks/{block_id}",
    summary="Remove a block rule (master only)",
)
async def delete_block(
    block_id: int,
    _user: RequireMaster,
    session: AsyncSession = Depends(_get_session),
) -> dict:
    """Remove a hublist block rule."""
    block = await session.get(HubListBlock, block_id)
    if not block:
        raise HTTPException(status_code=404, detail="Block rule not found")
    await session.delete(block)
    await session.commit()
    _emit_hublist_event("hublist_block_removed", {"block_id": block_id})
    return {"status": "OK", "deleted": block_id}


# =============================================================================
# Stale Entry Pruning (background task)
# =============================================================================


async def prune_stale_hubs(timeout: int = STALE_HUB_TIMEOUT) -> int:
    """
    Mark hub entries as offline if they haven't pinged within ``timeout``
    seconds. Also emit WebSocket events for newly-offline hubs.

    Returns the number of entries marked offline.
    """
    from verlihub.models.database import get_database
    db = get_database()
    cutoff = _utcnow() - timedelta(seconds=timeout)
    async with db._session_factory() as session:
        result = await session.execute(
            select(HubListEntry).where(
                HubListEntry.last_seen < cutoff,
                HubListEntry.status != 0,
            )
        )
        stale = result.scalars().all()
        for hub in stale:
            hub.status = 0
            session.add(hub)
            _emit_hublist_event("hublist_offline", {"hub": _hub_to_dict(hub)})
        await session.commit()
        return len(stale)


# =============================================================================
# XML / JSON serialization
# =============================================================================


def _hubs_to_xml(hubs: list[HubListEntry]) -> str:
    """Serialize hub list to NMDC-standard XML format."""
    root = ET.Element("Hubs")
    for hub in hubs:
        ET.SubElement(root, "Hub", {
            "Name": hub.name,
            "Address": hub.address,
            "Description": hub.description,
            "Users": str(hub.users),
            "Share": str(hub.share),
            "Minshare": str(hub.min_share),
            "Maxusers": str(hub.max_users),
            "Country": hub.country,
            "Encoding": hub.encoding,
            "Owner": hub.owner,
            "Website": hub.website,
            "Status": str(hub.status),
            "Software": hub.software,
        })
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def _hubs_to_json(hubs: list[HubListEntry]) -> str:
    """Serialize hub list to JSON."""
    import json
    return json.dumps([_hub_to_dict(h) for h in hubs], indent=2)


# =============================================================================
# Helper: build hub info dict for the registration client
# =============================================================================


def build_hub_info(ctx=None) -> dict:
    """
    Build the info dict that the registration client sends to hublist servers.

    ``ctx`` is an optional hub context (SWIG HubContext).  When not available,
    falls back to the Python config singleton.
    """
    info: dict = {
        "software": "Verlihub-py",
        "status": 1,
    }

    if ctx:
        try:
            _g = lambda k, d="": ctx.get_config("config", k, d)
            info["name"] = _g("hub_name", "Verlihub Hub")
            host = _g("hub_host", "")
            port = _g("listen_port", "411")
            info["address"] = host or f"dchub://0.0.0.0:{port}"
            info["description"] = _g("hub_desc", "")
            info["users"] = ctx.user_count if hasattr(ctx, "user_count") else 0
            info["share"] = ctx.total_share if hasattr(ctx, "total_share") else 0
            info["min_share"] = int(_g("min_share", "0"))
            info["max_users"] = int(_g("max_users", "1000"))
            info["encoding"] = _g("hub_encoding", "UTF-8")
            info["owner"] = _g("hub_owner", "")
            return info
        except Exception:
            pass

    # Fallback: read from Python config
    try:
        from verlihub.config import get_config_optional
        cfg = get_config_optional()
        if cfg:
            info["name"] = cfg.hub.name
            info["address"] = cfg.hub.host or f"dchub://0.0.0.0:{cfg.hub.port}"
            info["description"] = cfg.hub.description
            info["max_users"] = cfg.hub.max_users
            info["encoding"] = cfg.hub.encoding
            info["owner"] = cfg.hub.owner
    except Exception:
        pass

    return info
