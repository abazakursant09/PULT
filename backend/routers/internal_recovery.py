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
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select

from config import settings
from database import AsyncSessionLocal
from models.execution_log import ExecutionLog
from services.marketplace.operation_key import canonical_client_uuid, OperationKeyError
from services.marketplace.recovery import operator_view, operator_resolve, operator_resume

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


def _require_operator_redispatch(x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key")
                                 ) -> _OperatorContext:
    """SECURITY-2D-1C-C3C2 — perimeter for the authorize-and-resume endpoint (the only provider-write
    contour). BOTH master switches must be on, checked BEFORE any DB work: recovery_operator_enabled AND
    recovery_redispatch_enabled. Either off → neutral 404 (0 DB / 0 token decrypt / 0 audit / 0 provider).
    Then the same fail-closed operator-key check as the read-only perimeter. The automated_l4 automation
    gate is row-specific and enforced inside resume()/evaluate_resume (a row's mode is not known here)."""
    if settings.recovery_operator_enabled is not True:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if settings.recovery_redispatch_enabled is not True:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return _require_operator(x_internal_key)


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


# ── SECURITY-2D-1C-C3B — manual operator resolution (POST) ────────────────────────────────────────────
class ResolveBody(BaseModel):
    """Optional body — ONLY an allowlisted reason_code. No manual_resolution / action / correlation_id /
    actor_id / user_id / free comment; unknown fields are rejected."""
    model_config = ConfigDict(extra="forbid")
    reason_code: Optional[str] = None


class _AuditOutcome(BaseModel):
    audit_id: str
    execution_log_id: str
    action: str
    previous_status: Optional[str] = None
    previous_resolution: Optional[str] = None
    new_resolution: Optional[str] = None
    reason_code: str
    actor_id: str
    correlation_id: str
    created_at: Optional[datetime] = None
    result_code: str            # APPLIED | CACHED


class ResolveResponse(BaseModel):
    # The idempotent record of THIS request (reconstructed from the immutable audit row — never faked from
    # the current denormalized resolution, which a later correction may already have changed).
    idempotent_result: _AuditOutcome
    # The current safe read-only projection of the operation (may reflect a later correction).
    current_operation: OperatorOperationView


async def _current_projection(log_id: str, tenant_user_id: str) -> Optional[OperatorOperationView]:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ExecutionLog).where(
            ExecutionLog.id == log_id, ExecutionLog.user_id == tenant_user_id))).scalars().first()
    return _project(row) if row is not None else None


async def _do_resolve(action: str, log_id: str, ctx: _OperatorContext,
                      idem_key: Optional[str], body: Optional[ResolveBody]) -> ResolveResponse:
    # Mandatory canonical UUIDv4 Idempotency-Key (header only; never body/server-generated/target_reference).
    try:
        correlation_id = canonical_client_uuid(idem_key)
    except OperationKeyError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Idempotency-Key must be a canonical lowercase UUIDv4")
    # Optional reason_code — only from THIS action's allowlist (client can't use another action's reason).
    reason_code = body.reason_code if body is not None else None
    if reason_code is not None and reason_code not in operator_resolve.allowed_reasons(action):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid reason_code")

    out = await operator_resolve.resolve(
        log_id=log_id, tenant_user_id=ctx.user_id, action=action, actor_id=ctx.operator_id,
        correlation_id=correlation_id, reason_code=reason_code, now=datetime.now(timezone.utc))

    if out.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if out.status == "not_open":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="OPERATION_NOT_OPEN_FOR_MANUAL_RESOLUTION")
    if out.status == "unverified":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="UNVERIFIED_OPERATION_ONLY_CLOSE_ALLOWED")
    if out.status == "mismatch":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="IDEMPOTENCY_MISMATCH")
    if out.status not in ("ok", "cached") or out.audit is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="could not record resolution — retry later")
    current = await _current_projection(out.log_id, ctx.user_id)
    if current is None:                                   # row vanished between commit and re-read
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return ResolveResponse(idempotent_result=_AuditOutcome(**out.audit), current_operation=current)


@router.post("/operations/{log_id}/confirm-applied", response_model=ResolveResponse)
async def confirm_applied(log_id: str, response: Response,
                          ctx: _OperatorContext = Depends(_require_operator),
                          idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
                          body: Optional[ResolveBody] = Body(default=None)):
    response.headers["Cache-Control"] = "no-store"
    return await _do_resolve("confirm_applied", log_id, ctx, idem_key, body)


@router.post("/operations/{log_id}/confirm-not-applied", response_model=ResolveResponse)
async def confirm_not_applied(log_id: str, response: Response,
                              ctx: _OperatorContext = Depends(_require_operator),
                              idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
                              body: Optional[ResolveBody] = Body(default=None)):
    response.headers["Cache-Control"] = "no-store"
    return await _do_resolve("confirm_not_applied", log_id, ctx, idem_key, body)


@router.post("/operations/{log_id}/close", response_model=ResolveResponse)
async def close_operation(log_id: str, response: Response,
                          ctx: _OperatorContext = Depends(_require_operator),
                          idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
                          body: Optional[ResolveBody] = Body(default=None)):
    response.headers["Cache-Control"] = "no-store"
    return await _do_resolve("close", log_id, ctx, idem_key, body)


# ── SECURITY-2D-1C-C3C2 — authorize-and-resume (the ONLY provider-write contour) ──────────────────────
_AUTHORIZE_REASONS = frozenset({"operator_authorized_retry"})


class AuthorizeResumeResponse(BaseModel):
    # The idempotent record of THIS authorize request (from the immutable audit row).
    idempotent_result: _AuditOutcome
    # Whether this request actually attempted a single provider dispatch (False on cached/conflict).
    dispatch_attempted: bool
    # Terminal classification of the dispatch, if attempted (success | failed | ambiguous | ...).
    terminal_status: Optional[str] = None
    # Current safe read-only projection of the operation.
    current_operation: OperatorOperationView


@router.post("/operations/{log_id}/authorize-retry", response_model=AuthorizeResumeResponse)
async def authorize_retry(log_id: str, response: Response,
                          ctx: _OperatorContext = Depends(_require_operator_redispatch),
                          idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
                          body: Optional[ResolveBody] = Body(default=None)):
    response.headers["Cache-Control"] = "no-store"
    try:
        correlation_id = canonical_client_uuid(idem_key)
    except OperationKeyError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Idempotency-Key must be a canonical lowercase UUIDv4")
    reason_code = body.reason_code if body is not None else None
    if reason_code is not None and reason_code not in _AUTHORIZE_REASONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid reason_code")

    out = await operator_resume.resume(
        log_id=log_id, tenant_user_id=ctx.user_id, actor_id=ctx.operator_id,
        correlation_id=correlation_id, reason_code=reason_code, now=datetime.now(timezone.utc))

    if out.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if out.status == "conflict":
        # Neutral 409 — the safe reason code never reveals which specific gate was lost.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=(out.reason_code or "conflict"))
    if out.status not in ("dispatched", "cached") or out.audit is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="could not authorize resume — retry later")
    current = await _current_projection(out.log_id, ctx.user_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return AuthorizeResumeResponse(idempotent_result=_AuditOutcome(**out.audit),
                                   dispatch_attempted=out.dispatch_attempted,
                                   terminal_status=out.terminal_status, current_operation=current)
