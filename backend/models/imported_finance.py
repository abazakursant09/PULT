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
    # PULT-LAUNCH-1.3 store binding — see ImportedProductRow. account_id CASCADE (cabinet delete
    # purges money history), store_id SET NULL (store archive/removal keeps cabinet totals).
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=True)
    marketplace_store_id   = Column(String(36), ForeignKey("marketplace_stores.id", ondelete="SET NULL"), nullable=True)
    source      = Column(String(10), nullable=False, default="csv", server_default="csv")  # csv | api
    fetched_at  = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_imp_finance_user_mp", "user_id", "marketplace"),
        Index("ix_imp_finance_product_id", "product_id"),
    )
