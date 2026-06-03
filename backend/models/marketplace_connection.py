import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, JSON, Index
from database import Base


class MarketplaceConnection(Base):
    """One connected seller cabinet (WB or Ozon). Tokens live in ApiCredential."""

    __tablename__ = "marketplace_connections"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String(36), nullable=False)
    marketplace    = Column(String(20), nullable=False)            # wildberries | ozon
    label          = Column(String(120), nullable=True)
    status         = Column(String(20), nullable=False, default="connected")  # connected|invalid|revoked
    scopes         = Column(JSON, nullable=False, default=list)    # ["feedbacks","prices",...]
    ozon_client_id = Column(String(64), nullable=True)             # Ozon needs Client-Id alongside key
    last_check_at  = Column(DateTime, nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_mp_conn_user", "user_id"),
        Index("ix_mp_conn_user_mp", "user_id", "marketplace"),
    )
