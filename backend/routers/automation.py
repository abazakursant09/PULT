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
