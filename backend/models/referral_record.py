import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Index, UniqueConstraint
from database import Base


class ReferralRecord(Base):
    __tablename__ = "referral_records"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    referrer_id         = Column(String(36), nullable=False)
    invitee_id          = Column(String(36), nullable=False)
    joined_at           = Column(DateTime, default=datetime.utcnow)
    paid_at             = Column(DateTime, nullable=True)
    is_paid             = Column(Boolean, nullable=False, default=False)
    # True once: is_paid=True AND account age >= 30 days AND not deleted early
    is_valid            = Column(Boolean, nullable=False, default=False)
    invalidated_at      = Column(DateTime, nullable=True)
    invalidation_reason = Column(String(100), nullable=True)

    __table_args__ = (
        Index("ix_referral_referrer", "referrer_id"),
        UniqueConstraint("invitee_id", name="uq_referral_invitee"),
    )
