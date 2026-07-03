"""
Redirect management API endpoints.

Provides CRUD for redirect rules that route users to alternative hubs.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from verlihub.api.auth import require_permission, Permission, TokenData
from verlihub.models import RedirectRead

router = APIRouter()


class RedirectCreateRequest(BaseModel):
    address: str
    flag: int = 0
    enable: bool = True


class RedirectUpdateRequest(BaseModel):
    address: Optional[str] = None
    flag: Optional[int] = None
    enable: Optional[bool] = None


class RedirectList(BaseModel):
    count: int
    redirects: list[RedirectRead]


async def get_session():
    from verlihub.models.database import get_database
    db = get_database()
    async with db._session_factory() as session:
        yield session


@router.get("/", response_model=RedirectList)
async def list_redirects(
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> RedirectList:
    """List all redirect rules."""
    from verlihub.redirect_service import get_all_redirects
    redirects = await get_all_redirects(session)
    page = redirects[skip : skip + limit]
    return RedirectList(
        count=len(page),
        redirects=[RedirectRead.model_validate(r) for r in page],
    )


@router.post("/", response_model=RedirectRead)
async def create_redirect_endpoint(
    request: RedirectCreateRequest,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> RedirectRead:
    """Create a new redirect rule."""
    if not request.address.strip():
        raise HTTPException(status_code=400, detail="Address is required")

    from verlihub.redirect_service import create_redirect
    redirect = await create_redirect(
        session,
        address=request.address,
        flag=request.flag,
        enable=request.enable,
    )
    return RedirectRead.model_validate(redirect)


@router.put("/{redirect_id}", response_model=RedirectRead)
async def update_redirect_endpoint(
    redirect_id: int,
    request: RedirectUpdateRequest,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> RedirectRead:
    """Update an existing redirect rule."""
    from verlihub.redirect_service import get_redirect_by_id, update_redirect
    redirect = await get_redirect_by_id(session, redirect_id)
    if redirect is None:
        raise HTTPException(status_code=404, detail="Redirect not found")

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    redirect = await update_redirect(session, redirect, **updates)
    return RedirectRead.model_validate(redirect)


@router.delete("/{redirect_id}")
async def delete_redirect_endpoint(
    redirect_id: int,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove a redirect rule."""
    from verlihub.redirect_service import get_redirect_by_id, remove_redirect
    redirect = await get_redirect_by_id(session, redirect_id)
    if redirect is None:
        raise HTTPException(status_code=404, detail="Redirect not found")

    await remove_redirect(session, redirect)
    return {"success": True, "message": f"Removed redirect {redirect_id}"}
