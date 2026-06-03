import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Boolean, Float, Integer, Index
from database import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id       = Column(String(36), nullable=True)
    company_name  = Column(String(255), nullable=False)
    industry      = Column(String(100), nullable=True)
    region        = Column(String(100), nullable=True)
    country       = Column(String(10),  nullable=False, default="russia")
    description   = Column(Text,        nullable=True)
    website       = Column(String(255), nullable=True)
    phone         = Column(String(50),  nullable=True)
    min_order_qty = Column(Integer,     nullable=True)
    is_verified   = Column(Boolean,     nullable=False, default=False)
    is_seed       = Column(Boolean,     nullable=False, default=True)
    rating        = Column(Float,       nullable=False, default=0.0)
    total_reviews = Column(Integer,     nullable=False, default=0)
    total_deals   = Column(Integer,     nullable=False, default=0)
    created_at    = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (
        Index("ix_suppliers_industry", "industry"),
        Index("ix_suppliers_region",   "region"),
        Index("ix_suppliers_verified", "is_verified"),
    )
