import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Boolean, Text, Index
from database import Base


class MarketplaceCategoryAttributeRow(Base):
    """Marketplace category attribute schema — GLOBAL Reference Data (Phase C2b).

    Reference Data per the Reference Data Doctrine: the per-category attribute schema
    (required / filterable / variant flags, type, length limit, allowed values) — global,
    marketplace-owned, NO user_id. Versioned current-state: immutable stored versions, latest-wins,
    replay uses the pinned version; `captured_at` freshness; `source` provenance.

    Inert schema foundation: no ingestion job, no producer, not in the registry, not in the
    Decision Feed, not called by the SEO engine (merges into CardSnapshot at build time, C2d). No
    marketplace API here. This is what later fills CardSnapshot.category_schema / required_attributes
    / constraints so constraint-dependent SEO rules can stop returning not_evaluated."""

    __tablename__ = "marketplace_category_attribute_rows"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace         = Column(String(20), nullable=False)
    category_id         = Column(String(64), nullable=False)
    attribute_id        = Column(String(64), nullable=False)
    name                = Column(String(500), nullable=False)
    type                = Column(String(40), nullable=True)      # text | number | dictionary | ...
    is_required         = Column(Boolean, nullable=False, default=False, server_default="0")
    is_filterable       = Column(Boolean, nullable=False, default=False, server_default="0")
    is_variant          = Column(Boolean, nullable=False, default=False, server_default="0")
    max_length          = Column(Integer, nullable=True)
    allowed_values_json = Column(Text, nullable=True)            # JSON list of allowed values
    captured_at         = Column(DateTime, nullable=False, default=datetime.utcnow)  # freshness
    version             = Column(String(40), nullable=True)      # reference version tag (replay pin)
    source              = Column(String(20), nullable=True)      # provenance: api_snapshot | manual
    created_at          = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_mp_cat_attr_marketplace", "marketplace"),
        Index("ix_mp_cat_attr_category_id", "category_id"),
        Index("ix_mp_cat_attr_mp_cat", "marketplace", "category_id"),
        Index("ix_mp_cat_attr_captured_at", "captured_at"),
    )
