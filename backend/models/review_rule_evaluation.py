"""
Review Rule Evaluation (Review Assistant data foundation, A2) — coverage ledger.

Distinguishes "problem NOT found" from "problem NOT evaluated". One immutable row
per (audit, problem_type):
  result = "triggered"     → a review_problem was emitted (FOUND)
  result = "not_triggered" → rule ran, predicate false (definitively NOT found)
  result = "not_evaluated" → rule could not run (e.g. no review text); see reason

Absence of a problem row is never ambiguous. Append-only, marketplace-agnostic.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index, UniqueConstraint
from database import Base


class ReviewRuleEvaluation(Base):
    __tablename__ = "review_rule_evaluation"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id     = Column(String(36), nullable=False)    # soft ref → review_audit.id
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id
    listing_id   = Column(String(36), nullable=True)

    problem_type = Column(String(40), nullable=False)    # canonical rule key
    result       = Column(String(20), nullable=False)    # triggered|not_triggered|not_evaluated
    reason       = Column(String(120), nullable=True)    # e.g. "missing_fields: review_text"
    evidence     = Column(Text, nullable=True)           # JSON facts when triggered

    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("audit_id", "problem_type", name="uq_review_rule_eval_audit_type"),
        Index("ix_review_rule_eval_audit", "audit_id"),
        Index("ix_review_rule_eval_result", "result"),
    )
