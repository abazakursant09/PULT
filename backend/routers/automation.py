"""L4 automation rules CRUD (ME-1 shape; engine wired in ME-7).

AR-CONTROL: Auto Reviews (action publish_review_response) are managed PER marketplace connection,
gated by explicit seller consent and a server-side gate (services/marketplace/review_automation_gate).
Default OFF: a rule is created disabled, in confirm mode, with no consent.
"""
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from schemas.marketplace import AutomationRuleCreate, AutomationRuleOut, AutomationRuleModeUpdate
from services.marketplace import action_catalog
from services.marketplace import review_automation_gate as gate

log = logging.getLogger(__name__)
router = APIRouter()

# The marketplaces a review-automation policy may name. Data-driven membership, not an if/elif —
# a marketplace is either in this set or it is rejected. WB is the only one the ingestion/publish
# path actually supports today; the others are accepted as declared scope but stay honestly
# unsupported downstream (no synced reviews, no publish path), so a rule naming them simply never
# acts. Naming an unknown marketplace is rejected outright.
_REVIEW_MARKETPLACES = {"wildberries", "ozon", "yandex_market", "megamarket"}

REVIEW_ACTION = gate.REVIEW_ACTION


def _validate_review_policy(action_type: str, guard: dict) -> None:
    """Validate the AR4 review-automation policy carried in AutomationRule.guard (no new columns).

    Only enforced for publish_review_response — other actions keep their own guard shape untouched.
    """
    if action_type != REVIEW_ACTION:
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


async def _load_owned_rule(db: AsyncSession, rule_id: str, user: User) -> AutomationRule:
    rule = (
        await db.execute(
            select(AutomationRule).where(
                AutomationRule.id == rule_id,
                AutomationRule.user_id == user.id,
            )
        )
    ).scalars().first()
    if rule is None:
        raise HTTPException(404, "rule not found")
    return rule


async def _load_owned_connection(db: AsyncSession, connection_id: str, user: User) -> MarketplaceConnection:
    conn = (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.id == connection_id,
                MarketplaceConnection.user_id == user.id,
            )
        )
    ).scalars().first()
    if conn is None:
        raise HTTPException(404, "connection not found")
    return conn


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


@router.get("/automation-rules/by-connection/{connection_id}", response_model=Optional[AutomationRuleOut])
async def get_review_rule_for_connection(
    connection_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The seller's Auto Reviews rule for one of their connections (or null if none exists yet)."""
    await _load_owned_connection(db, connection_id, current_user)
    rule = (
        await db.execute(
            select(AutomationRule).where(
                AutomationRule.user_id == current_user.id,
                AutomationRule.connection_id == connection_id,
                AutomationRule.action_type == REVIEW_ACTION,
            )
        )
    ).scalars().first()
    return rule


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

    # Auto Reviews rules are per-connection: require an owned connection.
    if body.action_type == REVIEW_ACTION:
        if not body.connection_id:
            raise HTTPException(422, "connection_id is required for publish_review_response rules")
        await _load_owned_connection(db, body.connection_id, current_user)

    rule = AutomationRule(
        id=str(uuid.uuid4()), user_id=current_user.id, contour=body.contour,
        action_type=body.action_type, trigger=body.trigger, guard=body.guard,
        mode=body.mode, enabled=body.enabled, connection_id=body.connection_id,
    )
    # A newly created rule never carries consent, so enabling+auto is refused here if requested.
    if rule.action_type == REVIEW_ACTION and rule.enabled:
        try:
            gate.check_enable_preconditions(
                rule, await _load_owned_connection(db, rule.connection_id, current_user))
        except gate.AutoPublishBlocked as e:
            raise HTTPException(409, f"cannot enable: {e.reason}")
    db.add(rule)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "an Auto Reviews rule already exists for this connection")
    await db.refresh(rule)
    return rule


@router.post("/automation-rules/{rule_id}/consent", response_model=AutomationRuleOut)
async def grant_consent(
    rule_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record explicit seller consent to automated publishing for this rule's connection."""
    rule = await _load_owned_rule(db, rule_id, current_user)
    if rule.action_type != REVIEW_ACTION:
        raise HTTPException(422, "consent applies only to review-automation rules")
    if not rule.connection_id:
        raise HTTPException(422, "rule is not bound to a connection")
    await _load_owned_connection(db, rule.connection_id, current_user)   # ownership re-check
    rule.consent_at = datetime.utcnow()
    rule.consent_version = gate.CONSENT_VERSION
    rule.consent_revoked_at = None
    await db.commit()
    await db.refresh(rule)
    log.info("auto-reviews consent granted rule=%s conn=%s", rule_id, rule.connection_id)
    return rule


@router.post("/automation-rules/{rule_id}/consent/revoke", response_model=AutomationRuleOut)
async def revoke_consent(
    rule_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke consent — disables the rule in the SAME transaction; no new auto-publish may occur."""
    rule = await _load_owned_rule(db, rule_id, current_user)
    rule.consent_revoked_at = datetime.utcnow()
    rule.enabled = False
    await db.commit()
    await db.refresh(rule)
    log.info("auto-reviews consent revoked rule=%s (disabled)", rule_id)
    return rule


@router.patch("/automation-rules/{rule_id}/mode", response_model=AutomationRuleOut)
async def set_mode(
    rule_id: str,
    body: AutomationRuleModeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.mode not in ("confirm", "auto"):
        raise HTTPException(422, "mode must be 'confirm' or 'auto'")
    rule = await _load_owned_rule(db, rule_id, current_user)
    rule.mode = body.mode
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/automation-rules/{rule_id}/toggle", response_model=AutomationRuleOut)
async def toggle_rule(
    rule_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await _load_owned_rule(db, rule_id, current_user)
    # Turning OFF is always allowed and immediate. Turning ON a review rule must pass the
    # consent + connection preconditions — the same the auto-publish gate enforces — so a UI can
    # never enable automation the backend would refuse to run.
    if not rule.enabled and rule.action_type == REVIEW_ACTION:
        conn = None
        if rule.connection_id:
            conn = (
                await db.execute(
                    select(MarketplaceConnection).where(
                        MarketplaceConnection.id == rule.connection_id,
                        MarketplaceConnection.user_id == current_user.id,
                    )
                )
            ).scalars().first()
        try:
            gate.check_enable_preconditions(rule, conn)
        except gate.AutoPublishBlocked as e:
            raise HTTPException(409, f"cannot enable: {e.reason}")
    rule.enabled = not rule.enabled
    await db.commit()
    await db.refresh(rule)
    log.info("automation rule %s enabled=%s", rule_id, rule.enabled)
    return rule
