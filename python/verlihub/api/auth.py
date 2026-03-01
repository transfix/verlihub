"""
Authentication and authorization for Verlihub API.

Provides JWT-based authentication with permission levels matching
user classes from the C++ hub.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from verlihub.models import RegUser, UserClass


# =============================================================================
# Configuration
# =============================================================================

import logging
_logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

# --- Lazy JWT config resolved from the config singleton ---
# These are read on first use (not at import time) so the config
# singleton has a chance to be initialised first.

_jwt_secret_cached: Optional[str] = None


def _get_jwt_secret() -> str:
    """Return the JWT signing secret, resolving it lazily from config."""
    global _jwt_secret_cached
    if _jwt_secret_cached is not None:
        return _jwt_secret_cached

    from verlihub.config import get_config_optional

    cfg = get_config_optional()
    secret = cfg.api.secret if cfg else ""
    if not secret:
        _logger.warning(
            "No api.secret configured — using random key. "
            "Tokens will be invalidated on restart. "
            "Set api.secret in your YAML config for production."
        )
        secret = secrets.token_hex(32)
    _jwt_secret_cached = secret
    return secret


def _get_token_expire_minutes() -> int:
    """Return token expiry from config, default 60."""
    from verlihub.config import get_config_optional

    cfg = get_config_optional()
    return cfg.api.token_expire_minutes if cfg else 60

# Password hashing — uses bcrypt directly (passlib is unmaintained and
# incompatible with bcrypt ≥ 4.1).

# Security scheme
security = HTTPBearer(auto_error=False)


# =============================================================================
# Permission Levels (maps to user classes)
# =============================================================================


class Permission(IntEnum):
    """
    API permission levels based on user classes.
    
    These match the UserClass values from the hub:
    - GUEST (-1): Unauthenticated, read-only public info
    - PINGER (0): Basic read access
    - REGISTERED (1): Read own data
    - VIP (2): Extended read access
    - OPERATOR (3): User management, kicks
    - CHEEF (4): Ban management
    - ADMIN (5): Full read access, config changes
    - MASTER (10): Full control including shutdown
    - SUPERADMIN (11): API admin (token management)
    """
    PUBLIC = -1      # No auth required
    READ_ONLY = 0    # Basic read access
    USER = 1         # Authenticated user
    VIP = 2          # VIP features
    OPERATOR = 3     # User management
    CHEEF = 4        # Ban management
    ADMIN = 5        # Configuration
    MASTER = 10      # Full control
    SUPERADMIN = 11  # API administration


# =============================================================================
# Token Models
# =============================================================================


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class TokenData(BaseModel):
    """Data extracted from JWT token."""
    nick: str
    user_class: int
    exp: datetime


class LoginRequest(BaseModel):
    """Login request body."""
    nick: str
    password: str


class CurrentUser(BaseModel):
    """Current authenticated user."""
    nick: str
    user_class: int
    permissions: list[str]


# =============================================================================
# Password Utilities
# =============================================================================


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a bcrypt hash."""
    if not hashed_password:
        return not plain_password
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8") if isinstance(hashed_password, str) else hashed_password,
        )
    except (ValueError, TypeError):
        _logger.warning("Password hash format not recognized - rejecting authentication")
        return False


def hash_password(password: str) -> str:
    """Hash a password with bcrypt for storage."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# =============================================================================
# Token Creation and Validation
# =============================================================================


def create_access_token(nick: str, user_class: int) -> Token:
    """
    Create a JWT access token.
    
    Args:
        nick: User nickname
        user_class: User's permission class
        
    Returns:
        Token object with access_token, token_type, and expires_in
    """
    expires_delta = timedelta(minutes=_get_token_expire_minutes())
    expire = datetime.now(timezone.utc) + expires_delta
    
    to_encode = {
        "sub": nick,
        "class": user_class,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    
    encoded_jwt = jwt.encode(to_encode, _get_jwt_secret(), algorithm=ALGORITHM)
    
    return Token(
        access_token=encoded_jwt,
        expires_in=int(expires_delta.total_seconds()),
    )


def decode_token(token: str) -> Optional[TokenData]:
    """
    Decode and validate a JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        TokenData if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[ALGORITHM])
        nick = payload.get("sub")
        user_class = payload.get("class", UserClass.GUEST)
        exp = datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc)
        
        if nick is None:
            return None
            
        return TokenData(nick=nick, user_class=user_class, exp=exp)
    except JWTError:
        return None


# =============================================================================
# Authentication Dependencies
# =============================================================================


async def get_db_session():
    """Get database session for auth operations."""
    from verlihub.models.database import get_database
    db = get_database()
    async with db._session_factory() as session:
        yield session


def _extract_token(
    credentials: Optional[HTTPAuthorizationCredentials],
    request: Request,
) -> Optional[str]:
    """
    Extract a JWT token string from either the Authorization header
    (via HTTPBearer) or the ``access_token`` httponly cookie.

    The cookie is set by the dashboard login route as
    ``"Bearer <jwt>"`` — we strip the prefix before returning.
    """
    if credentials is not None:
        return credentials.credentials

    cookie_value = request.cookies.get("access_token")
    if cookie_value:
        # The cookie is stored as "Bearer <jwt>"
        if cookie_value.startswith("Bearer "):
            return cookie_value[7:]
        return cookie_value

    return None


async def get_current_user_optional(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    request: Request,
) -> Optional[TokenData]:
    """
    Get current user if authenticated, None otherwise.
    
    Use this for endpoints that work with or without authentication.
    """
    raw_token = _extract_token(credentials, request)
    if raw_token is None:
        return None
    
    token_data = decode_token(raw_token)
    return token_data


async def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    request: Request,
) -> TokenData:
    """
    Get current authenticated user.
    
    Raises 401 if not authenticated.
    """
    raw_token = _extract_token(credentials, request)
    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token_data = decode_token(raw_token)
    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return token_data


def require_permission(required_class: int):
    """
    Create a dependency that requires a minimum permission level.
    
    Usage:
        @router.get("/admin")
        async def admin_endpoint(user: TokenData = Depends(require_permission(Permission.ADMIN))):
            ...
    """
    async def permission_checker(
        current_user: Annotated[TokenData, Depends(get_current_user)],
    ) -> TokenData:
        if current_user.user_class < required_class:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied. Required class: {required_class}, your class: {current_user.user_class}",
            )
        return current_user
    
    return permission_checker


# Convenience dependency aliases
RequireUser = Annotated[TokenData, Depends(require_permission(Permission.USER))]
RequireOperator = Annotated[TokenData, Depends(require_permission(Permission.OPERATOR))]
RequireAdmin = Annotated[TokenData, Depends(require_permission(Permission.ADMIN))]
RequireMaster = Annotated[TokenData, Depends(require_permission(Permission.MASTER))]


# =============================================================================
# User Authentication
# =============================================================================


async def authenticate_user(
    nick: str,
    password: str,
    session: AsyncSession,
) -> Optional[RegUser]:
    """
    Authenticate a user against the database.
    
    Args:
        nick: User nickname
        password: Plain text password
        session: Database session
        
    Returns:
        RegUser if authenticated, None otherwise
    """
    query = select(RegUser).where(RegUser.nick == nick)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    
    if user is None:
        return None
    
    if not user.authorised:
        return None
    
    if not verify_password(password, user.login_pwd):
        return None
    
    return user
