import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Index, ForeignKey
from database import Base


class ImportedProductRow(Base):
    __tablename__ = "imported_product_rows"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    import_id     = Column(String(36), nullable=False, index=True)
    user_id       = Column(String(36), nullable=False)
    marketplace   = Column(String(20), nullable=False)
    sku           = Column(String(255), nullable=False)
    title         = Column(String(500), nullable=True)
    price         = Column(Float,   nullable=True)
    stock         = Column(Integer, nullable=True)
    rating        = Column(Float,   nullable=True)
    reviews_count = Column(Integer, nullable=True)
    # Product Spine (Step 1): canonical link. Nullable until backfill coverage
    # is proven; SET NULL so deleting a Product never drops import history.
    product_id    = Column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    # PULT-LAUNCH-1.3 store binding. account_id CASCADE: deleting a cabinet purges all its
    # commercial rows (no seller data left behind). store_id SET NULL: archiving/removing ONE
    # store must keep the cabinet's history (money/stock), so the row stays linked to the account
    # with store_id cleared — never lost. source/fetched_at prepare API+CSV provenance.
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=True)
    marketplace_store_id   = Column(String(36), ForeignKey("marketplace_stores.id", ondelete="SET NULL"), nullable=True)
    source        = Column(String(10), nullable=False, default="csv", server_default="csv")  # csv | api
    fetched_at    = Column(DateTime, nullable=True)
    # PULT-LAUNCH-1.4.4: linked (product_id set) | unassigned (no product) | conflict (>1 candidate,
    # never auto-picked; awaits the seller). conflict/unassigned keep product_id NULL.
    link_status   = Column(String(10), nullable=False, default="unassigned", server_default="unassigned")
    created_at    = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_imp_product_user_mp", "user_id", "marketplace"),
        Index("ix_imp_product_product_id", "product_id"),
    )
