import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Index, ForeignKey, text
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
    # Stable external id of an API row, for idempotency (PULT-LAUNCH-1.4.5E). For Wildberries this
    # is the nmID — a stable business id, never a page number, array index, uuid, title or
    # fetched_at. NULL for CSV rows, so the partial unique below never constrains them.
    external_row_id = Column(String(64), nullable=True)
    # PULT-LAUNCH-1.4.4: linked (product_id set) | unassigned (no product) | conflict (>1 candidate,
    # never auto-picked; awaits the seller). conflict/unassigned keep product_id NULL.
    link_status   = Column(String(10), nullable=False, default="unassigned", server_default="unassigned")
    created_at    = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_imp_product_user_mp", "user_id", "marketplace"),
        Index("ix_imp_product_product_id", "product_id"),
        # One API snapshot per (cabinet, store, external id): a repeated page or a repeated full
        # sync updates the row in place instead of inserting a second. Partial on the non-null
        # external_row_id, so CSV rows (external_row_id NULL) are never constrained.
        Index("uq_imp_product_api_row", "marketplace_account_id", "marketplace_store_id",
              "source", "external_row_id", unique=True,
              sqlite_where=text("external_row_id IS NOT NULL"),
              postgresql_where=text("external_row_id IS NOT NULL")),
    )
