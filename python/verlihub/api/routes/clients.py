"""
Client detection management API endpoints.

Provides CRUD for DC client version rules (allow/ban by client tag).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from verlihub.api.auth import require_permission, Permission, TokenData
from verlihub.models import DCClientRead

router = APIRouter()


class ClientCreateRequest(BaseModel):
    name: str
    tag_id: str = ""
    min_version: float = 0.0
    max_version: float = 0.0
    ban: bool = False
    enable: bool = True


class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    tag_id: Optional[str] = None
    min_version: Optional[float] = None
    max_version: Optional[float] = None
    ban: Optional[bool] = None
    enable: Optional[bool] = None


class ClientList(BaseModel):
    count: int
    clients: list[DCClientRead]


async def get_session():
    from verlihub.models.database import get_database
    db = get_database()
    async with db._session_factory() as session:
        yield session


@router.get("/", response_model=ClientList)
async def list_clients(
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> ClientList:
    """List all client detection rules."""
    from verlihub.client_detection_service import get_all_clients
    clients = await get_all_clients(session)
    page = clients[skip : skip + limit]
    return ClientList(
        count=len(page),
        clients=[DCClientRead.model_validate(c) for c in page],
    )


@router.post("/", response_model=DCClientRead)
async def create_client_endpoint(
    request: ClientCreateRequest,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> DCClientRead:
    """Create a new client detection rule."""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Client name is required")

    from verlihub.client_detection_service import create_client_rule
    client = await create_client_rule(
        session,
        name=request.name,
        tag_id=request.tag_id,
        min_version=request.min_version,
        max_version=request.max_version,
        ban=request.ban,
        enable=request.enable,
    )
    return DCClientRead.model_validate(client)


@router.put("/{client_id}", response_model=DCClientRead)
async def update_client_endpoint(
    client_id: int,
    request: ClientUpdateRequest,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> DCClientRead:
    """Update an existing client detection rule."""
    from verlihub.client_detection_service import get_client_by_id, update_client_rule
    client = await get_client_by_id(session, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client rule not found")

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    client = await update_client_rule(session, client, **updates)
    return DCClientRead.model_validate(client)


@router.delete("/{client_id}")
async def delete_client_endpoint(
    client_id: int,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove a client detection rule."""
    from verlihub.client_detection_service import get_client_by_id, remove_client_rule
    client = await get_client_by_id(session, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client rule not found")

    await remove_client_rule(session, client)
    return {"success": True, "message": f"Removed client rule {client_id}"}
