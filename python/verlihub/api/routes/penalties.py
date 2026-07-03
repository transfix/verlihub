"""
Penalty management API endpoints.

Provides CRUD for temporary per-user restrictions (gag, PM ban, etc.).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.api.auth import require_permission, Permission, TokenData
from verlihub.models import Penalty, PenaltyRead, PenaltyType

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================


class PenaltyCreateRequest(BaseModel):
    """Request to create a penalty."""
    nick: str
    penalty_type: int = PenaltyType.GAG
    reason: str = ""
    duration_minutes: Optional[int] = None  # None = permanent
    op_nick: str = "API"


class PenaltyList(BaseModel):
    """List of penalties."""
    count: int
    penalties: list[PenaltyRead]


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


@router.get("/", response_model=PenaltyList)
async def list_penalties(
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    nick: Optional[str] = Query(None, description="Filter by nick"),
    active_only: bool = True,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> PenaltyList:
    """List penalties."""
    from verlihub.penalty_service import get_active_penalties

    if active_only:
        penalties = await get_active_penalties(session, nick=nick)
    else:
        query = select(Penalty)
        if nick:
            query = query.where(Penalty.nick == nick)
        query = query.offset(skip).limit(limit)
        result = await session.execute(query)
        penalties = list(result.scalars().all())

    return PenaltyList(
        count=len(penalties),
        penalties=[PenaltyRead.model_validate(p) for p in penalties],
    )


@router.post("/", response_model=PenaltyRead)
async def create_penalty(
    request: PenaltyCreateRequest,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> PenaltyRead:
    """Create a new penalty (temporary restriction)."""
    if not request.nick:
        raise HTTPException(status_code=400, detail="Nick is required")

    from verlihub.penalty_service import add_penalty

    penalty = await add_penalty(
        session,
        nick=request.nick,
        penalty_type=request.penalty_type,
        reason=request.reason,
        op_nick=request.op_nick,
        duration_minutes=request.duration_minutes,
    )

    return PenaltyRead.model_validate(penalty)


@router.delete("/{penalty_id}")
async def delete_penalty(
    penalty_id: int,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove a specific penalty."""
    query = select(Penalty).where(Penalty.id == penalty_id)
    result = await session.execute(query)
    penalty = result.scalar_one_or_none()

    if penalty is None:
        raise HTTPException(status_code=404, detail="Penalty not found")

    from verlihub.penalty_service import remove_penalty
    await remove_penalty(session, penalty)

    return {"success": True, "message": f"Removed penalty {penalty_id}"}


@router.delete("/nick/{nick}")
async def remove_all_penalties_for_nick(
    nick: str,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove all penalties for a user."""
    from verlihub.penalty_service import remove_penalties_for_nick

    count = await remove_penalties_for_nick(session, nick)
    if count == 0:
        raise HTTPException(status_code=404, detail="No penalties found for nick")

    return {"success": True, "count": count, "message": f"Removed {count} penalty(ies) for {nick}"}
