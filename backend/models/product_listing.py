import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Boolean, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base


class ProductListing(Base):
    """
    Листинг (Doctrine §3) — представление физ.товара на КОНКРЕТНОМ маркетплейсе.

    Уровень листинга: продажи, реклама, отзывы, позиции, остатки. Связь с атомом
    через physical_product_id. Матчинг (§3.1): barcode → sku → fuzzy-name; при
    confidence < threshold требуется ручное подтверждение (confirmed=False).

    legacy_product_id — мост к существующей таблице products (она была per-MP,
    т.е. фактически листинг-уровня) без её удаления.
    """
    __tablename__ = "product_listings"

    id                 = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    physical_product_id = Column(String(36), ForeignKey("physical_products.id", ondelete="CASCADE"), nullable=False)
    user_id            = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    marketplace        = Column(String(20), nullable=False)   # wb | ozon | yandex | ...
    external_id        = Column(String(255), nullable=False)  # nmID / Ozon SKU / offerId
    title              = Column(String(500), nullable=True)
    legacy_product_id  = Column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)

    # Матчинг листинг→товар (§3.1)
    match_method     = Column(String(20), nullable=True)   # barcode | sku | name_fuzzy | manual
    match_confidence = Column(Float,   nullable=True)      # 0..1
    confirmed        = Column(Boolean, nullable=False, default=False, server_default="0")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    physical_product = relationship("PhysicalProduct", back_populates="listings")

    __table_args__ = (
        UniqueConstraint("user_id", "marketplace", "external_id", name="uq_listing_user_mp_ext"),
        Index("ix_listing_phys", "physical_product_id"),
        Index("ix_listing_user_mp", "user_id", "marketplace"),
    )
