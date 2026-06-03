import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from database import Base


class ReviewResponse(Base):
    __tablename__ = "review_responses"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    review_text = Column(Text, nullable=True)
    author = Column(String(255), nullable=True)
    rating = Column(Integer, nullable=True)
    response_text = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    # ── Marketplace Execution Layer (ME-2): real publish, not local imitation ──
    external_review_id = Column(String(120), nullable=True)   # WB/Ozon feedback id
    marketplace = Column(String(20), nullable=True)           # wildberries | ozon
    published_at = Column(DateTime, nullable=True)            # set only on real API success
    execution_log_id = Column(String(36), nullable=True)      # link to ExecutionLog of the publish
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = relationship("Product", back_populates="reviews")
