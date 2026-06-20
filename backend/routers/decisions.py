"""
Decisions apply endpoint (Slice D) — narrow exposure of the apply bridge.

Thin wrapper over services.decision_apply.apply_decision. No new business logic,
no measurement close, no causal claim. Measurement opening is NOT exposed here:
it needs a marketplace token, and tokens are never accepted over HTTP — that
wiring (server-side credential resolution) is a later slice. This endpoint
applies the Decision only.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from services import decision_apply
from services.measurement_close_bridge import close_due_measurements

router = APIRouter()


class ApplyDecisionRequest(BaseModel):
    overrides: dict[str, Any]
    mode: str = "manual_l3"
    connection_id: str | None = None
    idempotency_key: str | None = None
    dry_run: bool = False


class ApplyDecisionResponse(BaseModel):
    ok: bool
    decision_id: str
    execution_log_id: str | None = None
    status: str
    reason: str | None = None
    decision_outcome_id: str | None = None


@router.post("/decisions/{decision_id}/apply", response_model=ApplyDecisionResponse)
async def apply_decision_endpoint(
    decision_id: str,
    body: ApplyDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApplyDecisionResponse:
    res = await decision_apply.apply_decision(
        db=db,
        user_id=current_user.id,
        decision_id=decision_id,
        overrides=body.overrides,
        mode=body.mode,
        connection_id=body.connection_id,
        idempotency_key=body.idempotency_key,
        dry_run=body.dry_run,
    )
    return ApplyDecisionResponse(
        ok=res.ok,
        decision_id=res.decision_id,
        execution_log_id=res.execution_log_id,
        status=res.status,
        reason=res.reason,
        decision_outcome_id=res.decision_outcome_id,
    )


class CloseDueResponse(BaseModel):
    total_due: int
    confirmed: int
    refuted: int
    insufficient: int
    skipped: int
    errors: int


@router.post("/decisions/measurements/close-due", response_model=CloseDueResponse)
async def close_due_measurements_endpoint(
    limit: int | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CloseDueResponse:
    """
    G1 runtime trigger — close every due still_open measurement (the Learning OS
    write-side activation). Thin wrapper over the already-tested
    close_due_measurements service: it closes outcomes, appends DecisionMemory,
    and opens refuted follow-ups via the existing close bridge. No new business
    logic. Idempotent — re-running closes nothing already closed. Intended to be
    invoked periodically (deploy cron / scheduled job hitting this endpoint).
    """
    summary = await close_due_measurements(db, limit=limit)
    return CloseDueResponse(
        total_due=summary.total_due,
        confirmed=summary.confirmed,
        refuted=summary.refuted,
        insufficient=summary.insufficient,
        skipped=summary.skipped,
        errors=summary.errors,
    )
