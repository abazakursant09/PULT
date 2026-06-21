"""
Review Assistant read/trigger API (A7) — exposes the review core (A2-A6).

Reputation contour, read-only except POST /reviews/audit (runs the pure engine
over an internal ReviewResponse snapshot + persists). It never drafts, sends, or
auto-publishes review replies. No language-model use, no content-write, no
Decision bridge, no measurement, no external API, no score. Marketplace-agnostic.
Honest degradation: missing review → status=review_unavailable (not an error).
safety_mode is always visible; AUTO is never returned for RISK/ATTENTION.

NOTE: this router must be mounted BEFORE routers.reviews (whose GET
/reviews/{product_id} would otherwise shadow these explicit GET paths).
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
from models.review_audit import ReviewAudit
from models.review_problem import ReviewProblem
from models.review_signal import ReviewSignal

from services.review.snapshot import ReviewSnapshot, ReviewDataUnavailable
from services.review.internal_source import build_snapshot_from_reviews
from services.review.audit_persist import audit_and_persist

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


# ── POST /reviews/audit ──────────────────────────────────────────────────────

class ReviewAuditRequest(BaseModel):
    review_id: str
    marketplace: Optional[str] = None


class ReconciliationView(BaseModel):
    created: int
    updated: int
    resolved: int
    reopened: int
    unchanged: int


class ReviewAuditResponse(BaseModel):
    ok: bool
    status: str                       # completed | review_unavailable | <reason>
    review_id: str
    audit_id: Optional[str] = None
    total_problems: Optional[int] = None
    total_not_evaluated: Optional[int] = None
    top_severity: Optional[str] = None
    reconciliation: Optional[ReconciliationView] = None
    reason: Optional[str] = None


@router.post("/reviews/audit", response_model=ReviewAuditResponse)
async def run_review_audit(
    body: ReviewAuditRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewAuditResponse:
    snap = await build_snapshot_from_reviews(
        db, review_id=body.review_id, marketplace=body.marketplace,
        owner_user_id=current_user.id)
    if isinstance(snap, ReviewDataUnavailable):
        status = "review_unavailable" if snap.reason == "review_missing" else snap.reason
        return ReviewAuditResponse(ok=False, status=status, review_id=body.review_id, reason=snap.reason)
    assert isinstance(snap, ReviewSnapshot)
    res = await audit_and_persist(db, user_id=current_user.id, snapshot=snap, triggered_by="manual")
    await db.commit()
    rec = res.reconciliation
    return ReviewAuditResponse(
        ok=True, status="completed", review_id=body.review_id, audit_id=res.audit_id,
        total_problems=res.total_problems, total_not_evaluated=res.total_not_evaluated,
        top_severity=res.top_severity,
        reconciliation=ReconciliationView(created=rec.created, updated=rec.updated,
                                          resolved=rec.resolved, reopened=rec.reopened,
                                          unchanged=rec.unchanged) if rec else None,
    )


# ── GET /reviews/overview ────────────────────────────────────────────────────

class ReviewOverviewResponse(BaseModel):
    listing_id: Optional[str]
    active_signals: int
    risk_signals: int
    attention_signals: int
    safe_signals: int
    unresolved_problems: int
    total_not_evaluated: int
    last_audit_at: Optional[str]


@router.get("/reviews/overview", response_model=ReviewOverviewResponse)
async def review_overview(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewOverviewResponse:
    uid = current_user.id
    live = (await db.execute(_scope(
        select(ReviewSignal).where(ReviewSignal.status.in_(_LIVE)),
        ReviewSignal, uid, listing_id))).scalars().all()
    last_audit = (await db.execute(_scope(
        select(ReviewAudit).order_by(ReviewAudit.created_at.desc()).limit(1),
        ReviewAudit, uid, listing_id))).scalars().first()
    return ReviewOverviewResponse(
        listing_id=listing_id,
        active_signals=len(live),
        risk_signals=sum(1 for s in live if s.safety_category == "RISK"),
        attention_signals=sum(1 for s in live if s.safety_category == "ATTENTION"),
        safe_signals=sum(1 for s in live if s.safety_category == "SAFE"),
        unresolved_problems=len(live),
        total_not_evaluated=last_audit.total_not_evaluated if last_audit else 0,
        last_audit_at=last_audit.created_at.isoformat() if last_audit and last_audit.created_at else None,
    )


# ── GET /reviews/signals ─────────────────────────────────────────────────────

class ReviewSignalView(BaseModel):
    insight_key: Optional[str]
    signal_key: str
    problem_type: str
    review_id: Optional[str]
    status: str
    safety_category: Optional[str]
    safety_mode: Optional[str]
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


class ReviewSignalsResponse(BaseModel):
    items: list[ReviewSignalView]
    total: int


@router.get("/reviews/signals", response_model=ReviewSignalsResponse)
async def review_signals(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    status: Optional[str] = None,
    safety_category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewSignalsResponse:
    stmt = _scope(select(ReviewSignal), ReviewSignal, current_user.id, listing_id)
    if status:
        stmt = stmt.where(ReviewSignal.status == status)
    if safety_category:
        stmt = stmt.where(ReviewSignal.safety_category == safety_category)
    rows = (await db.execute(stmt)).scalars().all()
    items = [ReviewSignalView(
        insight_key=s.insight_key, signal_key=s.signal_key, problem_type=s.problem_type,
        review_id=s.review_id, status=s.status, safety_category=s.safety_category,
        safety_mode=s.safety_mode, priority_level=s.priority_level,
        recommended_action=s.what_to_do, recommended_action_key=s.recommended_action_key,
        alternative_action_keys=_loads(s.alternative_action_keys) or [],
        what=s.what, why=s.why, meaning=s.meaning, expected_effect=s.expected_effect,
        effect_band=s.effect_band, confidence=s.confidence,
    ) for s in rows]
    return ReviewSignalsResponse(items=items, total=len(items))


# ── GET /reviews/problems ────────────────────────────────────────────────────

class ReviewProblemView(BaseModel):
    review_id: Optional[str]
    problem_type: str
    severity: str
    category: Optional[str]
    estimated_effect_type: Optional[str]
    evidence: Optional[dict]
    detected_at: Optional[str]


class ReviewProblemsResponse(BaseModel):
    items: list[ReviewProblemView]
    total: int


@router.get("/reviews/problems", response_model=ReviewProblemsResponse)
async def review_problems(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewProblemsResponse:
    uid = current_user.id
    last_audit_id = (await db.execute(_scope(
        select(ReviewAudit.id).order_by(ReviewAudit.created_at.desc()).limit(1),
        ReviewAudit, uid, listing_id))).scalar()
    if not last_audit_id:
        return ReviewProblemsResponse(items=[], total=0)
    rows = (await db.execute(select(ReviewProblem).where(
        ReviewProblem.audit_id == last_audit_id))).scalars().all()
    items = [ReviewProblemView(
        review_id=p.review_id, problem_type=p.problem_type, severity=p.severity, category=p.category,
        estimated_effect_type=p.estimated_effect_type, evidence=_loads(p.evidence),
        detected_at=p.created_at.isoformat() if p.created_at else None,
    ) for p in rows]
    return ReviewProblemsResponse(items=items, total=len(items))


# ── GET /reviews/audits ──────────────────────────────────────────────────────

class ReviewAuditView(BaseModel):
    audit_id: str
    status: str
    total_problems: int
    total_not_evaluated: int
    top_severity: Optional[str]
    triggered_by: Optional[str]
    created_at: Optional[str]


class ReviewAuditsResponse(BaseModel):
    items: list[ReviewAuditView]
    total: int


@router.get("/reviews/audits", response_model=ReviewAuditsResponse)
async def review_audits(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewAuditsResponse:
    rows = (await db.execute(_scope(
        select(ReviewAudit).order_by(ReviewAudit.created_at.desc()).limit(max(1, min(limit, 200))),
        ReviewAudit, current_user.id, listing_id))).scalars().all()
    items = [ReviewAuditView(
        audit_id=a.id, status=a.status, total_problems=a.total_problems or 0,
        total_not_evaluated=a.total_not_evaluated or 0, top_severity=a.top_severity,
        triggered_by=a.triggered_by,
        created_at=a.created_at.isoformat() if a.created_at else None,
    ) for a in rows]
    return ReviewAuditsResponse(items=items, total=len(items))
