import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Index, ForeignKey
from database import Base


class ImportedReturnRow(Base):
    """Returns ingestion foundation (Phase R0). One row per seller-uploaded returns report
    line — the third import_type alongside finance/products. Field shape mirrors
    ImportedFinanceRow (period rows keyed by date+sku). Ingestion only: NO diagnosis logic,
    NO producer, NOT in the Advisory Runtime registry, NOT in the Decision Feed. Fed by the
    file-upload path (csv_import → csv_parser), never a marketplace API call."""

    __tablename__ = "imported_return_rows"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    import_id     = Column(String(36), nullable=False, index=True)
    user_id       = Column(String(36), nullable=False)          # denormalized for fast queries
    marketplace   = Column(String(20), nullable=False)
    date          = Column(String(10), nullable=True)           # YYYY-MM-DD
    sku           = Column(String(255), nullable=True)
    returns_qty   = Column(Integer, nullable=False, default=0)  # observed returned units
    return_amount = Column(Float, nullable=False, default=0.0)  # observed returned value
    reason        = Column(String(255), nullable=True)          # optional return reason (as reported)
    # Product Spine (Step 1): canonical link. Nullable; a returns-only sku without a catalog
    # Product stays NULL (no auto-create from returns). SET NULL on delete.
    product_id    = Column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    # PULT-LAUNCH-1.3 store binding — see ImportedProductRow. account_id CASCADE, store_id SET NULL.
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=True)
    marketplace_store_id   = Column(String(36), ForeignKey("marketplace_stores.id", ondelete="SET NULL"), nullable=True)
    source        = Column(String(10), nullable=False, default="csv", server_default="csv")  # csv | api
    fetched_at    = Column(DateTime, nullable=True)
    # PULT-LAUNCH-1.4.4: linked | unassigned | conflict (see ImportedProductRow).
    link_status   = Column(String(10), nullable=False, default="unassigned", server_default="unassigned")
    created_at    = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_imp_returns_user_mp", "user_id", "marketplace"),
        Index("ix_imp_returns_product_id", "product_id"),
    )
