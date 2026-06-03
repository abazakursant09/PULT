import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Index
from database import Base


class SupplierReview(Base):
    __tablename__ = "supplier_reviews"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    reviewer_id  = Column(String(36), nullable=False)
    # target_type: supplier | transport_company
    target_type  = Column(String(20), nullable=False)
    target_id    = Column(String(36), nullable=False)
    deal_id      = Column(String(36), nullable=True)
    rating       = Column(Integer,    nullable=False)   # 1–5
    text         = Column(Text,       nullable=True)
    created_at   = Column(DateTime,   default=datetime.utcnow)

    __table_args__ = (
        Index("ix_sr_target",   "target_type", "target_id"),
        Index("ix_sr_reviewer", "reviewer_id"),
    )
