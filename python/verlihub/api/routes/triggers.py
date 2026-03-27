"""
Trigger management API endpoints.

Provides CRUD for custom auto-response commands.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from verlihub.api.auth import require_permission, Permission, TokenData
from verlihub.models import TriggerRead, TriggerFlags

router = APIRouter()


class TriggerCreateRequest(BaseModel):
    command: str
    response: str
    send_as: str = ""
    min_class: int = 1
    max_class: int = 10
    flags: int = TriggerFlags.SEND_MAIN
    seconds: int = 0


class TriggerUpdateRequest(BaseModel):
    command: Optional[str] = None
    response: Optional[str] = None
    send_as: Optional[str] = None
    min_class: Optional[int] = None
    max_class: Optional[int] = None
    flags: Optional[int] = None
    seconds: Optional[int] = None


class TriggerList(BaseModel):
    count: int
    triggers: list[TriggerRead]


async def get_session():
    from verlihub.models.database import get_database
    db = get_database()
    async with db._session_factory() as session:
        yield session


@router.get("/", response_model=TriggerList)
async def list_triggers(
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> TriggerList:
    """List all triggers."""
    from verlihub.trigger_service import get_all_triggers
    triggers = await get_all_triggers(session)
    page = triggers[skip : skip + limit]
    return TriggerList(
        count=len(page),
        triggers=[TriggerRead.model_validate(t) for t in page],
    )


@router.post("/", response_model=TriggerRead)
async def create_trigger_endpoint(
    request: TriggerCreateRequest,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> TriggerRead:
    """Create a new trigger."""
    if not request.command.strip():
        raise HTTPException(status_code=400, detail="Command is required")

    from verlihub.trigger_service import create_trigger
    trigger = await create_trigger(
        session,
        command=request.command,
        response=request.response,
        send_as=request.send_as,
        min_class=request.min_class,
        max_class=request.max_class,
        flags=request.flags,
        seconds=request.seconds,
    )
    return TriggerRead.model_validate(trigger)


@router.put("/{trigger_id}", response_model=TriggerRead)
async def update_trigger_endpoint(
    trigger_id: int,
    request: TriggerUpdateRequest,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> TriggerRead:
    """Update an existing trigger."""
    from verlihub.trigger_service import get_trigger_by_id, update_trigger
    trigger = await get_trigger_by_id(session, trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if "response" in updates:
        updates["def_"] = updates.pop("response")
    trigger = await update_trigger(session, trigger, **updates)
    return TriggerRead.model_validate(trigger)


@router.delete("/{trigger_id}")
async def delete_trigger_endpoint(
    trigger_id: int,
    _user: TokenData = Depends(require_permission(Permission.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove a trigger."""
    from verlihub.trigger_service import get_trigger_by_id, remove_trigger
    trigger = await get_trigger_by_id(session, trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    await remove_trigger(session, trigger)
    return {"success": True, "message": f"Removed trigger {trigger_id}"}
