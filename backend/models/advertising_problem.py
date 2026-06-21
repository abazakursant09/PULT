"""
Advertising Problem (Advertising Engine data foundation, A2) — append-only detection.

One immutable row per ad problem detected in a single audit run. Plain table
mirror, no rule logic. Lifecycle/status lives on advertising_signal, not here.
`problem_type` / `severity` / `estimated_effect_type` are canonical and
marketplace-agnostic; `evidence` holds deterministic facts only (ad_spend, drr,
net_profit, margin, stock — money/operations, never a cabinet metric).
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index
from database import Base


class AdvertisingProblem(Base):
    __tablename__ = "advertising_problem"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → advertising_audit.id
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)
    sku         = Column(String(255), nullable=True)

    problem_type          = Column(String(40), nullable=False)   # canonical (Signal Catalog)
    category              = Column(String(40), nullable=True)
    severity              = Column(String(10), nullable=False)   # critical|high|medium|low
    estimated_effect_type = Column(String(40), nullable=True)    # margin_loss|wasted_spend|...
    detectability         = Column(String(20), nullable=True)    # finance|requires_ad_api
    evidence              = Column(Text, nullable=True)          # JSON deterministic facts

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_adv_problem_audit", "audit_id"),
        Index("ix_adv_problem_user_listing_type", "user_id", "listing_id", "problem_type"),
    )
