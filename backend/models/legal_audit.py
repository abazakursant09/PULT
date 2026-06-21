"""
Legal Audit (Legal Navigator data foundation, A2) — append-only run record.

Legal Navigator is a RECOMMENDATION contour for legal risk — it never replaces a
lawyer, never issues a legal conclusion, never guarantees an outcome. One row per
legal check run over a subject (product / listing / brand / account). NO rule logic
here — plain table mirror, modelled on growth_audit. Marketplace-agnostic:
`marketplace` is provenance/dispatch only. Append-only: no `updated_at`, written
once per run. No score, no internal_health_index — legal risk is never a fabricated
index, only deterministic detected observations.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Index
from database import Base


class LegalAudit(Base):
    __tablename__ = "legal_audit"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id (seller)
    listing_id   = Column(String(36), nullable=True)     # soft ref → product_listings.id
    marketplace  = Column(String(20), nullable=True)     # provenance / dispatch only
    sku          = Column(String(255), nullable=True)
    subject_type = Column(String(20), nullable=True)     # product|listing|brand|account|sku
    subject_ref  = Column(String(255), nullable=True)    # soft ref to the checked object
    source       = Column(String(20), nullable=True)     # listing|import|manual|api

    status               = Column(String(15), nullable=False, default="completed",
                                  server_default="completed")  # pending|running|completed|failed
    rule_catalog_version = Column(String(20), nullable=True)
    snapshot_hash        = Column(String(64), nullable=True)

    total_findings       = Column(Integer, nullable=False, default=0, server_default="0")
    total_not_evaluated  = Column(Integer, nullable=False, default=0, server_default="0")
    top_severity         = Column(String(10), nullable=True)   # critical|high|medium|low

    triggered_by = Column(String(20), nullable=True)           # manual|scheduled|after_import
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_legal_audit_user_listing", "user_id", "listing_id"),
        Index("ix_legal_audit_status", "status"),
    )
