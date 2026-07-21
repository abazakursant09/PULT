import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Index, UniqueConstraint,
    ForeignKeyConstraint, CheckConstraint,
)
from database import Base


class ProductPlacement(Base):
    """Присутствие Product в конкретном MarketplaceStore (PULT-LAUNCH-1.1/1.2,
    level 4 of Кабинет→Магазин→Товар→Размещение).

    The EXPLICIT product↔store link, so a product placed in a store is known even
    with zero sales and zero stock — metrics alone could never prove membership.

    Cross-cabinet writes are impossible at the DB level: the single
    marketplace_account_id column feeds TWO composite FKs — one to
    products(id, marketplace_account_id), one to
    marketplace_stores(id, marketplace_account_id) — so the product and the store
    must belong to the SAME cabinet. A plain per-column FK could not express that.

    Never orphaned: both composite FKs cascade, so deleting the Product OR the Store
    removes the placement. status carries lifecycle (active | detached by the seller
    | vanished from the API) instead of deleting history.
    """
    __tablename__ = "product_placements"

    id                     = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id             = Column(String(36), nullable=False)
    marketplace_store_id   = Column(String(36), nullable=False)
    # denormalized cabinet id: forced equal to both parents by the two composite FKs
    marketplace_account_id = Column(String(36), nullable=False)
    external_offer_id      = Column(String(255), nullable=True)   # per-store offer id if the API exposes one
    seller_sku             = Column(String(255), nullable=True)
    status                 = Column(String(20), nullable=False, default="active", server_default="active")  # active | detached | vanished
    source                 = Column(String(10), nullable=False, default="manual", server_default="manual")  # manual | csv | api
    first_seen_at          = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at           = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "marketplace_account_id"],
            ["products.id", "products.marketplace_account_id"],
            ondelete="CASCADE", name="fk_placement_product",
        ),
        ForeignKeyConstraint(
            ["marketplace_store_id", "marketplace_account_id"],
            ["marketplace_stores.id", "marketplace_stores.marketplace_account_id"],
            ondelete="CASCADE", name="fk_placement_store",
        ),
        UniqueConstraint("marketplace_store_id", "product_id", name="uq_placement_store_product"),
        Index("uq_placement_store_offer", "marketplace_store_id", "external_offer_id",
              unique=True,
              sqlite_where=external_offer_id.isnot(None),
              postgresql_where=external_offer_id.isnot(None)),
        CheckConstraint(
            "external_offer_id IS NULL OR "
            "(external_offer_id <> '' AND external_offer_id = trim(external_offer_id) "
            "AND external_offer_id NOT LIKE '% %')",
            name="ck_placement_offer_clean",
        ),
        Index("ix_placement_product", "product_id"),
        Index("ix_placement_store", "marketplace_store_id"),
    )
