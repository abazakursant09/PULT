import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Index
from database import Base


class MarketplaceCategoryRow(Base):
    """Marketplace category tree — GLOBAL Reference Data (Phase C2b).

    Reference Data per the Reference Data Doctrine: a global, marketplace-owned fact — NOT a
    per-seller observation, so there is deliberately NO user_id. Versioned current-state: a new
    authoritative version supersedes the prior one; stored versions are immutable and kept for
    replay (a diagnosis pins the version it used). `captured_at` gives freshness; `source` is
    provenance (e.g. api_snapshot). Latest-wins by (marketplace, category_id, captured_at/version).

    Inert schema foundation: no ingestion job, no producer, not in the Advisory Runtime registry,
    not in the Decision Feed. Not called by the SEO engine — it merges into the CardSnapshot at
    snapshot-build time (a later slice, C2d). No marketplace API here."""

    __tablename__ = "marketplace_category_rows"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace = Column(String(20), nullable=False)              # wb | ozon | yandex | ...
    category_id = Column(String(64), nullable=False)             # marketplace category / subject id
    parent_id   = Column(String(64), nullable=True)
    name        = Column(String(500), nullable=False)
    path        = Column(String(1000), nullable=True)            # ">"-joined category path
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # freshness
    version     = Column(String(40), nullable=True)              # reference version tag (replay pin)
    source      = Column(String(20), nullable=True)              # provenance: api_snapshot | manual
    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_mp_category_marketplace", "marketplace"),
        Index("ix_mp_category_category_id", "category_id"),
        Index("ix_mp_category_mp_cat", "marketplace", "category_id"),
        Index("ix_mp_category_captured_at", "captured_at"),
    )
