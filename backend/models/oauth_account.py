import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Index
from database import Base


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"

    id               = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id          = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider         = Column(String(20),  nullable=False)   # google | apple | yandex
    provider_user_id = Column(String(255), nullable=False)
    email            = Column(String(255), nullable=True)
    name             = Column(String(255), nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_uid"),
        Index("ix_oauth_accounts_user_id", "user_id"),
    )
