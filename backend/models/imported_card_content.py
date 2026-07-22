import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Index, ForeignKey
from database import Base


class ImportedCardContentRow(Base):
    """Card-Content ingestion foundation (Phase C0) — UPLOAD Evidence for SEO.

    One row per seller-uploaded card-content report line — the fourth import_type alongside
    finance/products/returns. Holds the card CONTENT fields the SEO engine's CardSnapshot needs
    (title/description/brand/category/characteristics/media) which internal data does not carry
    today. Dated snapshot rows; field shape mirrors ImportedReturnRow / ImportedProductRow with a
    nullable Product Spine link.

    Ingestion only: NO diagnosis logic, NO producer, NOT in the Advisory Runtime registry, NOT in
    the Decision Feed. Fed by the file-upload path (csv_import → csv_parser), never a marketplace
    API call. Per the Evidence Source Doctrine this is UPLOAD Evidence; category schema / required
    attributes / marketplace constraints remain a separate API-Snapshot Evidence gap."""

    __tablename__ = "imported_card_content_rows"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    import_id           = Column(String(36), nullable=False, index=True)
    user_id             = Column(String(36), nullable=False)          # denormalized for fast queries
    marketplace         = Column(String(20), nullable=False)
    date                = Column(String(10), nullable=True)           # YYYY-MM-DD (capture date)
    sku                 = Column(String(255), nullable=True)
    title               = Column(String(500), nullable=True)
    description         = Column(Text, nullable=True)
    brand               = Column(String(255), nullable=True)
    category            = Column(String(255), nullable=True)          # category path / leaf as reported
    characteristics_json = Column(Text, nullable=True)                # JSON {key: value} of attributes
    image_count         = Column(Integer, nullable=True)
    image_urls_json     = Column(Text, nullable=True)                 # JSON list of image URLs (if reported)
    # Product Spine (Step 1): canonical link. Nullable; a card-only sku without a catalog Product
    # stays NULL (no auto-create from card content). SET NULL on delete.
    product_id          = Column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    # PULT-LAUNCH-1.3: card content is CABINET-level (shared across a cabinet's stores), so it gets
    # account_id (CASCADE — cabinet delete purges seller cards) but deliberately NO store_id.
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=True)
    source              = Column(String(10), nullable=False, default="csv", server_default="csv")  # csv | api
    fetched_at          = Column(DateTime, nullable=True)
    # PULT-LAUNCH-1.4.4: linked | unassigned | conflict (see ImportedProductRow).
    link_status         = Column(String(10), nullable=False, default="unassigned", server_default="unassigned")
    created_at          = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_imp_card_user_mp", "user_id", "marketplace"),
        Index("ix_imp_card_product_id", "product_id"),
    )
