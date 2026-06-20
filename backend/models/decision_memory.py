"""
Decision Memory (Memory OS Phase 1, Slice 1) — append-only schema.

One immutable row per recorded decision outcome: which chain, which attempt,
which action, in which business context, with what observed/estimated effect.
This is the FOUNDATION layer only — no similarity, no learning, no propagation,
no refuted-loop. It just remembers.

Append-only by contract:
- no `updated_at`, no mutable lifecycle columns;
- rows are INSERTed when an outcome resolves and never updated;
- chain status (open/confirmed/stopped) is DERIVED from rows, never stored here.

No business logic lives in this model — it is a plain table mirror.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, DateTime, Index
from database import Base


class DecisionMemory(Base):
    __tablename__ = "decision_memory"

    id                = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    decision_id       = Column(String(36), nullable=False)   # provenance → decisions.id (soft ref)
    decision_chain_id = Column(String(36), nullable=True)    # цепочка попыток над одной проблемой
    step_in_chain     = Column(Integer, nullable=False, default=0, server_default="0")

    product_id        = Column(String(36), nullable=True)    # physical product anchor
    marketplace       = Column(String(20), nullable=True)    # context dimension + execution target
    action_type       = Column(String(64), nullable=True)    # действие (НЕ входит в context_group)

    context_group     = Column(String(200), nullable=True)   # (marketplace, category, price_band, margin_band) — без action_type

    outcome           = Column(String(20), nullable=False)   # confirmed | refuted | insufficient | pending
    effect_value      = Column(Float, nullable=True)         # ИЗМЕРЕННАЯ дельта (null до измерения)
    estimate_value    = Column(Float, nullable=True)         # ОЦЕНКА ₽ (отдельно от измеренного эффекта)

    created_at        = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_decision_memory_chain", "decision_chain_id"),
        Index("ix_decision_memory_product", "product_id"),
        Index("ix_decision_memory_decision", "decision_id"),
        Index("ix_decision_memory_context", "context_group"),
    )
