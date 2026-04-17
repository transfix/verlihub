"""
Ban service with CIDR/subnet support and ban cache synchronization.

Provides business logic for ban management including:
- CIDR notation parsing and IP range calculation
- IP-in-range matching for subnet bans
- Ban cache synchronization with the C++ core
- Ban creation with automatic range expansion
"""
from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.models import Ban, BanType

if TYPE_CHECKING:
    from verlihub.verlihub_core import HubContext

_log = logging.getLogger(__name__)


def cidr_to_range(cidr: str) -> tuple[str, str]:
    """
    Convert a CIDR notation string to (min_ip, max_ip) range.

    Args:
        cidr: CIDR notation (e.g. "192.168.1.0/24")

    Returns:
        Tuple of (network_address, broadcast_address) as strings

    Raises:
        ValueError: If the CIDR notation is invalid
    """
    network = ipaddress.ip_network(cidr, strict=False)
    return str(network.network_address), str(network.broadcast_address)


def ip_in_range(ip: str, range_min: str, range_max: str) -> bool:
    """
    Check if an IP address falls within a range.

    Args:
        ip: IP address to check
        range_min: Start of range (inclusive)
        range_max: End of range (inclusive)

    Returns:
        True if ip is within [range_min, range_max]
    """
    try:
        addr = ipaddress.ip_address(ip)
        return ipaddress.ip_address(range_min) <= addr <= ipaddress.ip_address(range_max)
    except ValueError:
        return False


def ip_in_cidr(ip: str, cidr: str) -> bool:
    """
    Check if an IP address is within a CIDR network.

    Args:
        ip: IP address to check
        cidr: CIDR notation (e.g. "192.168.1.0/24")

    Returns:
        True if ip is within the network
    """
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


async def is_ip_banned(session: AsyncSession, ip: str) -> Optional[Ban]:
    """
    Check if an IP is banned (exact match or range/CIDR match).

    Checks exact IP bans first (fast), then range bans.

    Args:
        session: Database session
        ip: IP address to check

    Returns:
        The matching Ban object, or None
    """
    now = datetime.now(timezone.utc)

    # 1. Exact IP match
    query = select(Ban).where(
        Ban.ip == ip,
        Ban.ban_type.op("&")(BanType.IP) > 0,
        (Ban.date_limit.is_(None)) | (Ban.date_limit > now),
    )
    result = await session.execute(query)
    ban = result.scalar_one_or_none()
    if ban is not None:
        ban.last_hit = now
        session.add(ban)
        return ban

    # 2. Range/CIDR bans
    query = select(Ban).where(
        Ban.ban_type.op("&")(BanType.RANGE) > 0,
        (Ban.date_limit.is_(None)) | (Ban.date_limit > now),
    )
    result = await session.execute(query)
    range_bans = result.scalars().all()

    for rb in range_bans:
        matched = False
        if rb.cidr and ip_in_cidr(ip, rb.cidr):
            matched = True
        elif rb.ip_range_min and rb.ip_range_max:
            matched = ip_in_range(ip, rb.ip_range_min, rb.ip_range_max)

        if matched:
            rb.last_hit = now
            session.add(rb)
            return rb

    return None


async def create_ban(
    session: AsyncSession,
    *,
    ip: str = "",
    nick: str = "",
    cidr: str = "",
    ban_type: int = BanType.IP,
    reason: str = "",
    nick_op: str = "",
    duration_hours: Optional[int] = None,
    hub_ctx: Optional["HubContext"] = None,
) -> Ban:
    """
    Create a ban with optional CIDR expansion and cache sync.

    Args:
        session: Database session
        ip: IP address to ban (for exact IP bans)
        nick: Nickname to ban
        cidr: CIDR notation for range bans (e.g. "10.0.0.0/8")
        ban_type: Ban type flags
        reason: Ban reason
        nick_op: Operator who created the ban
        duration_hours: Hours until expiry (None = permanent)
        hub_ctx: HubContext for ban cache sync (optional)

    Returns:
        The created Ban object
    """
    now = datetime.now(timezone.utc)
    date_limit = None
    if duration_hours is not None:
        from datetime import timedelta
        date_limit = now + timedelta(hours=duration_hours)

    ip_range_min = ""
    ip_range_max = ""

    # Auto-expand CIDR to range
    if cidr:
        ban_type |= BanType.RANGE
        ip_range_min, ip_range_max = cidr_to_range(cidr)
        if not ip:
            ip = ip_range_min  # Store network address as primary IP

    ban = Ban(
        ip=ip,
        nick=nick,
        ban_type=ban_type,
        reason=reason,
        nick_op=nick_op,
        date_start=now,
        date_limit=date_limit,
        cidr=cidr,
        ip_range_min=ip_range_min,
        ip_range_max=ip_range_max,
    )

    session.add(ban)
    await session.commit()
    await session.refresh(ban)

    # Sync to C++ ban cache
    if hub_ctx is not None:
        if ip:
            hub_ctx.AddBanCacheIP(ip)
        if nick:
            hub_ctx.AddBanCacheNick(nick)

    return ban


async def remove_ban(
    session: AsyncSession,
    ban: Ban,
    hub_ctx: Optional["HubContext"] = None,
) -> None:
    """
    Remove a ban and sync the cache.

    Args:
        session: Database session
        ban: Ban to remove
        hub_ctx: HubContext for ban cache sync (optional)
    """
    ip = ban.ip
    nick = ban.nick

    await session.delete(ban)
    await session.commit()

    if hub_ctx is not None:
        if ip:
            hub_ctx.RemoveBanCacheIP(ip)
        if nick:
            hub_ctx.RemoveBanCacheNick(nick)


async def sync_ban_cache(
    session: AsyncSession,
    hub_ctx: "HubContext",
) -> int:
    """
    Load all active bans into the C++ ban cache.

    Should be called at hub startup and periodically.

    Args:
        session: Database session
        hub_ctx: HubContext to sync to

    Returns:
        Number of bans loaded
    """
    now = datetime.now(timezone.utc)
    query = select(Ban).where(
        (Ban.date_limit.is_(None)) | (Ban.date_limit > now),
    )
    result = await session.execute(query)
    bans = result.scalars().all()

    ips = []
    nicks = []
    for ban in bans:
        if ban.ip:
            ips.append(ban.ip)
        if ban.nick:
            nicks.append(ban.nick)

    hub_ctx.LoadBanCache(ips, nicks)
    _log.info("Synced %d bans to cache (%d IPs, %d nicks)", len(bans), len(ips), len(nicks))
    return len(bans)
