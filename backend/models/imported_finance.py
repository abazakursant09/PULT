import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Index, ForeignKey
from database import Base


class ImportedFinanceRow(Base):
    __tablename__ = "imported_finance_rows"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    import_id   = Column(String(36), nullable=False, index=True)
    user_id     = Column(String(36), nullable=False)          # denormalized for fast queries
    marketplace = Column(String(20), nullable=False)
    date        = Column(String(10), nullable=True)           # YYYY-MM-DD
    sku         = Column(String(255), nullable=True)
    title       = Column(String(500), nullable=True)
    revenue     = Column(Float, nullable=False, default=0.0)
    commission  = Column(Float, nullable=False, default=0.0)
    logistics   = Column(Float, nullable=False, default=0.0)
    ad_spend    = Column(Float, nullable=False, default=0.0)
    net_profit  = Column(Float, nullable=False, default=0.0)
    quantity    = Column(Integer, nullable=False, default=0)
    # Product Spine (Step 1): canonical link. Nullable; finance-only sku without
    # a catalog Product stays NULL (no auto-create from finance). SET NULL on delete.
    product_id  = Column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_imp_finance_user_mp", "user_id", "marketplace"),
        Index("ix_imp_finance_product_id", "product_id"),
    )
