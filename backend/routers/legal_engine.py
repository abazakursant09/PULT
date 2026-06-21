"""
Legal Navigator read/trigger API (A7) — exposes the legal core (A2-A6).

Read + a manual audit trigger + user lifecycle actions (acknowledge / dismiss /
reopen) on a legal_signal. Legal Navigator is a RECOMMENDATION contour — it never
issues a legal conclusion, never asserts compliance, never guarantees an outcome.
NO UI, NO AI, NO score, NO forecast, NO money. expected_effect is qualitative
risk-reduction only. Honest degradation: no subject data → status=legal_unavailable
(not an error). not_evaluated is NEVER shown as compliant.

Module name is `legal_engine` because the legacy `legal` router (legal cases under
/products/{id}/legal/...) already exists; these paths (/legal/*) do not collide.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.legal_audit import LegalAudit
from models.legal_finding import LegalFinding
from models.legal_signal import LegalSignal

from services.legal.snapshot import LegalDataUnavailable
from services.legal.persist import audit_and_persist, LegalPersistResult

router = APIRouter()

_LIVE = ("active", "acknowledged", "reopened")


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


# ── signal view (5-part doctrine + lifecycle, no score/money) ────────────────

class LegalSignalView(BaseModel):
    signal_id: str
    insight_key: Optional[str]
    signal_key: str
    requirement_type: str
    category: Optional[str]
    status: str
    lifecycle_reason: Optional[str]
    priority_level: Optional[str]
    risk_level: Optional[str]
    effect_type: Optional[str]            # qualitative *_risk_reduction
    effect_band: Optional[str]
    recommended_action_key: Optional[str]
    alternative_action_keys: list[str]
    # 5-part doctrine
    what_happened: Optional[str]
    why_it_matters: Optional[str]
    meaning: Optional[str]
    recommended_action: Optional[str]
    expected_effect: Optional[str]
    subject_type: Optional[str]
    subject_ref: Optional[str]
    sku: Optional[str]
    marketplace: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


def _view(s: LegalSignal) -> LegalSignalView:
    return LegalSignalView(
        signal_id=s.id, insight_key=s.insight_key, signal_key=s.signal_key,
        requirement_type=s.requirement_type, category=s.category, status=s.status,
        lifecycle_reason=s.lifecycle_reason, priority_level=s.priority_level,
        risk_level=s.risk_level, effect_type=s.effect_type, effect_band=s.effect_band,
        recommended_action_key=s.recommended_action_key,
        alternative_action_keys=_loads(s.alternative_action_keys) or [],
        what_happened=s.what, why_it_matters=s.why, meaning=s.meaning,
        recommended_action=s.what_to_do, expected_effect=s.expected_effect,
        subject_type=s.subject_type, subject_ref=s.subject_ref, sku=s.sku,
        marketplace=s.marketplace,
        created_at=s.created_at.isoformat() if s.created_at else None,
        updated_at=s.updated_at.isoformat() if s.updated_at else None,
    )


# ── GET /legal/signals ───────────────────────────────────────────────────────

class LegalSignalsResponse(BaseModel):
    items: list[LegalSignalView]
    total: int


@router.get("/legal/signals", response_model=LegalSignalsResponse)
async def legal_signals(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    subject_ref: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalSignalsResponse:
    stmt = _scope(select(LegalSignal), LegalSignal, current_user.id, listing_id)
    if status:
        stmt = stmt.where(LegalSignal.status == status)
    if category:
        stmt = stmt.where(LegalSignal.category == category)
    if subject_ref:
        stmt = stmt.where(LegalSignal.subject_ref == subject_ref)
    rows = (await db.execute(stmt)).scalars().all()
    items = [_view(s) for s in rows]
    return LegalSignalsResponse(items=items, total=len(items))


# ── POST /legal/audit ────────────────────────────────────────────────────────

class LegalAuditRequest(BaseModel):
    marketplace: str
    subject_type: Optional[str] = None
    subject_ref: Optional[str] = None
    sku: Optional[str] = None
    listing_id: Optional[str] = None


class ReconciliationView(BaseModel):
    created: int
    updated: int
    reopened: int
    resolved: int
    unchanged: int


class LegalAuditResponse(BaseModel):
    ok: bool
    status: str                       # completed | legal_unavailable | <reason>
    marketplace: str
    subject_ref: Optional[str]
    sku: Optional[str]
    audit_id: Optional[str] = None
    total_findings: Optional[int] = None
    total_not_evaluated: Optional[int] = None
    top_severity: Optional[str] = None
    reconciliation: Optional[ReconciliationView] = None
    reason: Optional[str] = None


@router.post("/legal/audit", response_model=LegalAuditResponse)
async def run_legal_audit(
    body: LegalAuditRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalAuditResponse:
    res = await audit_and_persist(
        db, seller_id=current_user.id, marketplace=body.marketplace,
        subject_type=body.subject_type, subject_ref=body.subject_ref,
        sku=body.sku, listing_id=body.listing_id, triggered_by="manual")
    if isinstance(res, LegalDataUnavailable):
        status = "legal_unavailable" if res.reason == "insufficient_data" else res.reason
        return LegalAuditResponse(ok=False, status=status, marketplace=body.marketplace,
                                  subject_ref=body.subject_ref, sku=body.sku, reason=res.reason)
    assert isinstance(res, LegalPersistResult)
    await db.commit()
    rec = res.reconciliation
    return LegalAuditResponse(
        ok=True, status="completed", marketplace=body.marketplace, subject_ref=body.subject_ref,
        sku=body.sku, audit_id=res.audit_id, total_findings=res.total_findings,
        total_not_evaluated=res.total_not_evaluated, top_severity=res.top_severity,
        reconciliation=ReconciliationView(created=rec.created, updated=rec.updated,
                                          reopened=rec.reopened, resolved=rec.resolved,
                                          unchanged=rec.unchanged) if rec else None,
    )


# ── lifecycle actions (mutate ONLY status / lifecycle_reason / updated_at) ───

class LegalSignalActionResponse(BaseModel):
    ok: bool
    signal: LegalSignalView


async def _load_owned(db, user_id, signal_id) -> LegalSignal:
    sig = (await db.execute(select(LegalSignal).where(
        LegalSignal.id == signal_id, LegalSignal.user_id == user_id))).scalars().first()
    if sig is None:
        raise HTTPException(status_code=404, detail="legal signal not found")
    return sig


async def _transition(db, user, signal_id, *, status, reason) -> LegalSignalActionResponse:
    sig = await _load_owned(db, user.id, signal_id)
    sig.status = status
    sig.lifecycle_reason = reason
    sig.updated_at = datetime.utcnow()
    await db.commit()
    return LegalSignalActionResponse(ok=True, signal=_view(sig))


@router.post("/legal/signals/{signal_id}/acknowledge", response_model=LegalSignalActionResponse)
async def acknowledge_signal(
    signal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalSignalActionResponse:
    return await _transition(db, current_user, signal_id,
                             status="acknowledged", reason="acknowledged_by_user")


@router.post("/legal/signals/{signal_id}/dismiss", response_model=LegalSignalActionResponse)
async def dismiss_signal(
    signal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalSignalActionResponse:
    return await _transition(db, current_user, signal_id,
                             status="dismissed", reason="dismissed_by_user")


@router.post("/legal/signals/{signal_id}/reopen", response_model=LegalSignalActionResponse)
async def reopen_signal(
    signal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalSignalActionResponse:
    return await _transition(db, current_user, signal_id,
                             status="reopened", reason="reopened_by_user")


# ── GET /legal/overview (counts only, no score) ──────────────────────────────

class LegalOverviewResponse(BaseModel):
    listing_id: Optional[str]
    active_signals: int
    high_signals: int
    medium_signals: int
    unresolved_signals: int
    total_not_evaluated: int
    last_audit_at: Optional[str]


@router.get("/legal/overview", response_model=LegalOverviewResponse)
async def legal_overview(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalOverviewResponse:
    uid = current_user.id
    live = (await db.execute(_scope(
        select(LegalSignal).where(LegalSignal.status.in_(_LIVE)),
        LegalSignal, uid, listing_id))).scalars().all()
    last_audit = (await db.execute(_scope(
        select(LegalAudit).order_by(LegalAudit.created_at.desc()).limit(1),
        LegalAudit, uid, listing_id))).scalars().first()
    return LegalOverviewResponse(
        listing_id=listing_id,
        active_signals=len(live),
        high_signals=sum(1 for s in live if s.priority_level in ("critical", "high")),
        medium_signals=sum(1 for s in live if s.priority_level == "medium"),
        unresolved_signals=len(live),
        total_not_evaluated=last_audit.total_not_evaluated if last_audit else 0,
        last_audit_at=last_audit.created_at.isoformat() if last_audit and last_audit.created_at else None,
    )


# ── GET /legal/audits (history) ──────────────────────────────────────────────

class LegalAuditView(BaseModel):
    audit_id: str
    status: str
    total_findings: int
    total_not_evaluated: int
    top_severity: Optional[str]
    triggered_by: Optional[str]
    created_at: Optional[str]


class LegalAuditsResponse(BaseModel):
    items: list[LegalAuditView]
    total: int


@router.get("/legal/audits", response_model=LegalAuditsResponse)
async def legal_audits(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalAuditsResponse:
    rows = (await db.execute(_scope(
        select(LegalAudit).order_by(LegalAudit.created_at.desc()).limit(max(1, min(limit, 200))),
        LegalAudit, current_user.id, listing_id))).scalars().all()
    items = [LegalAuditView(
        audit_id=a.id, status=a.status, total_findings=a.total_findings or 0,
        total_not_evaluated=a.total_not_evaluated or 0, top_severity=a.top_severity,
        triggered_by=a.triggered_by,
        created_at=a.created_at.isoformat() if a.created_at else None,
    ) for a in rows]
    return LegalAuditsResponse(items=items, total=len(items))


# ── GET /legal/findings (latest audit) ───────────────────────────────────────

class LegalFindingView(BaseModel):
    requirement_type: str
    category: Optional[str]
    severity: str
    risk_level: Optional[str]
    estimated_effect_type: Optional[str]
    evidence: Optional[dict]
    detected_at: Optional[str]


class LegalFindingsResponse(BaseModel):
    items: list[LegalFindingView]
    total: int


@router.get("/legal/findings", response_model=LegalFindingsResponse)
async def legal_findings(
    listing_id: Optional[str] = None,
    marketplace: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalFindingsResponse:
    uid = current_user.id
    last_audit_id = (await db.execute(_scope(
        select(LegalAudit.id).order_by(LegalAudit.created_at.desc()).limit(1),
        LegalAudit, uid, listing_id))).scalar()
    if not last_audit_id:
        return LegalFindingsResponse(items=[], total=0)
    rows = (await db.execute(select(LegalFinding).where(
        LegalFinding.audit_id == last_audit_id))).scalars().all()
    items = [LegalFindingView(
        requirement_type=f.requirement_type, category=f.category, severity=f.severity,
        risk_level=f.risk_level, estimated_effect_type=f.estimated_effect_type,
        evidence=_loads(f.evidence),
        detected_at=f.created_at.isoformat() if f.created_at else None,
    ) for f in rows]
    return LegalFindingsResponse(items=items, total=len(items))
