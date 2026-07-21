import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Boolean, UniqueConstraint, Index
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
    # PULT-LAUNCH-1.3: Product is the CABINET-level card. marketplace_account_id ties it to the
    # owning cabinet (nullable until the 1.3 backfill proves an unambiguous account; a NOT NULL
    # promotion is a later, preflight-gated step). external_product_id is the marketplace card id
    # (nmID / Ozon product_id / Yandex offerId); `sku` stays the seller SKU. Same SKU in two
    # cabinets = two Products (different account); same external_product_id in one cabinet is
    # forbidden. SKU is deliberately NOT unique — a SKU conflict inside a cabinet is resolved by
    # the app with a flag, never merged silently.
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=True)
    external_product_id    = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # composite-FK target for product_placements(product_id, marketplace_account_id)
        UniqueConstraint("id", "marketplace_account_id", name="uq_product_id_account"),
        Index("uq_product_account_external", "marketplace_account_id", "external_product_id",
              unique=True,
              sqlite_where=external_product_id.isnot(None),
              postgresql_where=external_product_id.isnot(None)),
        Index("ix_product_account", "marketplace_account_id"),
        Index("ix_product_account_sku", "marketplace_account_id", "sku"),
    )

    user        = relationship("User",               back_populates="products")
    competitors = relationship("CompetitorAnalysis", back_populates="product",  cascade="all, delete-orphan")
    reviews     = relationship("ReviewResponse",     back_populates="product",  cascade="all, delete-orphan")
    pricing_rule = relationship("PricingRule",       back_populates="product",  cascade="all, delete-orphan", uselist=False)
    price_changes       = relationship("PriceChangeLog",      back_populates="product", cascade="all, delete-orphan")
    financial_snapshots = relationship("FinancialSnapshot",    back_populates="product", cascade="all, delete-orphan")
    legal_cases         = relationship("LegalCase",            back_populates="product", cascade="all, delete-orphan")