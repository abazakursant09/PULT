"""
Review Problem (Review Assistant data foundation, A2) — append-only detection.

One immutable row per reputation problem detected in a single audit run. Plain
table mirror, no rule logic. Lifecycle/status lives on review_signal, not here.
`category` carries the safety class (SAFE | ATTENTION | RISK); `review_id` is a
soft ref to the specific review when the problem is review-scoped. `evidence`
holds deterministic facts only (rating, has_text, complaint flags) — never an AI
verdict.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index
from database import Base


class ReviewProblem(Base):
    __tablename__ = "review_problem"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → review_audit.id
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    review_id   = Column(String(36), nullable=True)      # soft ref → the specific review
    marketplace = Column(String(20), nullable=True)
    sku         = Column(String(255), nullable=True)

    problem_type          = Column(String(40), nullable=False)   # canonical
    category              = Column(String(40), nullable=True)    # SAFE | ATTENTION | RISK
    severity              = Column(String(10), nullable=False)   # critical|high|medium|low
    estimated_effect_type = Column(String(40), nullable=True)    # reputation_risk|rating_risk|...
    detectability         = Column(String(20), nullable=True)    # reviews|requires_text
    evidence              = Column(Text, nullable=True)          # JSON deterministic facts

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_review_problem_audit", "audit_id"),
        Index("ix_review_problem_user_listing_type", "user_id", "listing_id", "problem_type"),
        Index("ix_review_problem_review", "review_id"),
    )
