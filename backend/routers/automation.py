"""L4 automation rules CRUD (ME-1 shape; engine wired in ME-7)."""
import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.automation_rule import AutomationRule
from schemas.marketplace import AutomationRuleCreate, AutomationRuleOut
from services.marketplace import action_catalog

log = logging.getLogger(__name__)
router = APIRouter()

# The marketplaces a review-automation policy may name. Data-driven membership, not an if/elif —
# a marketplace is either in this set or it is rejected. WB is the only one the ingestion/publish
# path actually supports today; the others are accepted as declared scope but stay honestly
# unsupported downstream (no synced reviews, no publish path), so a rule naming them simply never
# acts. Naming an unknown marketplace is rejected outright.
_REVIEW_MARKETPLACES = {"wildberries", "ozon", "yandex_market", "megamarket"}


def _validate_review_policy(action_type: str, guard: dict) -> None:
    """Validate the AR4 review-automation policy carried in AutomationRule.guard (no new columns).

    Only enforced for publish_review_response — other actions keep their own guard shape untouched.
    """
    if action_type != "publish_review_response":
        return
    mps = guard.get("marketplaces")
    if mps is not None:
        if not isinstance(mps, list) or any(m not in _REVIEW_MARKETPLACES for m in mps):
            raise HTTPException(422, f"marketplaces must be a subset of {sorted(_REVIEW_MARKETPLACES)}")
    mr = guard.get("min_rating")
    if mr is not None and (not isinstance(mr, int) or not (1 <= mr <= 5)):
        raise HTTPException(422, "min_rating must be an integer 1..5")
    for key in ("daily_cap", "max_per_window", "window_seconds"):
        v = guard.get(key)
        if v is not None and (not isinstance(v, int) or v <= 0):
            raise HTTPException(422, f"{key} must be a positive integer")


@router.get("/automation-rules", response_model=List[AutomationRuleOut])
async def list_rules(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(AutomationRule).where(AutomationRule.user_id == current_user.id)
        )
    ).scalars().all()
    return rows


@router.post("/automation-rules", response_model=AutomationRuleOut, status_code=201)
async def create_rule(
    body: AutomationRuleCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.action_type not in action_catalog.known_actions():
        raise HTTPException(422, f"unknown action_type: {body.action_type}")
    if body.mode not in ("confirm", "auto"):
        raise HTTPException(422, "mode must be 'confirm' or 'auto'")
    _validate_review_policy(body.action_type, body.guard or {})
    rule = AutomationRule(
        id=str(uuid.uuid4()), user_id=current_user.id, contour=body.contour,
        action_type=body.action_type, trigger=body.trigger, guard=body.guard,
        mode=body.mode, enabled=body.enabled,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/automation-rules/{rule_id}/toggle", response_model=AutomationRuleOut)
async def toggle_rule(
    rule_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = (
        await db.execute(
            select(AutomationRule).where(
                AutomationRule.id == rule_id,
                AutomationRule.user_id == current_user.id,
            )
        )
    ).scalars().first()
    if rule is None:
        raise HTTPException(404, "rule not found")
    rule.enabled = not rule.enabled
    await db.commit()
    await db.refresh(rule)
    log.info("automation rule %s enabled=%s", rule_id, rule.enabled)
    return rule
