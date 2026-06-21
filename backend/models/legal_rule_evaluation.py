"""
Legal Rule Evaluation (Legal Navigator data foundation, A2) — coverage ledger.

Distinguishes "requirement checked, no risk observed" from "requirement NOT
evaluated". One immutable row per (audit, requirement_type):
  result = "detected"      → a legal_finding was emitted (risk observed)
  result = "not_detected"  → rule ran, predicate false (definitively no observation)
  result = "not_evaluated" → rule could not run (e.g. missing certificate data);
                             see reason

Absence of a finding is never ambiguous — honest degradation. A not_evaluated
requirement does NOT mean compliant. Append-only, marketplace-agnostic.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index, UniqueConstraint
from database import Base


class LegalRuleEvaluation(Base):
    __tablename__ = "legal_rule_evaluation"

    id               = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id         = Column(String(36), nullable=False)    # soft ref → legal_audit.id
    user_id          = Column(String(36), nullable=False)    # soft ref → users.id
    listing_id       = Column(String(36), nullable=True)

    requirement_type = Column(String(40), nullable=False)    # canonical rule key
    result           = Column(String(20), nullable=False)    # detected|not_detected|not_evaluated
    reason           = Column(String(120), nullable=True)    # e.g. "missing_fields: certificate"
    evidence         = Column(Text, nullable=True)           # JSON facts when detected

    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("audit_id", "requirement_type", name="uq_legal_rule_eval_audit_type"),
        Index("ix_legal_rule_eval_audit", "audit_id"),
        Index("ix_legal_rule_eval_result", "result"),
    )
