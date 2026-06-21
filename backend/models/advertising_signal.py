"""
Advertising Signal (Advertising Engine data foundation, A2) — seller-facing decision.

PULT-doctrine view of an ad problem: what / why / meaning / what_to_do /
expected_effect + canonical recommended action. `insight_key`
(`adv_<problem_type>:<marketplace>:<sku>`) anchors a future promote into the
Decision Spine, and `marketplace` is the same context dimension Learning OS uses.
Advertising decisions are measured on net_profit (Effect PULT) — money-first.

Plain table mirror, no signal-building logic. The lifecycle entity (status
mutates via reconciliation later) — carries evidence_hash + updated_at; the
detection layer (audit/problem/rule_evaluation) stays strictly append-only.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Float, DateTime, Index
from database import Base


class AdvertisingSignal(Base):
    __tablename__ = "advertising_signal"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → advertising_audit.id
    problem_id  = Column(String(36), nullable=True)      # soft ref → advertising_problem.id
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)      # context dimension (Learning OS)
    sku         = Column(String(255), nullable=True)

    signal_key              = Column(String(64), nullable=False)  # canonical adv_<problem_type>
    insight_key             = Column(String(64), nullable=True)   # adv_<ptype>:<mp>:<sku> (Decision bridge)
    problem_type            = Column(String(40), nullable=False)
    recommended_action_key  = Column(String(64), nullable=True)   # canonical primary (Action Catalog)
    alternative_action_keys = Column(Text, nullable=True)         # JSON list of alternatives

    # PULT doctrine, 5 parts (deterministic text, money-first facts)
    what            = Column(Text, nullable=True)
    why             = Column(Text, nullable=True)
    meaning         = Column(Text, nullable=True)
    what_to_do      = Column(Text, nullable=True)
    expected_effect = Column(Text, nullable=True)

    priority_level       = Column(String(10), nullable=True)    # critical|high|medium|low
    expected_effect_type = Column(String(40), nullable=True)    # margin_loss|wasted_spend|...
    effect_band          = Column(String(10), nullable=True)    # high|medium|low
    confidence           = Column(Float, nullable=True)

    status        = Column(String(20), nullable=False, default="active",
                           server_default="active")  # active|dismissed|promoted_to_decision|resolved|reopened
    evidence_hash = Column(String(64), nullable=True)  # change-detection for reconciliation
    decision_id   = Column(String(36), nullable=True)  # soft ref → decisions.id (set on promote, later)

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = Column(DateTime, nullable=True)      # last lifecycle transition

    __table_args__ = (
        Index("ix_adv_signal_user_listing", "user_id", "listing_id"),
        Index("ix_adv_signal_insight", "insight_key"),
        Index("ix_adv_signal_audit", "audit_id"),
        Index("ix_adv_signal_status", "status"),
    )
