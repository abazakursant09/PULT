"""
Engine Signal → Decision Link (Decision Outcome data foundation, A2).

The binding ledger that will let an engine lifecycle signal (SEO / Advertising /
Review / Growth / Legal) be promoted into a Decision. A2 is SCHEMA ONLY — no
bridge, no promotion, no execution, no measurement.

Append-only: written once per (seller, insight, action) binding, never destructively
rewritten — no `updated_at`. `link_status` carries the binding's place in the
future loop (proposed → promoted → measured → rejected); in A2 it is only ever the
default `proposed` (nothing transitions it yet). Marketplace-agnostic: all refs are
soft (String, no FK); `marketplace` is provenance/context only.

NO score, NO forecast, NO ROI, NO money projection — this is identity/provenance,
not economics.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Index, UniqueConstraint
from database import Base


class EngineSignalDecisionLink(Base):
    __tablename__ = "engine_signal_decision_link"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id (seller)
    contour      = Column(String(20), nullable=False)    # seo|advertising|review|growth|legal
    signal_table = Column(String(40), nullable=False)    # e.g. seo_signal, legal_signal
    signal_id    = Column(String(36), nullable=False)    # soft ref → <contour>_signal.id
    insight_key  = Column(String(80), nullable=False)    # canonical engine insight_key
    action_key   = Column(String(64), nullable=True)     # executor action; null = manual decision
    decision_id  = Column(String(36), nullable=True)     # soft ref → decisions.id (set on promote, later)

    link_status  = Column(String(20), nullable=False, default="proposed",
                          server_default="proposed")     # proposed|promoted|measured|rejected

    marketplace  = Column(String(20), nullable=True)     # provenance / context only
    sku          = Column(String(255), nullable=True)

    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # one binding per (seller, insight, action) — mirrors decisions'
        # uq_decision_user_insight_action; an insight may bind several actions.
        UniqueConstraint("user_id", "insight_key", "action_key",
                         name="uq_engine_link_user_insight_action"),
        Index("ix_engine_link_user_contour", "user_id", "contour"),
        Index("ix_engine_link_insight", "insight_key"),
        Index("ix_engine_link_decision", "decision_id"),
        Index("ix_engine_link_status", "link_status"),
    )
