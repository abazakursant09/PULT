import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Float, Integer, Boolean, Index
from database import Base


class Deal(Base):
    __tablename__ = "deals"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    seller_id       = Column(String(36), nullable=False)
    supplier_id     = Column(String(36), nullable=False)

    product_name    = Column(String(255), nullable=False)
    specification   = Column(Text,    nullable=True)
    price_per_unit  = Column(Float,   nullable=False)
    quantity        = Column(Integer, nullable=False)
    total_price     = Column(Float,   nullable=False)
    deadline        = Column(DateTime, nullable=True)

    # draft | agreed | paid | shipped | delivered | cancelled
    status          = Column(String(20), nullable=False, default="draft")

    contract_text   = Column(Text,    nullable=True)
    signed_by_seller = Column(Boolean, nullable=False, default=False)
    signed_at       = Column(DateTime, nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_deals_seller",   "seller_id"),
        Index("ix_deals_supplier", "supplier_id"),
        Index("ix_deals_status",   "status"),
    )
