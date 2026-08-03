import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, JSON, Index, Integer, CheckConstraint, text
from database import Base

# SECURITY-2D-1C-A/1C-B — the only allowed reconciliation_status values (recovery classification).
# intent_observed = the target end-state is observed NOW (NOT proof PULT made the change — no attribution).
# target_not_observed = the target end-state is NOT observed now. This is a CURRENT-STATE MISMATCH and is
#   NOT proof the original operation was never applied (may have applied then drifted, or the read lags).
#   It may only lead to manual_attention / still_unknown and NEVER by itself authorises a retry or a
#   provider write. (Renamed from the dangerous "not_observed"; a current price/status read is NOT a
#   per-operation "not applied" proof.)
_RECON_STATUSES = (
    "pending_recon", "reconciling", "intent_observed", "target_not_observed",
    "still_unknown", "manual_attention", "resolved",
)
_RECON_IN = ", ".join(f"'{s}'" for s in _RECON_STATUSES)


class ExecutionLog(Base):
    """
    Append-only audit of every marketplace action attempted through the
    executor. Borrows the append-only posture of the operational_review layer,
    but — unlike that layer — this one HAS execution authority by design.
    """

    __tablename__ = "execution_logs"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String(36), nullable=False)
    connection_id  = Column(String(36), nullable=True)
    insight_key    = Column(String(200), nullable=True)       # provenance: triggering insight
    decision_id    = Column(String(36), nullable=True)        # provenance: applied decision (soft ref, no hard FK — mirrors insight_key)
    action_type    = Column(String(60), nullable=False)
    marketplace    = Column(String(20), nullable=True)
    mode           = Column(String(20), nullable=False)        # manual_l3 | automated_l4
    payload        = Column(JSON, nullable=False, default=dict) # secrets stripped
    api_request_id = Column(String(120), nullable=True)
    status         = Column(String(20), nullable=False, default="pending")  # pending|in_flight|success|failed|ambiguous|rejected|reverted (2D-1B-B: in_flight = claimed, dispatch_started_at set)
    result         = Column(JSON, nullable=True)
    error_code     = Column(String(60), nullable=True)
    reverted_from  = Column(String(36), nullable=True)         # id of the log this reverts
    idempotency_key = Column(String(120), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    finished_at    = Column(DateTime, nullable=True)

    # SECURITY-2D-1B-A — additive, UNWIRED foundation for DB-enforced idempotency (the wiring +
    # partial-UNIQUE claim land in 1B-B). idempotency_key will identify WHICH business operation (a
    # stable immutable operation id — never derived from content); request_fingerprint describes WHAT
    # that operation does (its contents), so a same-key request with different contents can be caught as
    # a mismatch instead of dispatched. A content hash must NEVER be used as the operation identity: two
    # legitimate operations with equal content at different times are NOT a retry. Both columns are
    # nullable and NOT read by any runtime path in 1B-A.
    request_fingerprint = Column(String(72), nullable=True)   # "fp1:" + 64 lowercase hex = 68 chars
    dispatch_started_at = Column(DateTime(timezone=True), nullable=True)   # set just before the provider call (1B-B)

    # SECURITY-2D-1C-A — additive, UNWIRED recovery foundation. NOT read or written by any runtime path
    # in 1C-A (executor untouched). claim_generation is the fencing token: a future controlled re-own
    # (1C-C) bumps it, and the executor's pending→in_flight ownership CAS (also 1C-C) checks it, so a
    # revived stale worker is fenced out. reconciliation_status records read-only recovery classification
    # written by the reaper/reconciliation service (1C-B) — never the executor, never a provider write.
    claim_generation      = Column(Integer, nullable=False, server_default="0", default=0)
    reconciliation_status = Column(String(20), nullable=True)

    # SECURITY-2D-1C-B — read-only reconciliation scheduling (written ONLY by the recovery sweep, never
    # the executor, never a provider write). Bound the number of read-rechecks and schedule the next one
    # for eventual-consistent marketplaces. Unread in the executor.
    reconciliation_attempts = Column(Integer, nullable=False, server_default="0", default=0)
    last_reconciled_at      = Column(DateTime(timezone=True), nullable=True)
    next_reconcile_at       = Column(DateTime(timezone=True), nullable=True)

    # SECURITY-2D-1C-C1 — dispatch-attempt accounting, written ONLY by the executor's fencing CAS
    # (pending→in_flight). attempt_count is incremented inside the same atomic UPDATE that takes
    # in_flight, so it counts provable dispatch attempts (each a won ownership CAS); last_attempt_at
    # records when. A future controlled re-own (1C-C2) bounds retries on attempt_count. No PII.
    attempt_count  = Column(Integer, nullable=False, server_default="0", default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_execlog_user", "user_id"),
        Index("ix_execlog_user_action", "user_id", "action_type"),
        Index("ix_execlog_idem", "user_id", "action_type", "idempotency_key"),
        Index("ix_execlog_decision", "decision_id"),
        # SECURITY-2D-1B-B — DB-enforced at-most-one claim per (user, v1 operation key). Scoped to
        # 'v1:%' so legacy content-derived keys (which repeat across time) and NULL keys are exempt and
        # cannot cause a migration collision. Same predicate on SQLite + PostgreSQL.
        Index("uq_execlog_op_claim", "user_id", "idempotency_key", unique=True,
              sqlite_where=text("idempotency_key LIKE 'v1:%'"),
              postgresql_where=text("idempotency_key LIKE 'v1:%'")),
        # SECURITY-2D-1C-A — fencing counter is non-negative; reconciliation_status is NULL or one of the
        # 7 allowed recovery values (enum integrity enforced in the DB, not just app code).
        CheckConstraint("claim_generation >= 0", name="ck_execlog_claim_generation_nonneg"),
        CheckConstraint(
            f"reconciliation_status IS NULL OR reconciliation_status IN ({_RECON_IN})",
            name="ck_execlog_reconciliation_status"),
        CheckConstraint("reconciliation_attempts >= 0", name="ck_execlog_reconciliation_attempts_nonneg"),
        # SECURITY-2D-1C-C1 — dispatch-attempt counter is non-negative.
        CheckConstraint("attempt_count >= 0", name="ck_execlog_attempt_count_nonneg"),
    )
