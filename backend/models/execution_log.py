import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, JSON, Index
from database import Base


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
    status         = Column(String(20), nullable=False, default="pending")  # pending|running|success|failed|rejected|reverted
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

    __table_args__ = (
        Index("ix_execlog_user", "user_id"),
        Index("ix_execlog_user_action", "user_id", "action_type"),
        Index("ix_execlog_idem", "user_id", "action_type", "idempotency_key"),
        Index("ix_execlog_decision", "decision_id"),
    )
