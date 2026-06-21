"""
Legal Finding (Legal Navigator data foundation, A2) — append-only detection.

One immutable row per legal-risk OBSERVATION detected in a single audit run. A
finding is an observation that a requirement MAY apply / MAY be unmet — never a
legal conclusion, never a verdict. Plain table mirror, no rule logic.
Lifecycle/status lives on legal_signal, not here.

`requirement_type` is the canonical legal check/requirement key (e.g.
mark_required, certificate_required). `category` is the legal domain:
  marking | certification | labeling | ip | tax | content | prohibited
`evidence` holds deterministic facts only (which field is missing, which marker
matched) — never an AI verdict, never a forecast, never a legal opinion.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index
from database import Base


class LegalFinding(Base):
    __tablename__ = "legal_finding"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id     = Column(String(36), nullable=False)    # soft ref → legal_audit.id
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id (seller)
    listing_id   = Column(String(36), nullable=True)     # soft ref → product_listings.id
    marketplace  = Column(String(20), nullable=True)
    sku          = Column(String(255), nullable=True)
    subject_type = Column(String(20), nullable=True)     # product|listing|brand|account|sku
    subject_ref  = Column(String(255), nullable=True)

    requirement_type      = Column(String(40), nullable=False)   # canonical legal check key
    category              = Column(String(20), nullable=True)    # marking|certification|labeling|ip|tax|content|prohibited
    severity              = Column(String(10), nullable=False)   # critical|high|medium|low
    risk_level            = Column(String(10), nullable=True)    # high|medium|low (advisory, not a verdict)
    estimated_effect_type = Column(String(40), nullable=True)    # compliance_risk|takedown_risk|fine_risk|block_risk
    detectability         = Column(String(20), nullable=True)    # listing|import|requires_data
    evidence              = Column(Text, nullable=True)          # JSON deterministic facts

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_legal_finding_audit", "audit_id"),
        Index("ix_legal_finding_user_listing_type", "user_id", "listing_id", "requirement_type"),
        Index("ix_legal_finding_category", "category"),
    )
