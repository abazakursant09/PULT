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
    created_at    = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_imp_product_user_mp", "user_id", "marketplace"),
        Index("ix_imp_product_product_id", "product_id"),
    )
