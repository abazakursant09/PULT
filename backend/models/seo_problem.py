"""
SEO Problem (SEO Engine data foundation, A2) — append-only detection record.

One immutable row per problem detected in a single audit run. Plain table mirror,
no rule logic. The lifecycle/status of a problem is NOT stored here — it lives on
the derived seo_signal (the seller-facing entity). `problem_type` / `severity` /
`estimated_effect_type` are canonical and marketplace-agnostic; `evidence` holds
deterministic facts only (no metrics). Append-only: no `updated_at`.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index
from database import Base


class SeoProblem(Base):
    __tablename__ = "seo_problem"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → seo_audit.id
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)      # provenance only
    sku         = Column(String(255), nullable=True)

    problem_type          = Column(String(40), nullable=False)   # canonical (Rule Catalog)
    category              = Column(String(40), nullable=True)    # canonical category
    severity              = Column(String(10), nullable=False)   # critical|high|medium|low
    estimated_effect_type = Column(String(40), nullable=True)    # qualitative (not money/metric)
    detectability         = Column(String(20), nullable=True)    # static_card|requires_search_data
    evidence              = Column(Text, nullable=True)          # JSON-encoded deterministic facts

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_seo_problem_audit", "audit_id"),
        Index("ix_seo_problem_user_listing_type", "user_id", "listing_id", "problem_type"),
    )
