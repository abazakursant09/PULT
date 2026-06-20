import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, Index
from database import Base


class Observation(Base):
    """
    Canonical metric observation — a single normalized fact (Metric Catalog
    foundation). Append-only: one row = one measured value of one canonical
    metric for one entity at one time. NEVER updated in place.

    Value is already normalized to the metric's canonical unit by the adapter
    before it reaches this table; no marketplace-specific field names live here.
    `marketplace` is provenance only (null at product grain). Interpretation
    (deltas, labels, learning) is explicitly NOT this layer's job.
    """
    __tablename__ = "observations"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String(36), nullable=False)
    entity_grain = Column(String(10), nullable=False)            # listing | product
    entity_id    = Column(String(255), nullable=True)            # agnostic entity ref
    metric_name  = Column(String(40), nullable=False)            # canonical (metric_catalog)
    marketplace  = Column(String(20), nullable=True)             # provenance; null at product grain
    value        = Column(Float, nullable=False)
    unit         = Column(String(16), nullable=False)            # canonical unit
    window_days  = Column(Integer, nullable=True)                # observation window
    observed_at  = Column(DateTime, nullable=False)              # as-of time of the value
    source       = Column(String(10), nullable=False)           # api | compute | forecast
    quality      = Column(String(40), nullable=True)            # sample-size / confidence note
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_observation_user_metric_entity", "user_id", "metric_name", "entity_id"),
        Index("ix_observation_user_observed", "user_id", "observed_at"),
    )
