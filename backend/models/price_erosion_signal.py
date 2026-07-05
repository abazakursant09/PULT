"""
Price Erosion / Discount Creep Signal (Price Erosion Diagnosis contour data foundation,
Phase 8.0) — seller-facing.

Business-Diagnosis view of PRICE EROSION / DISCOUNT CREEP — a product's own realized price
drifting DOWN across the seller's OWN dated snapshots (ImportedProductRow.price), compressing
margin. This reopens the margin_erosion question Money Leak deferred: Money Leak had no
price-change history, but dated price snapshots now supply observed own-price history at import
cadence. `insight_key` (`price_erosion_<problem_type>:<marketplace>:<sku>`) anchors a future
promote into the Decision Spine; `marketplace` is the Learning OS context dimension. `category`
keeps the domain tag for architectural parity with the other contour signal tables.

Price Erosion is DISTINCT from the executable Pricing contour (which ACTS on price) — this is
PURE DIAGNOSIS of observed own-price decline, no price-write action. Also distinct from Money
Leak (cost-share drift), Revenue, and Overstock. Diagnosis-only — no treatment/lever, no
forecast, no benchmark, no competitor compare. The schema deliberately mirrors GrowthSignal
field-for-field for architectural consistency (status / decision_id / priority_level /
recommended_action / expected_effect kept intentionally).

Plain table mirror, no signal-building logic — schema only (Phase 8.0). The lifecycle entity
(status mutates via reconciliation later) carries evidence_hash + updated_at; the detection
layer stays strictly append-only. Observed present erosion only — no fabricated number.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Float, DateTime, Index
from database import Base


class PriceErosionSignal(Base):
    __tablename__ = "price_erosion_signal"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → price_erosion_audit.id
    problem_id  = Column(String(36), nullable=True)      # soft ref → price_erosion_problem.id (future)
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id (seller)
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)      # context dimension (Learning OS)
    sku         = Column(String(255), nullable=True)

    signal_key              = Column(String(64), nullable=False)  # canonical price_erosion_<problem_type>
    insight_key             = Column(String(64), nullable=True)   # price_erosion_<ptype>:<mp>:<sku>
    problem_type            = Column(String(40), nullable=False)
    category                = Column(String(20), nullable=True)   # domain tag (parity with other contours)
    recommended_action_key  = Column(String(64), nullable=True)
    alternative_action_keys = Column(Text, nullable=True)         # JSON list

    # PULT doctrine, 5 parts (deterministic text, no fabricated numbers)
    what            = Column(Text, nullable=True)
    why             = Column(Text, nullable=True)
    meaning         = Column(Text, nullable=True)
    what_to_do      = Column(Text, nullable=True)
    expected_effect = Column(Text, nullable=True)

    priority_level = Column(String(10), nullable=True)    # critical|high|medium|low (erosion severity)
    effect_type    = Column(String(40), nullable=True)    # margin_compression|... (deterministic class)
    effect_band    = Column(String(10), nullable=True)    # high|medium|low
    confidence     = Column(Float, nullable=True)

    status        = Column(String(20), nullable=False, default="active",
                           server_default="active")  # active|dismissed|promoted_to_decision|resolved|reopened
    evidence_hash = Column(String(64), nullable=True)
    decision_id   = Column(String(36), nullable=True)  # soft ref → decisions.id (set on promote, later)

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at  = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_price_erosion_signal_user_listing", "user_id", "listing_id"),
        Index("ix_price_erosion_signal_insight", "insight_key"),
        Index("ix_price_erosion_signal_audit", "audit_id"),
        Index("ix_price_erosion_signal_status", "status"),
        Index("ix_price_erosion_signal_category", "category"),
    )
