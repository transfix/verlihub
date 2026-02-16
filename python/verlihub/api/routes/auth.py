"""
Authentication API endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

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
)

router = APIRouter()


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
