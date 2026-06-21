"""
SEO Rule Evaluation (SEO Engine data foundation, A2) — coverage ledger.

The explicit mechanism that lets PULT distinguish "problem NOT found" from
"problem NOT evaluated". One immutable row per (audit, problem_type) rule run:

  result = "triggered"      → a seo_problem was emitted (problem FOUND)
  result = "not_triggered"  → rule ran, predicate false (problem definitively NOT found)
  result = "not_evaluated"  → rule could not run (missing snapshot fields); see `reason`

Because every rule's evaluation outcome is recorded, the ABSENCE of a seo_problem
row is never ambiguous — the ledger states whether the rule was actually run.
Plain table mirror, append-only, marketplace-agnostic. No rule logic here.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Index, UniqueConstraint
from database import Base


class SeoRuleEvaluation(Base):
    __tablename__ = "seo_rule_evaluation"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id     = Column(String(36), nullable=False)    # soft ref → seo_audit.id
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id
    listing_id   = Column(String(36), nullable=True)     # soft ref → product_listings.id

    problem_type = Column(String(40), nullable=False)    # canonical rule key
    result       = Column(String(20), nullable=False)    # triggered|not_triggered|not_evaluated
    reason       = Column(String(120), nullable=True)    # e.g. "missing_fields: category_schema"

    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # one outcome per rule per audit run
        UniqueConstraint("audit_id", "problem_type", name="uq_seo_rule_eval_audit_type"),
        Index("ix_seo_rule_eval_audit", "audit_id"),
        Index("ix_seo_rule_eval_result", "result"),
    )
