"""
User management API endpoints.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.api.auth import (
    Permission,
    RequireAdmin,
    RequireOperator,
    TokenData,
    require_permission,
)
from verlihub.models import RegUser, RegUserCreate, RegUserRead, RegUserUpdate, UserClass
from verlihub.models.database import get_database

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class OnlineUser(BaseModel):
    """Online user information."""
    nick: str
    ip: str = ""
    share: int = 0
    user_class: int = UserClass.GUEST
    login_time: Optional[str] = None


class UserList(BaseModel):
    """List of online users."""
    count: int
    users: list[OnlineUser]


class KickRequest(BaseModel):
    """Request to kick a user."""
    nick: str
    reason: str = ""
    op_nick: str = "API"


class MessageRequest(BaseModel):
    """Request to send a message to a user."""
    nick: str
    message: str


# =============================================================================
# Dependencies
# =============================================================================


def get_hub_context():
    """Get the HubContext."""
    from verlihub.api.deps import get_hub_context as _get_ctx
    ctx = _get_ctx()
    if ctx is None:
        raise HTTPException(status_code=503, detail="Hub not initialized")
    return ctx


async def get_session():
    """Get database session."""
    db = get_database()
    async with db._session_factory() as session:
        yield session


# =============================================================================
# Online User Endpoints
# =============================================================================


@router.get("/online", response_model=UserList)
async def get_online_users(ctx=Depends(get_hub_context)) -> UserList:
    """Get list of currently online users with full info."""
    user_dicts = ctx.get_user_list()
    users = [
        OnlineUser(
            nick=u.get("nick", ""),
            ip=u.get("ip", ""),
            share=u.get("share", 0),
            user_class=u.get("user_class", 0),
        )
        for u in user_dicts
    ]
    return UserList(count=len(users), users=users)


@router.get("/online/{nick}")
async def get_online_user(nick: str, ctx=Depends(get_hub_context)) -> OnlineUser:
    """Get information about a specific online user."""
    for u in ctx.get_user_list():
        if u.get("nick") == nick:
            return OnlineUser(
                nick=u.get("nick", nick),
                ip=u.get("ip", ""),
                share=u.get("share", 0),
                user_class=u.get("user_class", 0),
            )
    raise HTTPException(status_code=404, detail="User not online")


@router.post("/kick")
async def kick_user(
    request: KickRequest,
    ctx=Depends(get_hub_context),
    user: TokenData = Depends(require_permission(Permission.OPERATOR)),
) -> dict:
    """Kick a user from the hub. Requires OPERATOR (3) permission."""
    # Use the authenticated user's nick as the operator
    op_nick = user.nick
    success = ctx.kick_user(op_nick, request.nick, request.reason)
    if not success:
        raise HTTPException(status_code=404, detail="User not found or kick failed")
    
    return {"success": True, "message": f"Kicked {request.nick}"}


@router.post("/message")
async def send_message(
    request: MessageRequest,
    ctx=Depends(get_hub_context),
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
) -> dict:
    """Send a private message to a user. Requires OPERATOR (3) permission."""
    success = ctx.send_to_user(request.nick, request.message)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"success": True}


# =============================================================================
# Registered User Endpoints (Database)
# =============================================================================


@router.get("/registered", response_model=list[RegUserRead])
async def list_registered_users(
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    user_class: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
) -> list[RegUserRead]:
    """List registered users from database. Requires OPERATOR (3) permission."""
    query = select(RegUser)
    
    if user_class is not None:
        query = query.where(RegUser.user_class == user_class)
    
    query = query.offset(skip).limit(limit)
    result = await session.execute(query)
    users = result.scalars().all()
    
    return [RegUserRead.model_validate(u) for u in users]


@router.get("/registered/{nick}", response_model=RegUserRead)
async def get_registered_user(
    nick: str,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> RegUserRead:
    """Get a specific registered user. Requires OPERATOR (3) permission."""
    query = select(RegUser).where(RegUser.nick == nick)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    
    return RegUserRead.model_validate(user)


@router.post("/registered", response_model=RegUserRead)
async def create_registered_user(
    user: RegUserCreate,
    _auth: RequireAdmin = None,
    session: AsyncSession = Depends(get_session),
) -> RegUserRead:
    """Register a new user. Requires ADMIN (5) permission."""
    # Check if user already exists
    query = select(RegUser).where(RegUser.nick == user.nick)
    result = await session.execute(query)
    existing = result.scalar_one_or_none()
    
    if existing is not None:
        raise HTTPException(status_code=409, detail="User already exists")
    
    db_user = RegUser.model_validate(user)
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    
    return RegUserRead.model_validate(db_user)


@router.patch("/registered/{nick}", response_model=RegUserRead)
async def update_registered_user(
    nick: str,
    user_update: RegUserUpdate,
    _user: RequireAdmin = None,
    session: AsyncSession = Depends(get_session),
) -> RegUserRead:
    """Update a registered user. Requires ADMIN (5) permission."""
    query = select(RegUser).where(RegUser.nick == nick)
    result = await session.execute(query)
    db_user = result.scalar_one_or_none()
    
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    
    update_data = user_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_user, key, value)
    
    await session.commit()
    await session.refresh(db_user)
    
    return RegUserRead.model_validate(db_user)


@router.delete("/registered/{nick}")
async def delete_registered_user(
    nick: str,
    _user: RequireAdmin = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a registered user. Requires ADMIN (5) permission."""
    query = select(RegUser).where(RegUser.nick == nick)
    result = await session.execute(query)
    db_user = result.scalar_one_or_none()
    
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    
    await session.delete(db_user)
    await session.commit()
    
    return {"success": True, "message": f"Deleted user {nick}"}
