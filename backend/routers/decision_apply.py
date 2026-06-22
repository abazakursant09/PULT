"""
Decision Apply UX API (A4) — thin delegation to preview.py / confirm.py.

Contains NO business logic: GET preview → build_apply_preview, POST confirm →
confirm_and_apply_decision. Owner-scoped (a foreign decision → 404 via
decision_not_found). No direct executor/marketplace call, no Feed change, no score/
forecast/money. All gates (binding, capability, payload, guard, idempotency) live in
the existing services.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User

from services.decision_apply_ux.preview import build_apply_preview, ApplyPreview
from services.decision_apply_ux.confirm import confirm_and_apply_decision, ApplyConfirmResult

router = APIRouter()


# ── GET /decision-apply/preview/{decision_id} ────────────────────────────────

class PreviewResponse(BaseModel):
    applyable: bool
    decision_id: str
    action_key: Optional[str]
    payload: Optional[dict]
    capability_ok: bool
    payload_status: Optional[str]
    safety_class: Optional[str]
    dry_run_status: Optional[str]
    reason: Optional[str]
    marketplace: Optional[str]
    sku: Optional[str]


def _preview_view(p: ApplyPreview) -> PreviewResponse:
    return PreviewResponse(
        applyable=p.applyable, decision_id=p.decision_id, action_key=p.action_key,
        payload=dict(p.payload) if p.payload else None, capability_ok=p.capability_ok,
        payload_status=p.payload_status, safety_class=p.safety_class,
        dry_run_status=p.dry_run_status, reason=p.reason, marketplace=p.marketplace, sku=p.sku)


@router.get("/decision-apply/preview/{decision_id}", response_model=PreviewResponse)
async def decision_apply_preview(
    decision_id: str,
    marketplace: str,
    sku: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    p = await build_apply_preview(db, user_id=current_user.id, decision_id=decision_id,
                                  marketplace=marketplace, sku=sku)
    if p.reason == "decision_not_found":
        raise HTTPException(status_code=404, detail="decision not found")
    return _preview_view(p)


# ── POST /decision-apply/confirm/{decision_id} ───────────────────────────────

class ConfirmRequest(BaseModel):
    marketplace: str
    sku: Optional[str] = None
    idempotency_key: str


class ConfirmResponse(BaseModel):
    ok: bool
    decision_id: str
    action_key: Optional[str]
    execution_log_id: Optional[str]
    status: str
    reason: Optional[str]
    measurement_opened: bool
    intent_id: Optional[str]


def _confirm_view(r: ApplyConfirmResult) -> ConfirmResponse:
    return ConfirmResponse(
        ok=r.ok, decision_id=r.decision_id, action_key=r.action_key,
        execution_log_id=r.execution_log_id, status=r.status, reason=r.reason,
        measurement_opened=r.measurement_opened, intent_id=r.intent_id)


@router.post("/decision-apply/confirm/{decision_id}", response_model=ConfirmResponse)
async def decision_apply_confirm(
    decision_id: str,
    body: ConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConfirmResponse:
    res = await confirm_and_apply_decision(
        db, user_id=current_user.id, decision_id=decision_id, marketplace=body.marketplace,
        sku=body.sku, idempotency_key=body.idempotency_key)
    if res.reason == "decision_not_found":
        raise HTTPException(status_code=404, detail="decision not found")
    return _confirm_view(res)
