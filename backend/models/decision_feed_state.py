"""
Decision Feed State (Daily Decision Feed data foundation, A2) — per-user attention
state for one feed item.

The Daily Decision Feed is the single "what needs my decision today" surface over
all six contours (SEO / Advertising / Review / Growth / Legal + Decision Outcome).
It NEVER stores or duplicates a signal — the signals live in their own engine
tables and the proven effects in engine_effect_observation. This table holds ONLY
the seller's attention state for a feed item (seen / snoozed / dismissed …),
keyed by a CANONICAL item_key (canonical insight_key, or decision_id for Decision
Outcome — never a raw / 4-part Review key).

Lifecycle entity: state mutates over time → carries updated_at. Marketplace-agnostic:
all refs are soft, `contour` is provenance only. No score, no priority number, no
forecast — this is attention bookkeeping, not analytics.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Index, UniqueConstraint
from database import Base


class DecisionFeedState(Base):
    __tablename__ = "decision_feed_state"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id (seller)

    # stable feed item identity — canonical insight_key, or decision_id for the
    # Decision Outcome contour. NEVER a raw signal_key or a 4-part Review key.
    item_key     = Column(String(80), nullable=False)
    contour      = Column(String(20), nullable=False)    # seo|advertising|review|growth|legal|decision_outcome

    # attention lifecycle: new | seen | snoozed | acted | dismissed
    state        = Column(String(15), nullable=False, default="new", server_default="new")
    snooze_until = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)

    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at   = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "item_key", name="uq_feed_state_user_item"),
        Index("ix_feed_state_user_state", "user_id", "state"),
        Index("ix_feed_state_contour", "contour"),
    )
