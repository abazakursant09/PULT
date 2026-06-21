"""
Review Audit (Review Assistant data foundation, A2) — append-only run record.

Review Assistant is a REPUTATION-management contour, NOT an autoresponder. PULT
never turns automatic replies on by itself. One row per review audit of a
listing. NO rule logic here — plain table mirror. Marketplace-agnostic:
`marketplace` is provenance/dispatch only. Append-only: no `updated_at`, written
once per run. No public score — reputation effect is never a fabricated index.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Index
from database import Base


class ReviewAudit(Base):
    __tablename__ = "review_audit"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id
    listing_id   = Column(String(36), nullable=True)     # soft ref → product_listings.id
    marketplace  = Column(String(20), nullable=True)     # provenance / dispatch only
    sku          = Column(String(255), nullable=True)
    source       = Column(String(20), nullable=True)     # reviews | api | manual

    status               = Column(String(15), nullable=False, default="completed",
                                  server_default="completed")  # pending|running|completed|failed
    rule_catalog_version = Column(String(20), nullable=True)
    snapshot_hash        = Column(String(64), nullable=True)

    total_problems       = Column(Integer, nullable=False, default=0, server_default="0")
    total_not_evaluated  = Column(Integer, nullable=False, default=0, server_default="0")
    top_severity         = Column(String(10), nullable=True)   # critical|high|medium|low

    triggered_by = Column(String(20), nullable=True)           # manual|scheduled|after_new_review
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_review_audit_user_listing", "user_id", "listing_id"),
        Index("ix_review_audit_status", "status"),
    )
