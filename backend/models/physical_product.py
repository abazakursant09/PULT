import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Boolean, ForeignKey, Index
from sqlalchemy.orm import relationship
from database import Base


class PhysicalProduct(Base):
    """
    Атом системы (Doctrine §3) — ФИЗИЧЕСКИЙ товар, не листинг.

    Под ним живут ProductListing по маркетплейсам. На этом уровне — то, что
    ОБЩЕЕ для всех листингов: себестоимость, документы, бренд, товарный знак.
    """
    __tablename__ = "physical_products"

    id        = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id   = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title     = Column(String(500), nullable=False)
    barcode   = Column(String(64),  nullable=True)   # EAN — основной ключ матчинга (§3.1)
    seller_sku = Column(String(255), nullable=True)  # собственный SKU селлера
    brand     = Column(String(255), nullable=True)

    # Экономика (§14): COGS добровольна. cogs_source — источниковость (§6.1).
    cogs        = Column(Float,   nullable=True)
    cogs_source = Column(String(20), nullable=True)  # manual | yandex_api | null

    # Юр. блок. Статусов нет в API маркетплейсов (§6.1) — по умолчанию 'unknown'.
    trademark_status = Column(String(20), nullable=False, default="unknown", server_default="unknown")  # unknown|protected|not_protected
    trademark_source = Column(String(20), nullable=True)   # rospatent | manual | null
    chestny_znak_required = Column(Boolean, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    listings = relationship("ProductListing", back_populates="physical_product",
                            cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_phys_prod_user", "user_id"),
        Index("ix_phys_prod_barcode", "user_id", "barcode"),
    )
