"""
Money Leak Signal (Money Leak Detection contour data foundation, Phase 3.0) — seller-facing.

Business-Diagnosis view of WHY money leaks — silent cost-structure drift (commission
growth, logistics-cost growth, hidden passive margin erosion NOT caused by the seller's
price or ad actions). `insight_key` (`money_leak_<problem_type>:<marketplace>:<sku>`)
anchors a future promote into the Decision Spine; `marketplace` is the Learning OS context
dimension. `category` keeps the domain tag for architectural parity with the other contour
signal tables.

Money Leak Detection is DIAGNOSIS-ONLY (WHY money leaks) — it owns no treatment/lever and
does NOT absorb Pricing/Advertising/Operations executable responsibilities. The schema
deliberately mirrors GrowthSignal field-for-field for architectural consistency (status /
decision_id / priority_level / recommended_action / expected_effect kept intentionally),
even though the future producer will not bind an executor.

Plain table mirror, no signal-building logic — schema only (Phase 3.0). The lifecycle
entity (status mutates via reconciliation later) carries evidence_hash + updated_at; the
detection layer stays strictly append-only. No leak score, no forecast, no fabricated
number.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Float, DateTime, Index
from database import Base


class MoneyLeakSignal(Base):
    __tablename__ = "money_leak_signal"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → money_leak_audit.id
    problem_id  = Column(String(36), nullable=True)      # soft ref → money_leak_problem.id (future)
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)      # context dimension (Learning OS)
    sku         = Column(String(255), nullable=True)

    signal_key              = Column(String(64), nullable=False)  # canonical money_leak_<problem_type>
    insight_key             = Column(String(64), nullable=True)   # money_leak_<ptype>:<mp>:<sku>
    problem_type            = Column(String(40), nullable=False)
    category                = Column(String(20), nullable=True)   # domain tag (parity with other contours)
    recommended_action_key  = Column(String(64), nullable=True)
    alternative_action_keys = Column(Text, nullable=True)         # JSON list

    # PULT doctrine, 5 parts (deterministic text, no fabricated numbers)
    what            = Column(Text, nullable=True)
    why             = Column(Text, nullable=True)
    meaning         = Column(Text, nullable=True)
    what_to_do      = Column(Text, nullable=True)
    expected_effect = Column(Text, nullable=True)

    priority_level = Column(String(10), nullable=True)    # critical|high|medium|low
    effect_type    = Column(String(40), nullable=True)    # margin_erosion|... (deterministic class)
    effect_band    = Column(String(10), nullable=True)    # high|medium|low
    confidence     = Column(Float, nullable=True)

    status        = Column(String(20), nullable=False, default="active",
                           server_default="active")  # active|dismissed|promoted_to_decision|resolved|reopened
    evidence_hash = Column(String(64), nullable=True)
    decision_id   = Column(String(36), nullable=True)  # soft ref → decisions.id (set on promote, later)

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_money_leak_signal_user_listing", "user_id", "listing_id"),
        Index("ix_money_leak_signal_insight", "insight_key"),
        Index("ix_money_leak_signal_audit", "audit_id"),
        Index("ix_money_leak_signal_status", "status"),
        Index("ix_money_leak_signal_category", "category"),
    )
