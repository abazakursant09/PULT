import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Integer
from sqlalchemy.orm import relationship
from database import Base


class CompetitorAnalysis(Base):
    __tablename__ = "competitor_analysis"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    competitor_name = Column(String(255), nullable=False)
    competitor_url = Column(String(512), nullable=True)
    marketplace = Column(String(50), nullable=False)
    price = Column(Float, nullable=False)
    rating = Column(Float, nullable=True)
    reviews_count = Column(Integer, nullable=True)
    sales_estimate = Column(Integer, nullable=True)
    significance = Column(String(20), nullable=False)
    rank = Column(Integer, nullable=True)
    collected_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="competitors")