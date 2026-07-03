"""
Invite code management API endpoints.

Admins allocate invite codes to users. Users can then share those codes
so that new users can register at a class level up to the invite's max_class.
"""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from verlihub.api.auth import (
    Permission,
    RequireAdmin,
    TokenData,
    get_current_user,
    require_permission,
)
from verlihub.models import InviteCode, InviteCodeCreate, InviteCodeRead, UserClass
from verlihub.models.database import get_database

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class InviteAllocation(BaseModel):
    """Response after allocating invite codes."""
    allocated: int
    codes: list[str]
    nick: str
    max_class: int


class InviteSummary(BaseModel):
    """Summary of a user's invite codes."""
    total: int
    used: int
    available: int
    codes: list[InviteCodeRead]


# =============================================================================
# Dependencies
# =============================================================================


async def get_session():
    """Get database session."""
    db = get_database()
    async with db._session_factory() as session:
        yield session


def _generate_invite_code() -> str:
    """Generate a unique invite code."""
    return secrets.token_urlsafe(16)


# =============================================================================
# Admin Endpoints
# =============================================================================


@router.post("/allocate", response_model=InviteAllocation)
async def allocate_invite_codes(
    request: InviteCodeCreate,
    admin: RequireAdmin = None,
    session: AsyncSession = Depends(get_session),
) -> InviteAllocation:
    """
    Allocate invite codes to a user. Requires ADMIN (5) permission.
    
    The admin specifies:
    - nick: The user to allocate codes to
    - count: How many codes to create (1-100)
    - max_class: Maximum user class the invite can grant
    
    The max_class cannot exceed the admin's own class, and cannot
    exceed the target user's class.
    """
    # Validate max_class doesn't exceed admin's class
    if request.max_class > admin.user_class:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot allocate invites for class {request.max_class} "
                   f"(your class is {admin.user_class})",
        )
    
    # Validate max_class is a valid user class
    valid_classes = {c.value for c in UserClass if c.value >= 0}
    if request.max_class not in valid_classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid user class: {request.max_class}. "
                   f"Valid classes: {sorted(valid_classes)}",
        )
    
    # Create the invite codes
    codes = []
    for _ in range(request.count):
        code = _generate_invite_code()
        invite = InviteCode(
            code=code,
            created_by=request.nick,
            max_class=request.max_class,
        )
        session.add(invite)
        codes.append(code)
    
    await session.commit()
    
    return InviteAllocation(
        allocated=len(codes),
        codes=codes,
        nick=request.nick,
        max_class=request.max_class,
    )


@router.get("/admin", response_model=list[InviteCodeRead])
async def list_all_invites(
    _admin: RequireAdmin = None,
    nick: Optional[str] = Query(None, description="Filter by owner nick"),
    used: Optional[bool] = Query(None, description="Filter by used status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[InviteCodeRead]:
    """
    List all invite codes (admin view). Requires ADMIN (5) permission.
    
    Supports filtering by owner nick and used status.
    """
    query = select(InviteCode)
    
    if nick is not None:
        query = query.where(InviteCode.created_by == nick)
    if used is not None:
        query = query.where(InviteCode.used == used)
    
    query = query.offset(skip).limit(limit)
    result = await session.execute(query)
    invites = result.scalars().all()
    
    return [InviteCodeRead.model_validate(i) for i in invites]


@router.delete("/{code}")
async def revoke_invite_code(
    code: str,
    _admin: RequireAdmin = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Revoke (delete) an invite code. Requires ADMIN (5) permission.
    
    Only unused codes can be revoked.
    """
    query = select(InviteCode).where(InviteCode.code == code)
    result = await session.execute(query)
    invite = result.scalar_one_or_none()
    
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite code not found")
    
    if invite.used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot revoke an already-used invite code",
        )
    
    await session.delete(invite)
    await session.commit()
    
    return {"success": True, "message": f"Revoked invite code"}


# =============================================================================
# User Endpoints (view own invite codes)
# =============================================================================


@router.get("/mine", response_model=InviteSummary)
async def get_my_invites(
    user: TokenData = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InviteSummary:
    """
    Get the current user's invite codes.
    
    Returns all codes allocated to the authenticated user, along with
    usage statistics.
    """
    query = select(InviteCode).where(InviteCode.created_by == user.nick)
    result = await session.execute(query)
    invites = result.scalars().all()
    
    total = len(invites)
    used = sum(1 for i in invites if i.used)
    
    return InviteSummary(
        total=total,
        used=used,
        available=total - used,
        codes=[InviteCodeRead.model_validate(i) for i in invites],
    )
