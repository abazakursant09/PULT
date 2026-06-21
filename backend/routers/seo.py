"""
SEO Engine read/trigger API (A7) — exposes the SEO core (A2-A6) over HTTP.

Read-only except POST /seo/audit (which runs the pure engine + persists). No
Decision bridge, no measurement, no content-write, no AI, no external services,
and NO SEO score / search_visibility ever surfaced. Marketplace-agnostic — the
handler dispatches through the SeoAdapter registry and never branches on a
specific marketplace. When an adapter cannot build a snapshot it returns an
honest snapshot_unavailable (no fake data).
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
from models.seo_audit import SeoAudit
from models.seo_problem import SeoProblem
from models.seo_signal import SeoSignal

from services.seo.card_snapshot import CardSnapshot
from services.seo.adapter import SnapshotUnavailable
from services.seo.registry import get_seo_adapter
from services.seo.audit_persist import audit_and_persist

router = APIRouter()

_LIVE = ("active", "reopened")


def _loads(text: Optional[str]):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


# ── POST /seo/audit ──────────────────────────────────────────────────────────

class SeoAuditRequest(BaseModel):
    listing_id: str
    marketplace: str


class ReconciliationView(BaseModel):
    created: int
    updated: int
    resolved: int
    reopened: int
    unchanged: int


class SeoAuditResponse(BaseModel):
    ok: bool
    status: str                       # completed | snapshot_unavailable | unknown_marketplace
    listing_id: str
    marketplace: str
    audit_id: Optional[str] = None
    total_problems: Optional[int] = None
    total_not_evaluated: Optional[int] = None
    top_severity: Optional[str] = None
    reconciliation: Optional[ReconciliationView] = None
    reason: Optional[str] = None      # set on snapshot_unavailable / unknown_marketplace


@router.post("/seo/audit", response_model=SeoAuditResponse)
async def run_seo_audit(
    body: SeoAuditRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SeoAuditResponse:
    adapter = get_seo_adapter(body.marketplace)
    if adapter is None:
        return SeoAuditResponse(ok=False, status="unknown_marketplace",
                                listing_id=body.listing_id, marketplace=body.marketplace,
                                reason="no_adapter_for_marketplace")
    snap = await adapter.build_snapshot(listing_id=body.listing_id, db=db, token=None)
    if isinstance(snap, SnapshotUnavailable):
        return SeoAuditResponse(ok=False, status="snapshot_unavailable",
                                listing_id=body.listing_id, marketplace=body.marketplace,
                                reason=snap.reason)
    assert isinstance(snap, CardSnapshot)
    res = await audit_and_persist(db, user_id=current_user.id, snapshot=snap, triggered_by="manual")
    await db.commit()
    rec = res.reconciliation
    return SeoAuditResponse(
        ok=True, status="completed", listing_id=body.listing_id, marketplace=body.marketplace,
        audit_id=res.audit_id, total_problems=res.total_problems,
        total_not_evaluated=res.total_not_evaluated, top_severity=res.top_severity,
        reconciliation=ReconciliationView(created=rec.created, updated=rec.updated,
                                          resolved=rec.resolved, reopened=rec.reopened,
                                          unchanged=rec.unchanged) if rec else None,
    )


# ── GET /seo/overview ────────────────────────────────────────────────────────

class SeoOverviewResponse(BaseModel):
    listing_id: Optional[str]
    active_signals: int
    critical_signals: int
    high_signals: int
    unresolved_problems: int
    last_audit_at: Optional[str]      # ISO; null if no audit yet


def _scope(stmt, model, user_id, listing_id):
    stmt = stmt.where(model.user_id == user_id)
    if listing_id:
        stmt = stmt.where(model.listing_id == listing_id)
    return stmt


@router.get("/seo/overview", response_model=SeoOverviewResponse)
async def seo_overview(
    listing_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SeoOverviewResponse:
    uid = current_user.id
    live = (await db.execute(_scope(
        select(SeoSignal).where(SeoSignal.status.in_(_LIVE)), SeoSignal, uid, listing_id)
    )).scalars().all()
    last = (await db.execute(_scope(
        select(func.max(SeoAudit.created_at)), SeoAudit, uid, listing_id))).scalar()
    return SeoOverviewResponse(
        listing_id=listing_id,
        active_signals=len(live),
        critical_signals=sum(1 for s in live if s.priority_level == "critical"),
        high_signals=sum(1 for s in live if s.priority_level == "high"),
        unresolved_problems=len(live),
        last_audit_at=last.isoformat() if last else None,
    )


# ── GET /seo/signals ─────────────────────────────────────────────────────────

class SeoSignalView(BaseModel):
    insight_key: Optional[str]
    signal_key: str
    problem_type: str
    status: str
    priority_level: Optional[str]
    recommended_action: Optional[str]       # human text (what_to_do)
    recommended_action_key: Optional[str]
    alternative_action_keys: list[str]
    what: Optional[str]
    why: Optional[str]
    meaning: Optional[str]
    expected_effect: Optional[str]
    effect_band: Optional[str]
    confidence: Optional[float]


class SeoSignalsResponse(BaseModel):
    items: list[SeoSignalView]
    total: int


@router.get("/seo/signals", response_model=SeoSignalsResponse)
async def seo_signals(
    listing_id: Optional[str] = None,
    status: Optional[str] = None,            # active | dismissed | resolved | reopened
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SeoSignalsResponse:
    stmt = _scope(select(SeoSignal), SeoSignal, current_user.id, listing_id)
    if status:
        stmt = stmt.where(SeoSignal.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    items = [SeoSignalView(
        insight_key=s.insight_key, signal_key=s.signal_key, problem_type=s.problem_type,
        status=s.status, priority_level=s.priority_level, recommended_action=s.what_to_do,
        recommended_action_key=s.recommended_action_key,
        alternative_action_keys=_loads(s.alternative_action_keys) or [],
        what=s.what, why=s.why, meaning=s.meaning, expected_effect=s.expected_effect,
        effect_band=s.effect_band, confidence=s.confidence,
    ) for s in rows]
    return SeoSignalsResponse(items=items, total=len(items))


# ── GET /seo/problems ────────────────────────────────────────────────────────

class SeoProblemView(BaseModel):
    problem_type: str
    severity: str
    category: Optional[str]
    estimated_effect_type: Optional[str]
    evidence: Optional[dict]
    detected_at: Optional[str]


class SeoProblemsResponse(BaseModel):
    items: list[SeoProblemView]
    total: int


@router.get("/seo/problems", response_model=SeoProblemsResponse)
async def seo_problems(
    listing_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SeoProblemsResponse:
    uid = current_user.id
    # problems of the latest audit for the scope (most relevant snapshot)
    last_audit_id = (await db.execute(_scope(
        select(SeoAudit.id).order_by(SeoAudit.created_at.desc()).limit(1),
        SeoAudit, uid, listing_id))).scalar()
    if not last_audit_id:
        return SeoProblemsResponse(items=[], total=0)
    rows = (await db.execute(select(SeoProblem).where(
        SeoProblem.audit_id == last_audit_id))).scalars().all()
    items = [SeoProblemView(
        problem_type=p.problem_type, severity=p.severity, category=p.category,
        estimated_effect_type=p.estimated_effect_type, evidence=_loads(p.evidence),
        detected_at=p.created_at.isoformat() if p.created_at else None,
    ) for p in rows]
    return SeoProblemsResponse(items=items, total=len(items))


# ── GET /seo/audits ──────────────────────────────────────────────────────────

class SeoAuditView(BaseModel):
    audit_id: str
    listing_id: Optional[str]
    marketplace: Optional[str]
    sku: Optional[str]
    status: str
    total_problems: int
    total_not_evaluated: int
    top_severity: Optional[str]
    triggered_by: Optional[str]
    created_at: Optional[str]
    completed_at: Optional[str]
    # NOTE: no raw snapshot, no internal_health_index — never exposed.


class SeoAuditsResponse(BaseModel):
    items: list[SeoAuditView]
    total: int


@router.get("/seo/audits", response_model=SeoAuditsResponse)
async def seo_audits(
    listing_id: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SeoAuditsResponse:
    rows = (await db.execute(_scope(
        select(SeoAudit).order_by(SeoAudit.created_at.desc()).limit(max(1, min(limit, 200))),
        SeoAudit, current_user.id, listing_id))).scalars().all()
    items = [SeoAuditView(
        audit_id=a.id, listing_id=a.listing_id, marketplace=a.marketplace, sku=a.sku,
        status=a.status, total_problems=a.total_problems or 0,
        total_not_evaluated=a.total_not_evaluated or 0, top_severity=a.top_severity,
        triggered_by=a.triggered_by,
        created_at=a.created_at.isoformat() if a.created_at else None,
        completed_at=a.completed_at.isoformat() if a.completed_at else None,
    ) for a in rows]
    return SeoAuditsResponse(items=items, total=len(items))
