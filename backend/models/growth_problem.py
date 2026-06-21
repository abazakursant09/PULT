"""
Growth Problem (Growth/Opportunity Engine data foundation, A2) — append-only.

One immutable row per growth OPPORTUNITY (gap) detected in a single audit run.
"Problem" here means "unrealised opportunity", not a defect. Plain table mirror,
no rule logic. Lifecycle/status lives on growth_signal, not here.

`category` is the growth domain the opportunity belongs to:
  pricing | advertising | seo | inventory | reputation
`evidence` holds deterministic facts only (numbers from finance/listing) — never
a forecast, market trend, or AI verdict.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index
from database import Base


class GrowthProblem(Base):
    __tablename__ = "growth_problem"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → growth_audit.id
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)
    sku         = Column(String(255), nullable=True)

    problem_type          = Column(String(40), nullable=False)   # canonical opportunity key
    category              = Column(String(20), nullable=True)    # pricing|advertising|seo|inventory|reputation
    severity              = Column(String(10), nullable=False)   # critical|high|medium|low
    estimated_effect_type = Column(String(40), nullable=True)    # revenue_gain|margin_gain|traffic_gain|...
    detectability         = Column(String(20), nullable=True)    # finance|listing|requires_data
    evidence              = Column(Text, nullable=True)          # JSON deterministic facts

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_growth_problem_audit", "audit_id"),
        Index("ix_growth_problem_user_listing_type", "user_id", "listing_id", "problem_type"),
        Index("ix_growth_problem_category", "category"),
    )
