"""
Review Signal (Review Assistant data foundation, A2) — seller-facing decision.

PULT-doctrine view of a reputation problem: what / why / meaning / what_to_do /
expected_effect + canonical recommended action. `insight_key`
(`rev_<problem_type>:<marketplace>:<sku>`) anchors a future promote into the
Decision Spine; `marketplace` is the Learning OS context dimension.

`safety_mode` encodes the Human-Control + Negative-Review doctrine — the ONLY
mode PULT may recommend for this signal:
  off | manual_approval | auto | manual_only
RISK-category (negative) reviews are manual_only; AUTO is never allowed there.
No "this reply raises your rating" — expected_effect is reputation-risk language
only (No Fake Impact).

Plain table mirror, no signal-building logic. The lifecycle entity (status
mutates via reconciliation later) — carries evidence_hash + updated_at; the
detection layer stays strictly append-only.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Float, DateTime, Index
from database import Base


class ReviewSignal(Base):
    __tablename__ = "review_signal"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → review_audit.id
    problem_id  = Column(String(36), nullable=True)      # soft ref → review_problem.id
    review_id   = Column(String(36), nullable=True)      # soft ref → the specific review
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)      # context dimension (Learning OS)
    sku         = Column(String(255), nullable=True)

    signal_key              = Column(String(64), nullable=False)  # canonical rev_<problem_type>
    insight_key             = Column(String(64), nullable=True)   # rev_<ptype>:<mp>:<sku>
    problem_type            = Column(String(40), nullable=False)
    safety_category         = Column(String(20), nullable=True)   # SAFE | ATTENTION | RISK
    safety_mode             = Column(String(20), nullable=True)   # off|manual_approval|auto|manual_only
    recommended_action_key  = Column(String(64), nullable=True)
    alternative_action_keys = Column(Text, nullable=True)         # JSON list

    # PULT doctrine, 5 parts (deterministic text, reputation-risk language only)
    what            = Column(Text, nullable=True)
    why             = Column(Text, nullable=True)
    meaning         = Column(Text, nullable=True)
    what_to_do      = Column(Text, nullable=True)
    expected_effect = Column(Text, nullable=True)

    priority_level       = Column(String(10), nullable=True)    # critical|high|medium|low
    expected_effect_type = Column(String(40), nullable=True)    # reputation_risk|rating_risk|...
    effect_band          = Column(String(10), nullable=True)    # high|medium|low
    confidence           = Column(Float, nullable=True)

    status        = Column(String(20), nullable=False, default="active",
                           server_default="active")  # active|dismissed|promoted_to_decision|resolved|reopened
    evidence_hash = Column(String(64), nullable=True)
    decision_id   = Column(String(36), nullable=True)  # soft ref → decisions.id (set on promote, later)

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_review_signal_user_listing", "user_id", "listing_id"),
        Index("ix_review_signal_insight", "insight_key"),
        Index("ix_review_signal_audit", "audit_id"),
        Index("ix_review_signal_status", "status"),
        Index("ix_review_signal_review", "review_id"),
    )
