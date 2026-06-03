import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, Text, Index
from database import Base


class SeoRebuild(Base):
    """
    Tracks every SEO card generation. Enables style learning and winner detection.
    Metrics (ctr_after, delta_ctr_percent, etc.) are filled in post-measurement
    via PATCH /api/rebuild/{id}/metrics — not populated at generation time.
    """
    __tablename__ = "seo_rebuilds"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id       = Column(String(36), nullable=False, index=True)
    product_name  = Column(String(255), nullable=False, default="")
    marketplace   = Column(String(20),  nullable=False, default="all")
    category      = Column(String(50),  nullable=False, default="auto")
    preset        = Column(String(50),  nullable=False, default="premium")
    typography_preset = Column(String(50), nullable=True)

    # Normalized style features (derived from preset + typography at write time)
    bigger_product_mode = Column(Boolean, nullable=True)
    minimal_text_mode   = Column(Boolean, nullable=True)
    warm_gradient_mode  = Column(Boolean, nullable=True)
    high_contrast_mode  = Column(Boolean, nullable=True)

    # Rebuild reason + structured context
    rebuild_reason       = Column(String(60), nullable=False, default="manual_user_request")
    rebuild_context_json = Column(Text, nullable=True)  # {"recommendation": "...", "expected_gain_rub": 12000}

    # Performance metrics — filled later by user or integration
    ctr_before        = Column(Float, nullable=True)
    ctr_after         = Column(Float, nullable=True)
    delta_ctr_percent = Column(Float, nullable=True)
    impressions_count = Column(Integer, nullable=True)
    revenue_before    = Column(Float, nullable=True)
    revenue_after     = Column(Float, nullable=True)
    delta_revenue     = Column(Float, nullable=True)

    # Impact
    expected_gain_rub = Column(Float, nullable=True)
    actual_gain_rub   = Column(Float, nullable=True)
    impact_score      = Column(Integer, nullable=True)  # 0–100

    # Winner detection (computed when metrics are submitted)
    winner           = Column(Boolean, nullable=False, default=False, server_default='0')
    confidence_level = Column(String(10), nullable=False, default="low", server_default="'low'")

    # Timestamps
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    measured_at  = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_seo_rebuild_user_id",        "user_id"),
        Index("ix_seo_rebuild_user_product",   "user_id", "product_name"),
        Index("ix_seo_rebuild_user_mp_cat",    "user_id", "marketplace", "category"),
        Index("ix_seo_rebuild_user_created",   "user_id", "created_at"),
    )
