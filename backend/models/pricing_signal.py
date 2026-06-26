"""
Pricing Signal (Pricing/Margin Engine data foundation, A3-pre) — seller-facing.

First-class canonical PULT contour for observed pricing/margin problems. A
PULT-doctrine view of a margin problem: what / why / meaning / what_to_do /
expected_effect + (later) a canonical recommended action. `insight_key`
(`pricing_<problem_type>:<marketplace>:<sku>`) anchors a future promote into the
Decision Spine; `marketplace` is the Learning OS context dimension.

Derived ONLY from observed finance (ImportedFinanceRow) + optional listing price /
PricingRule floor — never a forecast, competitor-price recommendation, or fabricated
number. No pricing score.

Plain table mirror, no signal-building logic (that lives in services/pricing/). The
lifecycle entity (status mutates via reconciliation) — carries evidence_hash +
updated_at; same column shape + lifecycle as the other contour signal tables.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Float, DateTime, Index
from database import Base


class PricingSignal(Base):
    __tablename__ = "pricing_signal"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=True)      # soft ref (no audit table in A3-pre)
    problem_id  = Column(String(36), nullable=True)      # soft ref (reserved)
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)      # context dimension (Learning OS)
    sku         = Column(String(255), nullable=True)

    signal_key              = Column(String(64), nullable=False)  # canonical pricing_<problem_type>
    insight_key             = Column(String(64), nullable=True)   # pricing_<ptype>:<mp>:<sku>
    problem_type            = Column(String(40), nullable=False)
    category                = Column(String(20), nullable=True)   # pricing (domain)
    recommended_action_key  = Column(String(64), nullable=True)   # None until A3-bind
    alternative_action_keys = Column(Text, nullable=True)         # JSON list

    # PULT doctrine, 5 parts (deterministic text, no fabricated numbers):
    #   what_happened / why_it_happened / what_it_means / what_to_do / expected_effect
    what            = Column(Text, nullable=True)
    why             = Column(Text, nullable=True)
    meaning         = Column(Text, nullable=True)
    what_to_do      = Column(Text, nullable=True)
    expected_effect = Column(Text, nullable=True)

    priority_level = Column(String(10), nullable=True)    # critical|high|medium|low
    effect_type    = Column(String(40), nullable=True)    # margin_loss|margin_below_target|...
    effect_band    = Column(String(10), nullable=True)    # high|medium|low
    confidence     = Column(Float, nullable=True)

    status        = Column(String(20), nullable=False, default="active",
                           server_default="active")  # active|dismissed|promoted_to_decision|resolved|reopened
    evidence_hash = Column(String(64), nullable=True)
    decision_id   = Column(String(36), nullable=True)  # soft ref → decisions.id (set on promote, later)

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_pricing_signal_user_listing", "user_id", "listing_id"),
        Index("ix_pricing_signal_insight", "insight_key"),
        Index("ix_pricing_signal_status", "status"),
    )
