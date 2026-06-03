import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Float, Integer, Boolean, Index
from database import Base


class TransportCompany(Base):
    __tablename__ = "transport_companies"

    id               = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name             = Column(String(255), nullable=False)
    region           = Column(String(100), nullable=True)   # "Вся Россия" | "Москва и МО" | etc.
    delivery_types   = Column(String(200), nullable=True)   # comma-sep: auto,rail,air,express,cargo
    description      = Column(Text,        nullable=True)
    website          = Column(String(255), nullable=True)
    phone            = Column(String(50),  nullable=True)
    rating           = Column(Float,   nullable=False, default=0.0)
    total_reviews    = Column(Integer, nullable=False, default=0)
    price_per_kg     = Column(Float,   nullable=True)
    price_per_m3     = Column(Float,   nullable=True)
    min_transit_days = Column(Integer, nullable=True)
    max_transit_days = Column(Integer, nullable=True)
    is_seed          = Column(Boolean, nullable=False, default=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_tc_region", "region"),
    )
