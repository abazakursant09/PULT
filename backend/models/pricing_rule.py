import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Boolean
from sqlalchemy.orm import relationship
from database import Base


class PricingRule(Base):
    __tablename__ = "pricing_rules"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"),
                        nullable=False, unique=True)
    min_price           = Column(Float,   nullable=False)
    max_price           = Column(Float,   nullable=False)
    target_position     = Column(String(50),  nullable=False, default="below_top_3")
    target_percent      = Column(Float,   nullable=False, default=5.0)  # competitor-relative (NOT a margin)
    target_margin_pct   = Column(Float,   nullable=True)  # seller net-margin target, percent (25.0=25%); null=unset
    reaction_threshold  = Column(Float,   nullable=False, default=3.0)
    frequency           = Column(String(20),  nullable=False, default="once_per_day")
    auto_mode           = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = relationship("Product", back_populates="pricing_rule")


class PriceChangeLog(Base):
    __tablename__ = "price_change_log"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    old_price  = Column(Float,      nullable=False)
    new_price  = Column(Float,      nullable=False)
    reason     = Column(String(512), nullable=False)
    source     = Column(String(20),  nullable=False)  # "auto" | "manual"
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="price_changes")
