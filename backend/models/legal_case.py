import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class LegalCase(Base):
    __tablename__ = "legal_cases"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id     = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    case_type      = Column(String(50), nullable=False)   # review_response | card_audit
    status         = Column(String(20), nullable=False, default="open")  # open | resolved | escalated
    title          = Column(String(255), nullable=False)
    description    = Column(Text, nullable=False)
    risk_level     = Column(String(10), nullable=False)   # high | medium | low
    ai_recommendation = Column(Text, nullable=False)
    user_response  = Column(Text, nullable=True)
    review_id      = Column(String(36), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = relationship("Product", back_populates="legal_cases")
