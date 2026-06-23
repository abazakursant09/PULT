"""
Promotion Run (Promotion Activation data foundation, A2) — append-only ledger.

One row per run of the promotion activator: how many candidates were seen, how many
links were created, how many Decisions were promoted, how many skipped. It records
the activation of the EXISTING Decision Outcome promotion/bridge — it triggers no
execution, applies nothing, opens no measurement. A Decision is an intent record,
not a marketplace action. Append-only: written once per run, never rewritten — no
`updated_at`. Marketplace-agnostic, soft refs. No score, no forecast, no priority.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Index
from database import Base


class PromotionRun(Base):
    __tablename__ = "promotion_run"

    id                = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id           = Column(String(36), nullable=False)    # soft ref → users.id
    contour           = Column(String(20), nullable=True)     # filter used, or null = all contours

    candidates_seen   = Column(Integer, nullable=False, default=0, server_default="0")
    links_created     = Column(Integer, nullable=False, default=0, server_default="0")
    decisions_created = Column(Integer, nullable=False, default=0, server_default="0")
    skipped           = Column(Integer, nullable=False, default=0, server_default="0")

    triggered_by      = Column(String(20), nullable=True)     # manual|after_audit|...
    created_at        = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_promotion_run_user", "user_id"),
        Index("ix_promotion_run_contour", "contour"),
    )
