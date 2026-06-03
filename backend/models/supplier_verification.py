import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, Index
from database import Base


class SupplierVerification(Base):
    __tablename__ = "supplier_verifications"

    id      = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # The user who submitted the application
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Company details
    company_name  = Column(String(255), nullable=False)
    country       = Column(String(10),  nullable=False, default="russia")  # russia | china

    # Russian fields
    inn           = Column(String(12),  nullable=True)
    ogrn          = Column(String(15),  nullable=True)
    legal_address = Column(String(500), nullable=True)
    phone         = Column(String(30),  nullable=True)
    website       = Column(String(255), nullable=True)

    # China fields
    uscc           = Column(String(18),  nullable=True)   # Unified Social Credit Code
    business_scope = Column(Text,        nullable=True)   # must contain "manufacturing"
    founded_year   = Column(Integer,     nullable=True)   # used to calc age >= 1 year

    # Verification result
    status              = Column(String(20),  nullable=False, default="pending")
    # pending | verified | rejected
    verification_source = Column(String(20),  nullable=True)
    # fns | 2gis | uscc | manual
    rejection_reason    = Column(Text,        nullable=True)
    verified_at         = Column(DateTime,    nullable=True)
    created_at          = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (
        Index("ix_supplier_verifications_user_id", "user_id"),
        Index("ix_supplier_verifications_status",  "status"),
    )
