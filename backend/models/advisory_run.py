"""
Advisory Run (Advisory Runtime data foundation, Phase-0) — append-only ledger.

One row per Advisory Runtime invocation of ONE producer for ONE user: timing,
status, and an OPAQUE producer-supplied `stats` blob. The Runtime owns only the
generic fields (timing / status / error / producer_key); it does NOT know the
semantics of `stats` — that belongs to the producer. Therefore this table carries
NO typed counters (no listings_seen, no signals_created, no problems_detected):
those, if a producer reports them, live inside `stats` as JSON.

Append-only: written once per run, never rewritten — no `updated_at`.
Marketplace-agnostic, soft refs. No score, no forecast, no priority.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Text, Index
from database import Base


class AdvisoryRun(Base):
    __tablename__ = "advisory_run"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id       = Column(String(36), nullable=False)     # batch id of one Runtime tick
    user_id      = Column(String(36), nullable=False)     # soft ref → users.id
    producer_key = Column(String(64), nullable=False)     # ProducerSpec.key

    started_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at  = Column(DateTime, nullable=True)        # null while in-flight
    duration_ms  = Column(Integer, nullable=True)

    status       = Column(String(20), nullable=False, default="ok")  # ok | error | skipped
    error        = Column(Text, nullable=True)
    triggered_by = Column(String(20), nullable=True)      # scheduled | import | manual

    # OPAQUE producer-supplied counts/context — Runtime stores it verbatim, never
    # reads its keys. JSON object string (project convention: Text + json).
    stats        = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_advisory_run_user_producer_started", "user_id", "producer_key", "started_at"),
        Index("ix_advisory_run_run_id", "run_id"),
        Index("ix_advisory_run_status", "status"),
    )
