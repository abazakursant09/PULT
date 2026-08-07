"""SECURITY-2D-1C-C3A — READ-ONLY internal operator recovery API.

Two GET endpoints let a single configured operator LIST and READ disputed ExecutionLog rows. Strictly
read-only: no ExecutionLog mutation, no execution_recovery_audit write, no executor / provider / dispatch
call (an AST guard test enforces the import ban). This router is NOT mounted under the seller cookie-auth
dependencies — its only credential is a personal machine-to-machine key, separate from the shared
internal_api_key, and the tenant + actor are taken ONLY from server-side config.

Perimeter (see _require_operator):
  * recovery_operator_enabled is not True  → neutral 404 BEFORE any DB work (endpoint "does not exist").
  * any of key / operator id / tenant user id unset, or a wrong/missing X-Internal-Key → 403, 0 DB query.
  * cookie / Bearer / seller session grant NOTHING here.
The operator key is never echoed, logged, or sent to Sentry. Responses are no-store.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import and_, or_, select

from config import settings
from database import AsyncSessionLocal
from models.execution_log import ExecutionLog
from services.marketplace.recovery import operator_view

router = APIRouter()

# Disputed = still-open executor states OR a reconciliation classification that needs a human look.
_OPEN_STATUSES = ("pending", "in_flight", "ambiguous")
_ATTENTION_RECON = ("target_not_observed", "still_unknown", "manual_attention")

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


@dataclass
class _OperatorContext:
    operator_id: str
    user_id: str


def _require_operator(x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key")
                      ) -> _OperatorContext:
    """Fail-closed operator perimeter. Runs BEFORE any DB session is opened."""
    # Feature off → the endpoint is invisible. Neutral 404, no auth hint, no DB.
    if settings.recovery_operator_enabled is not True:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    key = settings.recovery_operator_api_key
    oid = settings.recovery_operator_id
    uid = settings.recovery_operator_user_id
    # Any unset perimeter setting → reject everyone (never fail open).
    if not key or not oid or not uid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="internal access required")
    if not x_internal_key or not hmac.compare_digest(x_internal_key, key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="internal access required")
    return _OperatorContext(operator_id=oid, user_id=uid)


# ── strict allowlist response schemas (built field-by-field; the ORM row is NEVER model_dumped) ──────
class _ReconciliationView(BaseModel):
    status: Optional[str] = None
    attempts: int = 0
    last_reconciled_at: Optional[datetime] = None
    next_reconcile_at: Optional[datetime] = None


class OperatorOperationView(BaseModel):
    log_id: str
    marketplace: Optional[str] = None
    action_type: str
    mode: str
    status: str
    manual_resolution: Optional[str] = None
    reconciliation: _ReconciliationView
    created_at: Optional[datetime] = None
    dispatch_started_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    last_reowned_at: Optional[datetime] = None
    attempt_count: int = 0
    reown_count: int = 0
    reverted: bool = False
    supported_for_retry: bool = False
    reason_code: str
    target_reference: Optional[str] = None


class _Cursor(BaseModel):
    created_at: Optional[datetime] = None
    log_id: Optional[str] = None


class OperatorOperationList(BaseModel):
    items: list[OperatorOperationView]
    next_cursor: Optional[_Cursor] = None


def _project(row) -> OperatorOperationView:
    """Map an ExecutionLog row to the allowlisted view. ONLY the whitelisted attributes are read; nothing
    else (user_id, connection_id, idempotency_key, request_fingerprint, payload, api_request_id, result,
    error_code) can reach the response, because they are never referenced here."""
    supported, reason = operator_view.evaluate_retry(row)
    return OperatorOperationView(
        log_id=row.id,
        marketplace=row.marketplace,
        action_type=row.action_type,
        mode=row.mode,
        status=row.status,
        manual_resolution=row.manual_resolution,
        reconciliation=_ReconciliationView(
            status=row.reconciliation_status,
            attempts=row.reconciliation_attempts or 0,
            last_reconciled_at=row.last_reconciled_at,
            next_reconcile_at=row.next_reconcile_at,
        ),
        created_at=row.created_at,
        dispatch_started_at=row.dispatch_started_at,
        last_attempt_at=row.last_attempt_at,
        last_reowned_at=row.last_reowned_at,
        attempt_count=row.attempt_count or 0,
        reown_count=row.reown_count or 0,
        reverted=row.reverted_from is not None,
        supported_for_retry=supported,
        reason_code=reason,
        target_reference=operator_view.target_reference(row),
    )


def _disputed_where(user_id: str):
    return and_(
        ExecutionLog.user_id == user_id,
        or_(
            ExecutionLog.status.in_(_OPEN_STATUSES),
            ExecutionLog.reconciliation_status.in_(_ATTENTION_RECON),
        ),
    )


@router.get("/operations", response_model=OperatorOperationList)
async def list_operations(
    response: Response,
    ctx: _OperatorContext = Depends(_require_operator),
    cursor_created: Optional[datetime] = Query(default=None),
    cursor_id: Optional[str] = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
):
    """List disputed operations for THIS operator's server-side tenant. Keyset pagination on
    (created_at, id). The tenant is forced from config — no user_id/tenant is accepted from the client."""
    response.headers["Cache-Control"] = "no-store"
    async with AsyncSessionLocal() as db:
        q = select(ExecutionLog).where(_disputed_where(ctx.user_id))
        if cursor_created is not None and cursor_id is not None:
            q = q.where(or_(
                ExecutionLog.created_at > cursor_created,
                and_(ExecutionLog.created_at == cursor_created, ExecutionLog.id > cursor_id),
            ))
        q = q.order_by(ExecutionLog.created_at, ExecutionLog.id).limit(limit)
        rows = (await db.execute(q)).scalars().all()
    items = [_project(r) for r in rows]
    next_cursor = None
    if len(rows) == limit:
        last = rows[-1]
        next_cursor = _Cursor(created_at=last.created_at, log_id=last.id)
    return OperatorOperationList(items=items, next_cursor=next_cursor)


@router.get("/operations/{log_id}", response_model=OperatorOperationView)
async def get_operation(
    log_id: str,
    response: Response,
    ctx: _OperatorContext = Depends(_require_operator),
):
    """Detail of one operation, scoped to the operator's server-side tenant. A log id that does not exist
    OR belongs to another tenant returns the SAME neutral 404 (no cross-tenant existence oracle)."""
    response.headers["Cache-Control"] = "no-store"
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ExecutionLog).where(
            ExecutionLog.id == log_id, ExecutionLog.user_id == ctx.user_id))).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return _project(row)
