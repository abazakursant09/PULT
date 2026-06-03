from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.monitor_event import MonitorEvent
from schemas.monitor import MonitorEventOut
from tasks.monitor_checker import run_check

router = APIRouter()


# ── GET all events ────────────────────────────────────────────────────────────

@router.get("/monitor/events", response_model=List[MonitorEventOut])
async def list_events(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MonitorEvent)
        .order_by(MonitorEvent.created_at.desc())
        .limit(100)
    )
    return result.scalars().all()


# ── GET single event ──────────────────────────────────────────────────────────

@router.get("/monitor/events/{event_id}", response_model=MonitorEventOut)
async def get_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MonitorEvent).where(MonitorEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return event


# ── POST check ────────────────────────────────────────────────────────────────

@router.post("/monitor/check", response_model=List[MonitorEventOut])
async def trigger_check(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await run_check(db)
