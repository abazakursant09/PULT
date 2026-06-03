import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class FinancialSnapshot(Base):
    __tablename__ = "financial_snapshots"

    id               = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id       = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    period           = Column(String(7),  nullable=False)   # "YYYY-MM"
    revenue          = Column(Float, nullable=False)
    marketplace_fee  = Column(Float, nullable=False)        # 2-5 % of revenue
    ad_spend         = Column(Float, nullable=False)        # 3-8 % of revenue
    cogs             = Column(Float, nullable=False)        # 40-60 % of revenue
    net_profit       = Column(Float, nullable=False)        # revenue - all costs
    margin_percent   = Column(Float, nullable=False)        # net_profit / revenue * 100
    created_at       = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="financial_snapshots")
