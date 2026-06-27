"""
Operations Signal (Operations contour data foundation, Slice 1) — seller-facing.

First-class canonical PULT contour for OBSERVED operations problems. Same column
shape + lifecycle as the other contour signal tables (seo/advertising/review/
growth/legal/pricing): a lifecycle entity keyed by insight_key
`operations_<problem_type>:<marketplace>:<sku>`, carrying the five PULT doctrine
fields + evidence_hash for reconciliation. `marketplace` is the Learning OS context
dimension.

Slice 1 use-case: auto-promotion margin drain — an Ozon listing participating in an
auto-promotion while observed net_profit < 0. Derived ONLY from observed inputs
(auto-promotion participation + ImportedFinanceRow net_profit) — never a forecast,
competitor figure, compute_recommendation, or fabricated number.

Plain table mirror, no signal-building logic (that lives in services/operations/).
This slice promotes the signal only as far as Candidate / Decision — no Apply, no
Effect, no Learning wiring here.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Float, DateTime, Index
from database import Base


class OperationsSignal(Base):
    __tablename__ = "operations_signal"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=True)      # soft ref (no audit table)
    problem_id  = Column(String(36), nullable=True)      # soft ref (reserved)
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)      # context dimension (Learning OS)
    sku         = Column(String(255), nullable=True)

    signal_key              = Column(String(64), nullable=False)  # canonical operations_<problem_type>
    insight_key             = Column(String(64), nullable=True)   # operations_<ptype>:<mp>:<sku>
    problem_type            = Column(String(40), nullable=False)
    category                = Column(String(20), nullable=True)   # operations (domain)
    recommended_action_key  = Column(String(64), nullable=True)   # None until binding
    alternative_action_keys = Column(Text, nullable=True)         # JSON list

    # PULT doctrine, 5 parts (deterministic text, no fabricated numbers):
    #   what_happened / why_it_happened / what_it_means / what_to_do / expected_effect
    what            = Column(Text, nullable=True)
    why             = Column(Text, nullable=True)
    meaning         = Column(Text, nullable=True)
    what_to_do      = Column(Text, nullable=True)
    expected_effect = Column(Text, nullable=True)

    priority_level = Column(String(10), nullable=True)    # critical|high|medium|low
    effect_type    = Column(String(40), nullable=True)    # margin_drain|...
    effect_band    = Column(String(10), nullable=True)    # high|medium|low
    confidence     = Column(Float, nullable=True)

    status        = Column(String(20), nullable=False, default="active",
                           server_default="active")  # active|dismissed|promoted_to_decision|resolved|reopened
    evidence_hash = Column(String(64), nullable=True)
    decision_id   = Column(String(36), nullable=True)  # soft ref → decisions.id (set on promote)

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_operations_signal_user_listing", "user_id", "listing_id"),
        Index("ix_operations_signal_insight", "insight_key"),
        Index("ix_operations_signal_status", "status"),
    )
