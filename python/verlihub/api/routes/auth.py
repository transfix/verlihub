"""
Authentication API endpoints.
"""
from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.api.auth import (
    CurrentUser,
    LoginRequest,
    Permission,
    Token,
    TokenData,
    authenticate_user,
    create_access_token,
    get_current_user,
    get_db_session,
    hash_password,
)
from verlihub.models import (
    InviteCode,
    RegUser,
    RegisterRequest,
    UserClass,
    utc_now,
)

router = APIRouter()


def _reg_enabled() -> bool:
    from verlihub.config import get_config_optional
    cfg = get_config_optional()
    return cfg.api.registration_enabled if cfg else True


def _reg_require_invite() -> bool:
    from verlihub.config import get_config_optional
    cfg = get_config_optional()
    if cfg:
        return cfg.api.registration_require_invite
    # Fallback: check hub config store
    try:
        from verlihub.api.deps import get_hub_context
        ctx = get_hub_context()
        if ctx:
            return ctx.get_config("config", "registration_require_invite", "0") == "1"
    except Exception:
        pass
    return False


def _reg_default_class() -> int:
    from verlihub.config import get_config_optional
    cfg = get_config_optional()
    return cfg.api.registration_default_class if cfg else UserClass.REGISTERED


def _reg_require_email() -> bool:
    from verlihub.config import get_config_optional
    cfg = get_config_optional()
    return cfg.api.registration_require_email if cfg else True


def _reg_check_deliverability() -> bool:
    from verlihub.config import get_config_optional
    cfg = get_config_optional()
    return cfg.api.registration_check_email_deliverability if cfg else False


def _reg_block_disposable() -> bool:
    from verlihub.config import get_config_optional
    cfg = get_config_optional()
    return cfg.api.registration_block_disposable_emails if cfg else True


def _is_nick_online(nick: str) -> bool:
    """Check if a nick is currently connected to the hub."""
    try:
        from verlihub.api.deps import get_hub_context
        ctx = get_hub_context()
        if ctx:
            for u in ctx.get_user_list():
                if u.get("nick") == nick:
                    return True
    except Exception:
        pass
    return False


@router.post("/register", response_model=Token)
async def register(
    request: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Token:
    """
    Public self-registration endpoint.
    
    Creates a new user account. If invite codes are required
    (VH_REGISTRATION_REQUIRE_INVITE=true), a valid invite code must be provided.
    When an invite code is used, the new user's class is set to the invite's
    max_class (capped at the inviter's class). Otherwise the default class
    (VH_REGISTRATION_DEFAULT_CLASS, default REGISTERED=1) is used.
    
    Returns a JWT token so the user is immediately logged in.
    """
    if not _reg_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled",
        )
    
    # Validate nick format
    nick = request.nick.strip()
    if not nick or len(nick) < 2 or len(nick) > 64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nick must be between 2 and 64 characters",
        )
    if not re.match(r'^[a-zA-Z0-9_\-\[\]{}|`^]+$', nick):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nick contains invalid characters. Use letters, numbers, _ - [ ] { } | ` ^",
        )
    
    # Validate password
    if not request.password or len(request.password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 4 characters",
        )
    
    # Check if nick already exists
    query = select(RegUser).where(RegUser.nick == nick)
    result = await session.execute(query)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nick already registered",
        )
    
    # Check if nick is currently online (live user)
    if _is_nick_online(nick):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This nickname is currently in use by a connected user. "
                   "Please choose a different nickname or disconnect first.",
        )
    
    # Validate email
    email = (request.email or "").strip().lower()
    if _reg_require_email():
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email address is required",
            )
        from verlihub.email_validation import validate_email
        ok, err = await validate_email(
            email, check_deliverability=_reg_check_deliverability(),
            block_disposable=_reg_block_disposable(),
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err,
            )
    elif email:
        # Email provided but not required — still validate format
        from verlihub.email_validation import validate_email
        ok, err = await validate_email(
            email, check_deliverability=False,
            block_disposable=_reg_block_disposable(),
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err,
            )
    
    # Handle invite code
    user_class = _reg_default_class()
    invite = None
    
    if _reg_require_invite() and not request.invite_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An invite code is required for registration",
        )
    
    if request.invite_code:
        query = select(InviteCode).where(InviteCode.code == request.invite_code)
        result = await session.execute(query)
        invite = result.scalar_one_or_none()
        
        if invite is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid invite code",
            )
        if invite.used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invite code has already been used",
            )
        if invite.expires_at:
            # Normalise to naive UTC for comparison (SQLite returns naive datetimes)
            exp = invite.expires_at.replace(tzinfo=None) if invite.expires_at.tzinfo else invite.expires_at
            if exp < datetime.utcnow():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invite code has expired",
                )
        
        # Use the invite's max_class (never exceed what the invite allows)
        user_class = invite.max_class
    
    # Create the user
    new_user = RegUser(
        nick=nick,
        login_pwd=hash_password(request.password),
        email=email,
        user_class=user_class,
        authorised=True,
        reg_op="self-registration",
    )
    session.add(new_user)
    
    # Mark invite as used
    if invite:
        invite.used = True
        invite.used_by = nick
        invite.used_at = utc_now()
    
    await session.commit()
    
    # Return token so user is logged in immediately
    return create_access_token(nick, user_class)


@router.post("/login", response_model=Token)
async def login(
    request: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Token:
    """
    Authenticate user and return JWT token.
    
    The token should be included in subsequent requests as:
    `Authorization: Bearer <token>`
    """
    user = await authenticate_user(request.nick, request.password, session)
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Update last login time
    from datetime import datetime
    user.login_last = datetime.utcnow()
    user.login_count += 1
    await session.commit()
    
    return create_access_token(user.nick, user.user_class)


@router.get("/me", response_model=CurrentUser)
async def get_current_user_info(
    current_user: TokenData = Depends(get_current_user),
) -> CurrentUser:
    """Get information about the currently authenticated user."""
    # Map user class to permission names
    permissions = []
    for perm in Permission:
        if current_user.user_class >= perm.value:
            permissions.append(perm.name.lower())
    
    return CurrentUser(
        nick=current_user.nick,
        user_class=current_user.user_class,
        permissions=permissions,
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(
    current_user: TokenData = Depends(get_current_user),
) -> Token:
    """
    Refresh the current token.
    
    Returns a new token with extended expiry.
    """
    return create_access_token(current_user.nick, current_user.user_class)


@router.post("/logout")
async def logout() -> dict:
    """
    Logout the current user.
    
    Note: JWT tokens are stateless, so this is a no-op on the server.
    The client should discard the token.
    """
    return {"success": True, "message": "Logged out (discard token on client)"}
