"""SECURITY-2D-1C-C3C2 — atomic operator authorize-and-resume of a stuck SAFE pending operation.

This is the FIRST recovery contour that can reach a real provider write. Flow (feature OFF by default —
gated in the router by recovery_operator_enabled AND recovery_redispatch_enabled, plus automation_enabled
for automated_l4):

  operator authorize  →  live re-check (DB reads + token decrypt, NO network)  →  ONE atomic pre-dispatch
  transaction {INSERT authorize_retry audit + guarded _RESUME_CAS pending→in_flight+retry_authorized}
  →  single provider dispatch via the shared executor._dispatch_and_finalize (the ONE dispatch call-site).

Guarantees:
  * at-most-one LOCAL provider dispatch per existing operation claim (never marketplace exactly-once);
  * the audit row and the fencing/resolution are committed together (one transaction) — they can never
    diverge, and retry_authorized is never committed while status is still pending;
  * a lost ACK after a real commit leaves the row in_flight + one audit row → a retry returns CACHED and
    dispatches nothing; a failure before the real commit rolls back everything and dispatches nothing;
  * C2 re-own / a concurrent authorize / an old generation → _RESUME_CAS RETURNING empty → 0 dispatch.

The ordinary executor.execute path and its own fencing CAS are NOT touched. resume() calls the shared
helper; it never calls a provider client directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text, bindparam
from sqlalchemy import DateTime as _SA_DateTime, Integer as _SA_Integer, String as _SA_String
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import AsyncSessionLocal
from models.execution_log import ExecutionLog
from models.execution_recovery_audit import ExecutionRecoveryAudit
from services.marketplace import action_catalog
from services.marketplace.errors import ExecutionError
from services.marketplace.recovery import resume_eligibility
# Reuse the executor's dispatch context helpers + the SINGLE shared dispatch/finalize point. resume() adds
# NO second provider-dispatch call-site (a guard test enforces exactly one, inside the shared helper).
from services.marketplace.executor import (
    _resolve_connection, _resolve_token, _account_ref, capability_for_action, _canon_mp,
    _dispatch_and_finalize,
)

logger = logging.getLogger(__name__)

_REVERT_PREFIX = "v1:revert:"

# Fixed C3C2 audit vocabulary (all already allowed by the C3A/C3B enums + C3B global UNIQUE(correlation_id)).
_AUDIT_ACTION = "authorize_retry"
_AUDIT_REASON = "operator_authorized_retry"
_NEW_RESOLUTION = "retry_authorized"

# Separate resume fencing CAS (the ordinary execute() fencing CAS is left untouched). It re-pins the FULL
# safe-pending contract atomically: tenant, un-dispatched pending claim, attempt_count still 0, the exact
# generation this request owns, the exact operation key + fingerprint it validated, the re-own bound, and a
# compatible manual_resolution — and it authorises (retry_authorized + resolved_by/at) in the same UPDATE.
# RETURNING empty → we lost a gate (C2 re-own, a concurrent winner, a stale generation) → 0 dispatch.
_RESUME_CAS = text(
    "UPDATE execution_logs "
    "SET status='in_flight', dispatch_started_at=:now, "
    "    attempt_count=attempt_count+1, last_attempt_at=:now, "
    "    manual_resolution='retry_authorized', resolved_by=:actor, resolved_at=:now "
    "WHERE id=:id AND user_id=:uid "
    "  AND status='pending' AND dispatch_started_at IS NULL AND attempt_count=0 "
    "  AND claim_generation=:gen "
    "  AND idempotency_key=:seen_key AND request_fingerprint=:seen_fp "
    "  AND reown_count < :max_reowns "
    "  AND (manual_resolution IS NULL OR manual_resolution IN ('confirmed_not_applied','manual_closed')) "
    "RETURNING id, claim_generation, attempt_count, dispatch_started_at"
).bindparams(
    bindparam("now", type_=_SA_DateTime(timezone=True)),
    bindparam("gen", type_=_SA_Integer()),
    bindparam("max_reowns", type_=_SA_Integer()),
    bindparam("id", type_=_SA_String()),
)


@dataclass
class ResumeResult:
    status: str                     # dispatched | cached | not_found | conflict | error
    reason_code: Optional[str] = None
    log_id: Optional[str] = None
    dispatch_attempted: bool = False
    terminal_status: Optional[str] = None   # success | failed | ambiguous | needs_reconcile | ...
    audit: Optional[dict] = None


def _audit_snapshot(a: ExecutionRecoveryAudit, result_code: str) -> dict:
    return {
        "audit_id": a.id, "execution_log_id": a.execution_log_id, "action": a.action,
        "previous_status": a.previous_status, "previous_resolution": a.previous_resolution,
        "new_resolution": a.new_resolution, "reason_code": a.reason_code, "actor_id": a.actor_id,
        "correlation_id": a.correlation_id, "created_at": a.created_at, "result_code": result_code,
    }


def _revert_original_id(idempotency_key: Optional[str]) -> Optional[str]:
    key = idempotency_key or ""
    return key[len(_REVERT_PREFIX):] if key.startswith(_REVERT_PREFIX) else None


async def resume(*, log_id: str, tenant_user_id: str, actor_id: str, correlation_id: str,
                 reason_code: Optional[str], now: datetime) -> ResumeResult:
    """One atomic authorize-and-resume. Returns a ResumeResult; the router maps it to a safe HTTP shape.
    reason_code (if provided by the client) must already be validated by the router against the
    authorize-retry allowlist ({operator_authorized_retry})."""
    rc = reason_code or _AUDIT_REASON
    if rc != _AUDIT_REASON:
        return ResumeResult(status="error", reason_code="invalid_reason")

    # Idempotency FIRST: if this correlation_id already produced an audit row (a prior authorize that
    # committed — possibly with a lost ACK), return the CACHED outcome from the immutable audit and
    # dispatch NOTHING, regardless of the row's current state. Eligibility is only re-checked for a
    # genuinely NEW request. (A concurrent first-time race is still caught by the INSERT UNIQUE below.)
    pre = await _resolve_correlation(log_id, tenant_user_id, actor_id, rc, correlation_id)
    if pre is not None:
        return pre

    # ── Pre-dispatch transaction: lock, live re-check, prepare context, INSERT audit, guarded CAS, commit.
    try:
        async with AsyncSessionLocal() as db:
            rec = (await db.execute(select(ExecutionLog).where(
                ExecutionLog.id == log_id, ExecutionLog.user_id == tenant_user_id
            ).with_for_update())).scalars().first()
            if rec is None:
                return ResumeResult(status="not_found")

            # Preliminary + live read-only eligibility (connection/credential/scope/capability/guard/
            # automation/structural/attempt_count==0/fingerprint recompute/revert-original). No network.
            elig = await resume_eligibility.evaluate_resume(db, rec, tenant_user_id=tenant_user_id,
                                                            live=True)
            if not elig.eligible:
                return ResumeResult(status="conflict", reason_code=elig.reason_code, log_id=log_id)

            # Prepare the dispatch context + token (DB read + local Fernet decrypt; NO network). Ozon
            # campaign_control resolves its Performance bearer INSIDE spec.dispatch (token stays None here).
            spec = action_catalog.get(rec.action_type)
            conn = await _resolve_connection(db, tenant_user_id, rec.marketplace, rec.connection_id)
            ozon_perf = (capability_for_action(rec.action_type) == "campaign_control"
                         and _canon_mp(conn.marketplace) == "ozon")
            try:
                account_ref = await _account_ref(db, conn.id, spec.required_scope)
                token = None if ozon_perf else await _resolve_token(db, conn.id, spec.required_scope)
            except (ExecutionError, ValueError):
                # Missing credential/scope (ExecutionError) or a decrypt/tamper failure (ValueError) →
                # safe conflict, no audit / no CAS / no provider. Never leak the secret or the reason text.
                return ResumeResult(status="conflict", reason_code="credential_missing", log_id=log_id)

            owned_generation = rec.claim_generation
            prev_status = rec.status
            prev_resolution = rec.manual_resolution
            reverted_from = _revert_original_id(rec.idempotency_key)

            # INSERT the authorize_retry audit (idempotency claim on the global UNIQUE(correlation_id)) and
            # flush FIRST — a duplicate/constraint error surfaces BEFORE the fencing UPDATE, so the row is
            # never left in_flight without a committed audit.
            audit = ExecutionRecoveryAudit(
                execution_log_id=log_id, action=_AUDIT_ACTION, previous_status=prev_status,
                previous_resolution=prev_resolution, new_resolution=_NEW_RESOLUTION,
                actor_id=actor_id, reason_code=rc, correlation_id=correlation_id, created_at=now)
            db.add(audit)
            await db.flush()

            fenced = (await db.execute(_RESUME_CAS, {
                "id": log_id, "uid": tenant_user_id, "now": now, "gen": owned_generation,
                "actor": actor_id, "seen_key": rec.idempotency_key, "seen_fp": rec.request_fingerprint,
                "max_reowns": settings_max_reowns()})).first()
            if fenced is None:
                # Lost a gate (C2 re-own / concurrent winner / stale generation / changed manual state).
                await db.rollback()
                return ResumeResult(status="conflict", reason_code="operation_in_progress", log_id=log_id)

            await db.commit()                       # audit + fence + retry_authorized land together.
            audit_snap = _audit_snapshot(audit, "APPLIED")

            # ── After the pre-dispatch commit: ZERO DB/network/token/capability work before the single
            # provider dispatch. _dispatch_and_finalize syncs the ORM to the committed CAS state and makes
            # exactly one spec.dispatch call, then writes the terminal state in its own commit.
            ctx = {"marketplace": conn.marketplace, "ozon_client_id": conn.ozon_client_id,
                   "db": db, "connection_id": conn.id, "account_ref": account_ref}
            res = await _dispatch_and_finalize(
                db, rec, spec, token, rec.payload, ctx, reverted_from=reverted_from,
                user_id=tenant_user_id, action_type=rec.action_type, target_mp=conn.marketplace,
                mode=rec.mode, now=now, fenced_attempt_count=fenced.attempt_count)
            return ResumeResult(status="dispatched", log_id=log_id, dispatch_attempted=True,
                                terminal_status=res.status, audit=audit_snap)
    except IntegrityError:
        # correlation_id already used (a concurrent first-time race) → resolve idempotently in a FRESH
        # session. NEVER dispatch here. Do not read the expired ORM objects from the aborted transaction
        # (the C1 MissingGreenlet lesson).
        pass
    except SQLAlchemyError:
        logger.warning("operator resume: pre-dispatch infra error action=%s", _AUDIT_ACTION)
        return ResumeResult(status="error", reason_code="infra_error")

    resolved = await _resolve_correlation(log_id, tenant_user_id, actor_id, rc, correlation_id)
    return resolved if resolved is not None else ResumeResult(status="error", reason_code="infra_error")


async def _resolve_correlation(log_id: str, tenant_user_id: str, actor_id: str, rc: str,
                               correlation_id: str) -> Optional[ResumeResult]:
    """Idempotency resolution (cached vs mismatch) in a FRESH session — NEVER dispatches. Returns a
    ResumeResult when an audit row for this correlation_id already exists (same tuple → CACHED from the
    immutable audit; any difference → 409 idempotency_mismatch), else None (a genuinely new request)."""
    try:
        async with AsyncSessionLocal() as db2:
            existing = (await db2.execute(select(ExecutionRecoveryAudit).where(
                ExecutionRecoveryAudit.correlation_id == correlation_id))).scalars().first()
            if existing is None:
                return None
            linked = (await db2.execute(select(ExecutionLog.user_id).where(
                ExecutionLog.id == existing.execution_log_id))).scalars().first()
            equivalent = (linked is not None and linked == tenant_user_id
                          and existing.execution_log_id == log_id
                          and existing.action == _AUDIT_ACTION
                          and existing.reason_code == rc
                          and existing.actor_id == actor_id)
            if equivalent:
                return ResumeResult(status="cached", log_id=existing.execution_log_id,
                                    dispatch_attempted=False, audit=_audit_snapshot(existing, "CACHED"))
            return ResumeResult(status="conflict", reason_code="idempotency_mismatch")
    except SQLAlchemyError:
        logger.warning("operator resume: idempotency-resolve infra error action=%s", _AUDIT_ACTION)
        return ResumeResult(status="error", reason_code="infra_error")


def settings_max_reowns() -> int:
    from config import settings
    return settings.recovery_max_reowns
