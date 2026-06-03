import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Boolean
from sqlalchemy.orm import relationship
from database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    marketplace = Column(String(50), nullable=False)
    category = Column(String(255), nullable=True)
    sku = Column(String(255), nullable=True)
    price = Column(Float, nullable=True)
    auto_mode = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user        = relationship("User",               back_populates="products")
    competitors = relationship("CompetitorAnalysis", back_populates="product",  cascade="all, delete-orphan")
    reviews     = relationship("ReviewResponse",     back_populates="product",  cascade="all, delete-orphan")
    pricing_rule = relationship("PricingRule",       back_populates="product",  cascade="all, delete-orphan", uselist=False)
    price_changes       = relationship("PriceChangeLog",      back_populates="product", cascade="all, delete-orphan")
    financial_snapshots = relationship("FinancialSnapshot",    back_populates="product", cascade="all, delete-orphan")
    legal_cases         = relationship("LegalCase",            back_populates="product", cascade="all, delete-orphan")