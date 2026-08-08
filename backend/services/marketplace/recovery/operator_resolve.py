"""SECURITY-2D-1C-C3B — atomic writer for a manual operator resolution + append-only audit.

Records ONLY a human conclusion about a disputed ExecutionLog: it writes exactly three denormalized
fields (manual_resolution / resolved_by / resolved_at) and INSERTs exactly one append-only
ExecutionRecoveryAudit row, atomically. It NEVER dispatches, NEVER calls the executor or a provider,
NEVER touches status / dispatch_started_at / claim_generation / attempt_count / reown_count /
reconciliation_* / idempotency_key / request_fingerprint / payload / result / reverted_from, and NEVER
writes the C3C-only retry resolution value or the C3C-only retry audit action.

No executor / provider / dispatch / credential imports (an AST guard test enforces this).

Atomicity + honest lost-ACK semantics (the audit row and the three denormalized fields live in ONE
PostgreSQL transaction, one commit):
  * A failure BEFORE the underlying commit (validation, audit flush, or a pre-commit error) rolls the
    whole transaction back — neither the audit row nor the projection change persists — and returns an
    'error' status (HTTP 503). Nothing is saved.
  * If the underlying commit SUCCEEDS but the acknowledgement is lost to the app (a post-commit error),
    the HTTP result may be 503, yet BOTH the audit row AND the projection change ARE persisted together.
    We do NOT (and cannot) promise a rollback after a completed commit. What is guaranteed is that the two
    can never diverge (one without the other is impossible), and that a retry with the same
    Idempotency-Key is safe: it returns the CACHED original outcome (reconstructed from the immutable audit
    row) without a second audit row or any further projection change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import AsyncSessionLocal
from models.execution_log import ExecutionLog
from models.execution_recovery_audit import ExecutionRecoveryAudit
# Canonical validators ONLY — no executor, no provider clients.
from services.marketplace.operation_key import is_valid_v1_key
from services.marketplace.request_fingerprint import is_valid_fingerprint
from services.marketplace.operation_fingerprint import compute_fingerprint

logger = logging.getLogger(__name__)

# Rows an operator may act on = the SAME disputed set the C3A read-only list surfaces.
_OPEN_STATUSES = frozenset({"pending", "in_flight", "ambiguous"})
_ATTENTION_RECON = frozenset({"target_not_observed", "still_unknown", "manual_attention"})

# action → (manual_resolution written, audit action, default reason_code, allowed reason_codes).
# NONE of these is a C3C-only retry value/action. All reason codes already exist in
# ExecutionRecoveryAudit._REASON_CODES.
_ACTIONS = {
    "confirm_applied": ("confirmed_applied", "confirm_applied", "operator_confirmed_applied",
                        frozenset({"operator_confirmed_applied"})),
    "confirm_not_applied": ("confirmed_not_applied", "confirm_not_applied",
                            "operator_confirmed_not_applied",
                            frozenset({"operator_confirmed_not_applied"})),
    "close": ("manual_closed", "close", "operator_closed_no_action",
              frozenset({"operator_closed_no_action", "stale_pending_review", "ambiguous_needs_review"})),
}
# confirm_applied / confirm_not_applied assert a technical fact → require a canonically-verified op.
_VERIFY_REQUIRED = frozenset({"confirm_applied", "confirm_not_applied"})


def allowed_reasons(action: str) -> frozenset:
    return _ACTIONS[action][3] if action in _ACTIONS else frozenset()


def default_reason(action: str) -> Optional[str]:
    return _ACTIONS[action][2] if action in _ACTIONS else None


@dataclass
class ResolveOutcome:
    status: str                 # ok | cached | not_found | not_open | unverified | mismatch | error
    log_id: Optional[str] = None
    audit: Optional[dict] = None   # immutable snapshot of the written/existing audit row


def _disputed(status: Optional[str], recon: Optional[str]) -> bool:
    return status in _OPEN_STATUSES or (recon in _ATTENTION_RECON)


def _audit_snapshot(a: ExecutionRecoveryAudit, result_code: str) -> dict:
    """Plain-scalar snapshot captured while the row is live — safe to return after the session closes."""
    return {
        "audit_id": a.id,
        "execution_log_id": a.execution_log_id,
        "action": a.action,
        "previous_status": a.previous_status,
        "previous_resolution": a.previous_resolution,
        "new_resolution": a.new_resolution,
        "reason_code": a.reason_code,
        "actor_id": a.actor_id,
        "correlation_id": a.correlation_id,
        "created_at": a.created_at,
        "result_code": result_code,
    }


async def resolve(*, log_id: str, tenant_user_id: str, action: str, actor_id: str,
                  correlation_id: str, reason_code: Optional[str], now: datetime) -> ResolveOutcome:
    """One atomic manual resolution. See module docstring for the invariants."""
    if action not in _ACTIONS:
        return ResolveOutcome(status="error")            # programming error — router restricts action
    new_resolution, audit_action, default_rc, allowed = _ACTIONS[action]
    rc = reason_code or default_rc
    if rc not in allowed:
        return ResolveOutcome(status="error")            # router validates first; defence in depth

    # ── Attempt 1: lock the row, validate, INSERT audit (idempotency claim), then update projection ──
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(ExecutionLog).where(
                    ExecutionLog.id == log_id, ExecutionLog.user_id == tenant_user_id
                ).with_for_update())).scalars().first()
            if row is None:
                return ResolveOutcome(status="not_found")
            if not _disputed(row.status, row.reconciliation_status):
                return ResolveOutcome(status="not_open")
            if action in _VERIFY_REQUIRED:
                ok = (is_valid_v1_key(row.idempotency_key)
                      and is_valid_fingerprint(row.request_fingerprint)
                      and compute_fingerprint(row.user_id, row.connection_id, row.marketplace,
                                              row.action_type, row.mode, row.payload or {},
                                              row.reverted_from) == row.request_fingerprint)
                if not ok:
                    return ResolveOutcome(status="unverified")
            prev_status = row.status
            prev_resolution = row.manual_resolution
            audit = ExecutionRecoveryAudit(
                execution_log_id=log_id, action=audit_action, previous_status=prev_status,
                previous_resolution=prev_resolution, new_resolution=new_resolution,
                actor_id=actor_id, reason_code=rc, correlation_id=correlation_id, created_at=now)
            db.add(audit)
            # Flush FIRST so a UNIQUE(correlation_id)/FK/CHECK violation surfaces BEFORE the projection is
            # touched — the projection can never change without a committed audit row.
            await db.flush()
            row.manual_resolution = new_resolution
            row.resolved_by = actor_id
            row.resolved_at = now
            snap = _audit_snapshot(audit, "APPLIED")     # capture scalars while live (no post-commit read)
            await db.commit()
            return ResolveOutcome(status="ok", log_id=log_id, audit=snap)
    except IntegrityError:
        # correlation_id already used (or the composite) → resolve idempotently in a FRESH session.
        # Do NOT read the expired ORM objects from the aborted transaction (C1 MissingGreenlet lesson).
        pass
    except SQLAlchemyError:
        logger.warning("operator resolve: infra error action=%s", audit_action)
        return ResolveOutcome(status="error")

    # ── Attempt 2 (idempotency resolution): the correlation_id already exists. Cached vs mismatch. ──
    try:
        async with AsyncSessionLocal() as db2:
            existing = (await db2.execute(
                select(ExecutionRecoveryAudit).where(
                    ExecutionRecoveryAudit.correlation_id == correlation_id))).scalars().first()
            if existing is None:
                return ResolveOutcome(status="error")    # raced away impossibly; fail closed
            # Tenant equivalence is proven through the LINKED ExecutionLog (audit carries no tenant_id).
            linked = (await db2.execute(
                select(ExecutionLog.user_id).where(
                    ExecutionLog.id == existing.execution_log_id))).scalars().first()
            equivalent = (linked is not None and linked == tenant_user_id
                          and existing.execution_log_id == log_id
                          and existing.action == audit_action
                          and existing.reason_code == rc
                          and existing.actor_id == actor_id)
            if equivalent:
                return ResolveOutcome(status="cached", log_id=existing.execution_log_id,
                                      audit=_audit_snapshot(existing, "CACHED"))
            return ResolveOutcome(status="mismatch")
    except SQLAlchemyError:
        logger.warning("operator resolve: idempotency-resolve infra error action=%s", audit_action)
        return ResolveOutcome(status="error")
