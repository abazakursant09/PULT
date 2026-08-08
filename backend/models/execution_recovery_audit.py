"""SECURITY-2D-1C-C3A — append-only operator recovery audit (schema foundation only).

An immutable trail of every OPERATOR action taken on a disputed ExecutionLog (confirm applied / confirm
not applied / close / authorize retry). C3A only ships the schema + the model — NOTHING writes it yet
(the read-only operator view only READS ExecutionLog; the writer arrives in C3B). Append-only: written
once, never rewritten — no `updated_at`.

Deliberately PII-free and secret-free: it stores NO payload, NO idempotency key, NO request fingerprint,
NO provider id, NO email/IP, NO free text — only the transition (status/resolution), a server-side actor
id, an enum reason_code, and a correlation id (the operator action's idempotency key). The FK to
execution_logs is ON DELETE RESTRICT: an ExecutionLog is itself append-only and must never be deleted, so
RESTRICT turns an accidental delete into an error instead of silently destroying the audit history that a
CASCADE would erase.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Index, CheckConstraint, ForeignKey, UniqueConstraint,
)
from database import Base

# The operator actions this trail may record (future C3B/C3C producers). Enum-checked in the DB.
_AUDIT_ACTIONS = ("confirm_applied", "confirm_not_applied", "close", "authorize_retry")
_AUDIT_ACTION_IN = ", ".join(f"'{a}'" for a in _AUDIT_ACTIONS)

# The small, explicit, closed set of machine reason codes (NEVER free text). Longest below is 30 chars →
# the column is String(40). Kept as a frozen constant so the model CHECK, the migration CHECK and the
# tests share ONE source of truth.
_REASON_CODES = (
    "operator_confirmed_applied",
    "operator_confirmed_not_applied",
    "operator_closed_no_action",
    "operator_authorized_retry",
    "stale_pending_review",
    "ambiguous_needs_review",
)
_REASON_CODE_IN = ", ".join(f"'{r}'" for r in _REASON_CODES)


class ExecutionRecoveryAudit(Base):
    __tablename__ = "execution_recovery_audit"

    id               = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    execution_log_id = Column(String(36),
                              ForeignKey("execution_logs.id", ondelete="RESTRICT"), nullable=False)
    action           = Column(String(24), nullable=False)
    previous_status     = Column(String(20), nullable=True)
    previous_resolution = Column(String(24), nullable=True)
    new_resolution      = Column(String(24), nullable=True)
    actor_id         = Column(String(64), nullable=False)   # from server-side config, NEVER a client header
    reason_code      = Column(String(40), nullable=False)
    correlation_id   = Column(String(36), nullable=False)   # the operator action's idempotency key
    created_at       = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # One audit row per (operation, operator-idempotency-key, action): a retried operator request with
        # the same correlation id + action is idempotent (no duplicate audit row).
        UniqueConstraint("execution_log_id", "correlation_id", "action",
                         name="uq_recovery_audit_op_corr_action"),
        # SECURITY-2D-1C-C3B — GLOBAL idempotency: one operator Idempotency-Key (correlation_id) identifies
        # exactly ONE logical request across the whole table. The composite above cannot detect the same
        # key reused on a DIFFERENT log/action; this global UNIQUE makes such reuse a 409 mismatch instead
        # of a silent second row. A genuine new intent always mints a fresh UUIDv4, so this never blocks
        # legitimate work (a correction is a new key → a new audit row).
        UniqueConstraint("correlation_id", name="uq_recovery_audit_correlation"),
        Index("ix_recovery_audit_execlog", "execution_log_id"),
        CheckConstraint(f"action IN ({_AUDIT_ACTION_IN})", name="ck_recovery_audit_action"),
        CheckConstraint(f"reason_code IN ({_REASON_CODE_IN})", name="ck_recovery_audit_reason_code"),
    )
