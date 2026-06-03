import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class Payment(Base):
    __tablename__ = "payments"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id             = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    yookassa_payment_id = Column(String(64), nullable=False, unique=True, index=True)
    amount              = Column(Numeric(10, 2), nullable=False)
    tariff              = Column(String(20), nullable=False)   # basic | pro
    plan                = Column(String(20), nullable=False)   # master | profi
    status              = Column(String(20), nullable=False, default="pending")  # pending | succeeded | canceled
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="payments")
