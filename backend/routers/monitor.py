from typing import List, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from schemas.monitor import MonitorEventOut

router = APIRouter()


def _monitor_unavailable() -> NoReturn:
    """No verified event provider exists; never expose legacy synthetic rows."""
    raise HTTPException(status_code=404, detail="Not found")


# ── GET all events ────────────────────────────────────────────────────────────

@router.get("/monitor/events", response_model=List[MonitorEventOut],
            dependencies=[Depends(_monitor_unavailable)])
async def list_events(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _monitor_unavailable()


# ── GET single event ──────────────────────────────────────────────────────────

@router.get("/monitor/events/{event_id}", response_model=MonitorEventOut,
            dependencies=[Depends(_monitor_unavailable)])
async def get_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _monitor_unavailable()


# ── POST check ────────────────────────────────────────────────────────────────

@router.post("/monitor/check", response_model=List[MonitorEventOut],
             dependencies=[Depends(_monitor_unavailable)])
async def trigger_check(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _monitor_unavailable()
