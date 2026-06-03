import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Boolean, DateTime, Integer
from database import Base


class CreativeVariant(Base):
    __tablename__ = "creative_variants"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String(36), index=True, nullable=False)
    session_id     = Column(String(36), index=True, nullable=False)
    variant_name   = Column(String(100))
    preset         = Column(String(100))
    category       = Column(String(100))
    marketplace    = Column(String(50))
    product_name   = Column(String(255))
    creative_score = Column(Integer)
    grade          = Column(String(2))
    predicted_ctr  = Column(Float)
    chosen         = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.utcnow)
