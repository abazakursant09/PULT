"""
Decision Apply Intent (Decision Apply UX data foundation, A2) — append-only.

Records that a seller PREVIEWED / confirmed / rejected applying a decision. A2 only
ships the schema + the read-only preview service; nothing here triggers a real
apply. Append-only: written once per intent event, never rewritten — no
`updated_at`. Marketplace-agnostic, soft refs. No score, no forecast, no priority —
an approval/audit ledger, not analytics.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index
from database import Base


class DecisionApplyIntent(Base):
    __tablename__ = "decision_apply_intent"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String(36), nullable=False)    # soft ref → users.id
    decision_id    = Column(String(36), nullable=False)    # soft ref → decisions.id
    action_key     = Column(String(64), nullable=True)     # catalog action, or null when not applyable

    # previewed | confirmed | rejected
    intent_status  = Column(String(15), nullable=False)
    dry_run_status = Column(String(20), nullable=True)     # executor dry_run status, if reached
    reason         = Column(Text, nullable=True)
    marketplace    = Column(String(20), nullable=True)     # provenance / context only

    created_at     = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_decision_apply_intent_user", "user_id"),
        Index("ix_decision_apply_intent_decision", "decision_id"),
        Index("ix_decision_apply_intent_status", "intent_status"),
    )
