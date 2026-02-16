"""
Ban management API endpoints.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.api.auth import require_permission, Permission, TokenData
from verlihub.models import Ban, BanCreate, BanRead, BanType

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================


class BanCreateRequest(BaseModel):
    """Request to create a ban."""
    ip: str = ""
    nick: str = ""
    ban_type: int = BanType.IP
    reason: str = ""
    duration_hours: Optional[int] = None  # None = permanent
    nick_op: str = "API"


class BanList(BaseModel):
    """List of bans."""
    count: int
    bans: list[BanRead]


# =============================================================================
# Dependencies
# =============================================================================


async def get_session():
    """Get database session."""
    from verlihub.models.database import get_database
    db = get_database()
    async with db._session_factory() as session:
        yield session


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/", response_model=BanList)
async def list_bans(
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    active_only: bool = True,
    ban_type: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
) -> BanList:
    """List bans from database."""
    query = select(Ban)
    
    if active_only:
        # Filter to active bans (no expiry or expiry in future)
        now = datetime.utcnow()
        query = query.where(
            (Ban.date_limit.is_(None)) | (Ban.date_limit > now)
        )
    
    if ban_type is not None:
        query = query.where(Ban.ban_type == ban_type)
    
    query = query.offset(skip).limit(limit)
    result = await session.execute(query)
    bans = result.scalars().all()
    
    return BanList(
        count=len(bans),
        bans=[BanRead.model_validate(b) for b in bans],
    )


@router.get("/{ban_id}", response_model=BanRead)
async def get_ban(
    ban_id: int,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> BanRead:
    """Get a specific ban by ID."""
    query = select(Ban).where(Ban.id == ban_id)
    result = await session.execute(query)
    ban = result.scalar_one_or_none()
    
    if ban is None:
        raise HTTPException(status_code=404, detail="Ban not found")
    
    return BanRead.model_validate(ban)


@router.get("/search/ip/{ip}", response_model=BanList)
async def search_ban_by_ip(
    ip: str,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> BanList:
    """Search for bans by IP address."""
    query = select(Ban).where(Ban.ip == ip)
    result = await session.execute(query)
    bans = result.scalars().all()
    
    return BanList(
        count=len(bans),
        bans=[BanRead.model_validate(b) for b in bans],
    )


@router.get("/search/nick/{nick}", response_model=BanList)
async def search_ban_by_nick(
    nick: str,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> BanList:
    """Search for bans by nickname."""
    query = select(Ban).where(Ban.nick == nick)
    result = await session.execute(query)
    bans = result.scalars().all()
    
    return BanList(
        count=len(bans),
        bans=[BanRead.model_validate(b) for b in bans],
    )


@router.post("/", response_model=BanRead)
async def create_ban(
    request: BanCreateRequest,
    _user: TokenData = Depends(require_permission(Permission.CHEEF)),
    session: AsyncSession = Depends(get_session),
) -> BanRead:
    """Create a new ban."""
    if not request.ip and not request.nick:
        raise HTTPException(
            status_code=400, 
            detail="Either IP or nick must be provided"
        )
    
    # Calculate expiry date if duration provided
    date_limit = None
    if request.duration_hours is not None:
        date_limit = datetime.utcnow() + timedelta(hours=request.duration_hours)
    
    ban = Ban(
        ip=request.ip,
        nick=request.nick,
        ban_type=request.ban_type,
        reason=request.reason,
        nick_op=request.nick_op,
        date_start=datetime.utcnow(),
        date_limit=date_limit,
    )
    
    session.add(ban)
    await session.commit()
    await session.refresh(ban)
    
    return BanRead.model_validate(ban)


@router.delete("/{ban_id}")
async def delete_ban(
    ban_id: int,
    _user: TokenData = Depends(require_permission(Permission.CHEEF)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a ban by ID (unban)."""
    query = select(Ban).where(Ban.id == ban_id)
    result = await session.execute(query)
    ban = result.scalar_one_or_none()
    
    if ban is None:
        raise HTTPException(status_code=404, detail="Ban not found")
    
    await session.delete(ban)
    await session.commit()
    
    return {"success": True, "message": f"Deleted ban {ban_id}"}


@router.delete("/ip/{ip}")
async def unban_ip(
    ip: str,
    _user: TokenData = Depends(require_permission(Permission.CHEEF)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove all bans for an IP address."""
    query = select(Ban).where(Ban.ip == ip)
    result = await session.execute(query)
    bans = result.scalars().all()
    
    if not bans:
        raise HTTPException(status_code=404, detail="No bans found for IP")
    
    for ban in bans:
        await session.delete(ban)
    
    await session.commit()
    
    return {"success": True, "count": len(bans), "message": f"Removed {len(bans)} ban(s) for {ip}"}


@router.delete("/nick/{nick}")
async def unban_nick(
    nick: str,
    _user: TokenData = Depends(require_permission(Permission.CHEEF)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove all bans for a nickname."""
    query = select(Ban).where(Ban.nick == nick)
    result = await session.execute(query)
    bans = result.scalars().all()
    
    if not bans:
        raise HTTPException(status_code=404, detail="No bans found for nick")
    
    for ban in bans:
        await session.delete(ban)
    
    await session.commit()
    
    return {"success": True, "count": len(bans), "message": f"Removed {len(bans)} ban(s) for {nick}"}
