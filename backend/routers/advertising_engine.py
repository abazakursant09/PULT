"""
Advertising Engine read/trigger API (A7) — exposes the advertising core (A2-A6).

Read-only except POST /advertising/audit (runs the pure engine over an internal
finance snapshot + persists). Money-first: advertising judged via impact on
profit/margin/stock/listing. NO Decision bridge, NO measurement, NO content-write,
NO AI, NO external API, NO campaign/CTR/CPC, NO score. Marketplace-agnostic.
Honest degradation: missing finance → status=finance_unavailable (not an error).
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.advertising_audit import AdvertisingAudit
from models.advertising_problem import AdvertisingProblem
from models.advertising_signal import AdvertisingSignal

from services.advertising.snapshot import AdvertisingSnapshot, AdvertisingThresholds, AdvertisingDataUnavailable
from services.advertising.internal_source import build_snapshot_from_finance
from services.advertising.audit_persist import audit_and_persist
from services.promotion_activation.runner import run_promotion

import logging

router = APIRouter()
log = logging.getLogger(__name__)

# promoted_to_decision stays live: the signal has a Decision and awaits manual apply,
# it is still an open advertising issue (consistent with the Daily Decision Feed).
_LIVE = ("active", "reopened", "promoted_to_decision")


def _loads(text: Optional[str]):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _scope(stmt, model, user_id, listing_id):
    stmt = stmt.where(model.user_id == user_id)
    if listing_id:
        stmt = stmt.where(model.listing_id == listing_id)
    return stmt


# ── POST /advertising/audit ──────────────────────────────────────────────────

class ThresholdsIn(BaseModel):
    max_drr: float
    min_revenue_for_signal: float
    min_ad_spend_for_signal: float
    low_margin_threshold: float
    low_stock_units: int
    oos_risk_days: float


class AdvAuditRequest(BaseModel):
    listing_id: Optional[str] = None
    marketplace: str
    sku: str
    thresholds: Optional[ThresholdsIn] = None   # omitted → threshold rules not_evaluated


class ReconciliationView(BaseModel):
    created: int
    updated: int
    resolved: int
    reopened: int
    unchanged: int


class PromotionView(BaseModel):
    run_id: Optional[str] = None
    decisions_created: int = 0
    links_created: int = 0
    skipped: int = 0
    status: str = "skipped"           # completed | skipped | failed


class AdvAuditResponse(BaseModel):
    ok: bool
    status: str                       # completed | finance_unavailable | <reason>
    listing_id: Optional[str]
    marketplace: str
    sku: str
    audit_id: Optional[str] = None
    total_problems: Optional[int] = None
    total_not_evaluated: Optional[int] = None
    top_severity: Optional[str] = None
    reconciliation: Optional[ReconciliationView] = None
    reason: Optional[str] = None
    # additive, optional — auto-promotion of actionable signals after a successful
    # audit. None when the audit itself did not complete.
    promotion: Optional[PromotionView] = None


async def _promotion_hook(db, user_id: str) -> PromotionView:
    """Run promotion after a successful advertising audit. Non-blocking: a failure
    here NEVER breaks the (already-committed) audit — it is logged and reported."""
    try:
        r = await run_promotion(db, user_id=user_id, contour="advertising",
                                triggered_by="audit_hook")
        await db.commit()
        return PromotionView(run_id=r.run_id, decisions_created=r.decisions_created,
                             links_created=r.links_created, skipped=r.skipped, status="completed")
    except Exception:
        log.exception("promotion hook failed after advertising audit (audit unaffected)")
        await db.rollback()
        return PromotionView(status="failed")


@router.post("/advertising/audit", response_model=AdvAuditResponse)
async def run_advertising_audit(
    body: AdvAuditRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdvAuditResponse:
    thresholds = AdvertisingThresholds(**body.thresholds.model_dump()) if body.thresholds else None
    snap = await build_snapshot_from_finance(
        db, user_id=current_user.id, marketplace=body.marketplace, sku=body.sku,
        listing_id=body.listing_id, thresholds=thresholds)
    if isinstance(snap, AdvertisingDataUnavailable):
        status = "finance_unavailable" if snap.reason == "finance_missing" else snap.reason
        return AdvAuditResponse(ok=False, status=status, listing_id=body.listing_id,
                                marketplace=body.marketplace, sku=body.sku, reason=snap.reason)
    assert isinstance(snap, AdvertisingSnapshot)
    res = await audit_and_persist(db, user_id=current_user.id, snapshot=snap, triggered_by="manual")
    await db.commit()
    # audit is persisted + committed; promotion runs as a non-blocking side-effect
    promotion = await _promotion_hook(db, current_user.id)
    rec = res.reconciliation
    return AdvAuditResponse(
        ok=True, status="completed", listing_id=body.listing_id, marketplace=body.marketplace,
        sku=body.sku, audit_id=res.audit_id, total_problems=res.total_problems,
        total_not_evaluated=res.total_not_evaluated, top_severity=res.top_severity,
        reconciliation=ReconciliationView(created=rec.created, updated=rec.updated,
                                          resolved=rec.resolved, reopened=rec.reopened,
                                          unchanged=rec.unchanged) if rec else None,
        promotion=promotion,
    )


# ── GET /advertising/overview ────────────────────────────────────────────────

class AdvOverviewResponse(BaseModel):
    listing_id: Optional[str]
    active_signals: int
    critical_signals: int
    high_signals: int
    unresolved_problems: int
    total_not_evaluated: int
    last_audit_at: Optional[str]


@router.get("/advertising/overview", response_model=AdvOverviewResponse)
async def advertising_overview(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdvOverviewResponse:
    uid = current_user.id
    live = (await db.execute(_scope(
        select(AdvertisingSignal).where(AdvertisingSignal.status.in_(_LIVE)),
        AdvertisingSignal, uid, listing_id))).scalars().all()
    last_audit = (await db.execute(_scope(
        select(AdvertisingAudit).order_by(AdvertisingAudit.created_at.desc()).limit(1),
        AdvertisingAudit, uid, listing_id))).scalars().first()
    return AdvOverviewResponse(
        listing_id=listing_id,
        active_signals=len(live),
        critical_signals=sum(1 for s in live if s.priority_level == "critical"),
        high_signals=sum(1 for s in live if s.priority_level == "high"),
        unresolved_problems=len(live),
        total_not_evaluated=last_audit.total_not_evaluated if last_audit else 0,
        last_audit_at=last_audit.created_at.isoformat() if last_audit and last_audit.created_at else None,
    )


# ── GET /advertising/signals ─────────────────────────────────────────────────

class AdvSignalView(BaseModel):
    insight_key: Optional[str]
    signal_key: str
    problem_type: str
    status: str
    priority_level: Optional[str]
    recommended_action: Optional[str]
    recommended_action_key: Optional[str]
    alternative_action_keys: list[str]
    what: Optional[str]
    why: Optional[str]
    meaning: Optional[str]
    expected_effect: Optional[str]
    effect_band: Optional[str]
    confidence: Optional[float]


class AdvSignalsResponse(BaseModel):
    items: list[AdvSignalView]
    total: int


@router.get("/advertising/signals", response_model=AdvSignalsResponse)
async def advertising_signals(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdvSignalsResponse:
    stmt = _scope(select(AdvertisingSignal), AdvertisingSignal, current_user.id, listing_id)
    if status:
        stmt = stmt.where(AdvertisingSignal.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    items = [AdvSignalView(
        insight_key=s.insight_key, signal_key=s.signal_key, problem_type=s.problem_type,
        status=s.status, priority_level=s.priority_level, recommended_action=s.what_to_do,
        recommended_action_key=s.recommended_action_key,
        alternative_action_keys=_loads(s.alternative_action_keys) or [],
        what=s.what, why=s.why, meaning=s.meaning, expected_effect=s.expected_effect,
        effect_band=s.effect_band, confidence=s.confidence,
    ) for s in rows]
    return AdvSignalsResponse(items=items, total=len(items))


# ── GET /advertising/problems ────────────────────────────────────────────────

class AdvProblemView(BaseModel):
    problem_type: str
    severity: str
    category: Optional[str]
    estimated_effect_type: Optional[str]
    evidence: Optional[dict]
    detected_at: Optional[str]


class AdvProblemsResponse(BaseModel):
    items: list[AdvProblemView]
    total: int


@router.get("/advertising/problems", response_model=AdvProblemsResponse)
async def advertising_problems(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdvProblemsResponse:
    uid = current_user.id
    last_audit_id = (await db.execute(_scope(
        select(AdvertisingAudit.id).order_by(AdvertisingAudit.created_at.desc()).limit(1),
        AdvertisingAudit, uid, listing_id))).scalar()
    if not last_audit_id:
        return AdvProblemsResponse(items=[], total=0)
    rows = (await db.execute(select(AdvertisingProblem).where(
        AdvertisingProblem.audit_id == last_audit_id))).scalars().all()
    items = [AdvProblemView(
        problem_type=p.problem_type, severity=p.severity, category=p.category,
        estimated_effect_type=p.estimated_effect_type, evidence=_loads(p.evidence),
        detected_at=p.created_at.isoformat() if p.created_at else None,
    ) for p in rows]
    return AdvProblemsResponse(items=items, total=len(items))


# ── GET /advertising/audits ──────────────────────────────────────────────────

class AdvAuditView(BaseModel):
    audit_id: str
    status: str
    total_problems: int
    total_not_evaluated: int
    top_severity: Optional[str]
    triggered_by: Optional[str]
    created_at: Optional[str]


class AdvAuditsResponse(BaseModel):
    items: list[AdvAuditView]
    total: int


@router.get("/advertising/audits", response_model=AdvAuditsResponse)
async def advertising_audits(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdvAuditsResponse:
    rows = (await db.execute(_scope(
        select(AdvertisingAudit).order_by(AdvertisingAudit.created_at.desc()).limit(max(1, min(limit, 200))),
        AdvertisingAudit, current_user.id, listing_id))).scalars().all()
    items = [AdvAuditView(
        audit_id=a.id, status=a.status, total_problems=a.total_problems or 0,
        total_not_evaluated=a.total_not_evaluated or 0, top_severity=a.top_severity,
        triggered_by=a.triggered_by,
        created_at=a.created_at.isoformat() if a.created_at else None,
    ) for a in rows]
    return AdvAuditsResponse(items=items, total=len(items))
