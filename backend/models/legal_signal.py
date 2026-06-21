"""
Legal Signal (Legal Navigator data foundation, A2) — seller-facing recommendation.

PULT-doctrine view of a legal-risk observation: what / why / meaning / what_to_do /
expected_effect + a canonical recommended action. The recommended action is always
advisory — "проверить", "обратиться к юристу/специалисту", "получить документ" —
NEVER a legal conclusion, NEVER a guarantee of outcome.

`insight_key` (`legal_<requirement_type>:<marketplace>:<sku>`) anchors a future
promote into the Decision Spine; `marketplace` is the Learning OS context
dimension; `effect_type`/`effect_band` describe the deterministic risk class for
Effect PULT — never a forecast, never a fabricated number.

Plain table mirror, no signal-building logic. The lifecycle entity (status mutates
via reconciliation later) — carries evidence_hash + updated_at; the detection layer
stays strictly append-only.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Float, DateTime, Index
from database import Base


class LegalSignal(Base):
    __tablename__ = "legal_signal"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id     = Column(String(36), nullable=False)    # soft ref → legal_audit.id
    finding_id   = Column(String(36), nullable=True)     # soft ref → legal_finding.id
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id (seller)
    listing_id   = Column(String(36), nullable=True)     # soft ref → product_listings.id
    marketplace  = Column(String(20), nullable=True)     # context dimension (Learning OS)
    sku          = Column(String(255), nullable=True)
    subject_type = Column(String(20), nullable=True)     # product|listing|brand|account|sku
    subject_ref  = Column(String(255), nullable=True)

    signal_key              = Column(String(64), nullable=False)  # canonical legal_<requirement_type>
    insight_key             = Column(String(64), nullable=True)   # legal_<rtype>:<mp>:<sku>
    requirement_type        = Column(String(40), nullable=False)
    category                = Column(String(20), nullable=True)   # marking|certification|labeling|ip|tax|content|prohibited
    recommended_action_key  = Column(String(64), nullable=True)   # check_requirement|consult_lawyer|obtain_document|...
    alternative_action_keys = Column(Text, nullable=True)         # JSON list

    # PULT doctrine, 5 parts (deterministic advisory text, no legal conclusion)
    what            = Column(Text, nullable=True)
    why             = Column(Text, nullable=True)
    meaning         = Column(Text, nullable=True)
    what_to_do      = Column(Text, nullable=True)
    expected_effect = Column(Text, nullable=True)

    priority_level = Column(String(10), nullable=True)    # critical|high|medium|low
    risk_level     = Column(String(10), nullable=True)    # high|medium|low (advisory)
    effect_type    = Column(String(40), nullable=True)    # compliance_risk|takedown_risk|fine_risk|block_risk
    effect_band    = Column(String(10), nullable=True)    # high|medium|low
    confidence     = Column(Float, nullable=True)

    # lifecycle: active|acknowledged|dismissed|promoted_to_decision|resolved|reopened
    status        = Column(String(20), nullable=False, default="active", server_default="active")
    evidence_hash = Column(String(64), nullable=True)
    decision_id   = Column(String(36), nullable=True)  # soft ref → decisions.id (set on promote, later)

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_legal_signal_user_listing", "user_id", "listing_id"),
        Index("ix_legal_signal_insight", "insight_key"),
        Index("ix_legal_signal_audit", "audit_id"),
        Index("ix_legal_signal_status", "status"),
        Index("ix_legal_signal_category", "category"),
    )
