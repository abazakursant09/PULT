"""
Growth Engine read/trigger API (A7) — exposes the growth core (A2-A6).

Read-only except POST /growth/audit (builds an internal GrowthSnapshot from data
PULT already stores, runs the pure engine + persist + reconcile). Growth Engine
surfaces unrealised opportunity — money not taken yet. NO Decision bridge, NO
measurement, NO content-write, NO AI, NO forecast, NO competitor data, NO external
API, NO growth score. Marketplace-agnostic. Honest degradation: missing finance →
status=growth_unavailable (not an error).
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.growth_audit import GrowthAudit
from models.growth_problem import GrowthProblem
from models.growth_signal import GrowthSignal

from services.growth.snapshot import GrowthSnapshot, GrowthDataUnavailable
from services.growth.internal_source import build_snapshot_from_internal
from services.growth.audit_persist import audit_and_persist
from services.growth.rules import GrowthThresholds

router = APIRouter()

_LIVE = ("active", "reopened")


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


# ── POST /growth/audit ───────────────────────────────────────────────────────

class ThresholdsIn(BaseModel):
    low_stock_units: Optional[int] = None
    min_revenue_for_growth_signal: Optional[float] = None
    min_net_profit_for_growth_signal: Optional[float] = None


class GrowthAuditRequest(BaseModel):
    listing_id: Optional[str] = None
    marketplace: str
    sku: str
    thresholds: Optional[ThresholdsIn] = None   # omitted/partial → those rules not_evaluated


class ReconciliationView(BaseModel):
    created: int
    updated: int
    resolved: int
    reopened: int
    unchanged: int


class GrowthAuditResponse(BaseModel):
    ok: bool
    status: str                       # completed | growth_unavailable | <reason>
    listing_id: Optional[str]
    marketplace: str
    sku: str
    audit_id: Optional[str] = None
    total_problems: Optional[int] = None
    total_not_evaluated: Optional[int] = None
    top_severity: Optional[str] = None
    reconciliation: Optional[ReconciliationView] = None
    reason: Optional[str] = None


@router.post("/growth/audit", response_model=GrowthAuditResponse)
async def run_growth_audit(
    body: GrowthAuditRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrowthAuditResponse:
    thresholds = GrowthThresholds(**body.thresholds.model_dump()) if body.thresholds else GrowthThresholds()
    snap = await build_snapshot_from_internal(
        db, user_id=current_user.id, marketplace=body.marketplace, sku=body.sku,
        listing_id=body.listing_id)
    if isinstance(snap, GrowthDataUnavailable):
        status = "growth_unavailable" if snap.reason == "finance_missing" else snap.reason
        return GrowthAuditResponse(ok=False, status=status, listing_id=body.listing_id,
                                   marketplace=body.marketplace, sku=body.sku, reason=snap.reason)
    assert isinstance(snap, GrowthSnapshot)
    res = await audit_and_persist(db, user_id=current_user.id, snapshot=snap,
                                  thresholds=thresholds, triggered_by="manual")
    await db.commit()
    rec = res.reconciliation
    return GrowthAuditResponse(
        ok=True, status="completed", listing_id=body.listing_id, marketplace=body.marketplace,
        sku=body.sku, audit_id=res.audit_id, total_problems=res.total_problems,
        total_not_evaluated=res.total_not_evaluated, top_severity=res.top_severity,
        reconciliation=ReconciliationView(created=rec.created, updated=rec.updated,
                                          resolved=rec.resolved, reopened=rec.reopened,
                                          unchanged=rec.unchanged) if rec else None,
    )


# ── GET /growth/overview ─────────────────────────────────────────────────────

class GrowthOverviewResponse(BaseModel):
    listing_id: Optional[str]
    active_signals: int
    high_signals: int
    medium_signals: int
    unresolved_opportunities: int
    total_not_evaluated: int
    last_audit_at: Optional[str]


@router.get("/growth/overview", response_model=GrowthOverviewResponse)
async def growth_overview(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrowthOverviewResponse:
    uid = current_user.id
    live = (await db.execute(_scope(
        select(GrowthSignal).where(GrowthSignal.status.in_(_LIVE)),
        GrowthSignal, uid, listing_id))).scalars().all()
    last_audit = (await db.execute(_scope(
        select(GrowthAudit).order_by(GrowthAudit.created_at.desc()).limit(1),
        GrowthAudit, uid, listing_id))).scalars().first()
    return GrowthOverviewResponse(
        listing_id=listing_id,
        active_signals=len(live),
        high_signals=sum(1 for s in live if s.priority_level == "high"),
        medium_signals=sum(1 for s in live if s.priority_level == "medium"),
        unresolved_opportunities=len(live),
        total_not_evaluated=last_audit.total_not_evaluated if last_audit else 0,
        last_audit_at=last_audit.created_at.isoformat() if last_audit and last_audit.created_at else None,
    )


# ── GET /growth/signals ──────────────────────────────────────────────────────

class GrowthSignalView(BaseModel):
    insight_key: Optional[str]
    signal_key: str
    problem_type: str
    status: str
    category: Optional[str]
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


class GrowthSignalsResponse(BaseModel):
    items: list[GrowthSignalView]
    total: int


@router.get("/growth/signals", response_model=GrowthSignalsResponse)
async def growth_signals(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrowthSignalsResponse:
    stmt = _scope(select(GrowthSignal), GrowthSignal, current_user.id, listing_id)
    if status:
        stmt = stmt.where(GrowthSignal.status == status)
    if category:
        stmt = stmt.where(GrowthSignal.category == category)
    rows = (await db.execute(stmt)).scalars().all()
    items = [GrowthSignalView(
        insight_key=s.insight_key, signal_key=s.signal_key, problem_type=s.problem_type,
        status=s.status, category=s.category, priority_level=s.priority_level,
        recommended_action=s.what_to_do, recommended_action_key=s.recommended_action_key,
        alternative_action_keys=_loads(s.alternative_action_keys) or [],
        what=s.what, why=s.why, meaning=s.meaning, expected_effect=s.expected_effect,
        effect_band=s.effect_band, confidence=s.confidence,
    ) for s in rows]
    return GrowthSignalsResponse(items=items, total=len(items))


# ── GET /growth/problems ─────────────────────────────────────────────────────

class GrowthProblemView(BaseModel):
    problem_type: str
    severity: str
    category: Optional[str]
    estimated_effect_type: Optional[str]
    evidence: Optional[dict]
    detected_at: Optional[str]


class GrowthProblemsResponse(BaseModel):
    items: list[GrowthProblemView]
    total: int


@router.get("/growth/problems", response_model=GrowthProblemsResponse)
async def growth_problems(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrowthProblemsResponse:
    uid = current_user.id
    last_audit_id = (await db.execute(_scope(
        select(GrowthAudit.id).order_by(GrowthAudit.created_at.desc()).limit(1),
        GrowthAudit, uid, listing_id))).scalar()
    if not last_audit_id:
        return GrowthProblemsResponse(items=[], total=0)
    rows = (await db.execute(select(GrowthProblem).where(
        GrowthProblem.audit_id == last_audit_id))).scalars().all()
    items = [GrowthProblemView(
        problem_type=p.problem_type, severity=p.severity, category=p.category,
        estimated_effect_type=p.estimated_effect_type, evidence=_loads(p.evidence),
        detected_at=p.created_at.isoformat() if p.created_at else None,
    ) for p in rows]
    return GrowthProblemsResponse(items=items, total=len(items))


# ── GET /growth/audits ───────────────────────────────────────────────────────

class GrowthAuditView(BaseModel):
    audit_id: str
    status: str
    total_problems: int
    total_not_evaluated: int
    top_severity: Optional[str]
    triggered_by: Optional[str]
    created_at: Optional[str]


class GrowthAuditsResponse(BaseModel):
    items: list[GrowthAuditView]
    total: int


@router.get("/growth/audits", response_model=GrowthAuditsResponse)
async def growth_audits(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrowthAuditsResponse:
    rows = (await db.execute(_scope(
        select(GrowthAudit).order_by(GrowthAudit.created_at.desc()).limit(max(1, min(limit, 200))),
        GrowthAudit, current_user.id, listing_id))).scalars().all()
    items = [GrowthAuditView(
        audit_id=a.id, status=a.status, total_problems=a.total_problems or 0,
        total_not_evaluated=a.total_not_evaluated or 0, top_severity=a.top_severity,
        triggered_by=a.triggered_by,
        created_at=a.created_at.isoformat() if a.created_at else None,
    ) for a in rows]
    return GrowthAuditsResponse(items=items, total=len(items))
