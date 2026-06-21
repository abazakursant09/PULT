"""
SEO Audit (SEO Engine data foundation, A2) — append-only run record.

One row per SEO audit of a listing. NO rule logic, NO engine, NO signal building
lives here — this is a plain table mirror only. Marketplace-agnostic: `marketplace`
is provenance / dispatch context, NEVER a behavioural switch. Append-only by
contract: no `updated_at`, no mutable lifecycle columns; a run row is written
once. `score` is an internal deterministic health index, NOT a marketplace metric.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, DateTime, Index
from database import Base


class SeoAudit(Base):
    __tablename__ = "seo_audit"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String(36), nullable=False)    # soft ref → users.id
    listing_id   = Column(String(36), nullable=True)     # soft ref → product_listings.id
    marketplace  = Column(String(20), nullable=True)     # provenance / dispatch only
    sku          = Column(String(255), nullable=True)

    status               = Column(String(15), nullable=False, default="completed",
                                  server_default="completed")  # pending|running|completed|failed
    rule_catalog_version = Column(String(20), nullable=True)   # determinism marker
    snapshot_hash        = Column(String(64), nullable=True)   # dedup / throttle

    score                = Column(Float, nullable=True)        # internal health index (NOT a metric)
    total_problems       = Column(Integer, nullable=False, default=0, server_default="0")
    total_not_evaluated  = Column(Integer, nullable=False, default=0, server_default="0")
    top_severity         = Column(String(10), nullable=True)   # critical|high|medium|low

    triggered_by = Column(String(20), nullable=True)           # manual|scheduled|after_card_change
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_seo_audit_user_listing", "user_id", "listing_id"),
        Index("ix_seo_audit_status", "status"),
    )
