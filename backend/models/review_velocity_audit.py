"""
Review Velocity Audit (Review Acquisition / Social-Proof Velocity Diagnosis contour data
foundation, Phase 6.0) — append-only run.

Review Velocity looks for stalling review acquisition — reviews_count growth flattening
relative to sales velocity, from the seller's OWN observed data over time. One row per
review-velocity diagnosis run of a listing. NO rule logic here — plain table mirror, modelled
field-for-field on growth_audit. Marketplace-agnostic: `marketplace` is provenance/dispatch
only. Append-only: written once per run. No fabricated velocity index — only deterministic
detected stall. Does NOT reuse rating_audit or review_audit — this is a distinct contour from
both Rating and Review.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Index
from database import Base


class ReviewVelocityAudit(Base):
    __tablename__ = "review_velocity_audit"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id
    listing_id   = Column(String(36), nullable=True)     # soft ref → product_listings.id
    marketplace  = Column(String(20), nullable=True)     # provenance / dispatch only
    sku          = Column(String(255), nullable=True)
    source       = Column(String(20), nullable=True)     # catalog | finance | manual

    status               = Column(String(15), nullable=False, default="completed",
                                  server_default="completed")  # pending|running|completed|failed
    rule_catalog_version = Column(String(20), nullable=True)
    snapshot_hash        = Column(String(64), nullable=True)

    total_problems       = Column(Integer, nullable=False, default=0, server_default="0")  # stalls found
    total_not_evaluated  = Column(Integer, nullable=False, default=0, server_default="0")
    top_severity         = Column(String(10), nullable=True)   # critical|high|medium|low

    triggered_by = Column(String(20), nullable=True)           # manual|scheduled|after_import
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_review_velocity_audit_user_listing", "user_id", "listing_id"),
        Index("ix_review_velocity_audit_status", "status"),
    )
