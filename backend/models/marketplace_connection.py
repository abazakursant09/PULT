import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Index
from database import Base


class MarketplaceConnection(Base):
    """The current credential binding to one seller cabinet. Tokens live in ApiCredential.

    Not the cabinet's identity: that is `MarketplaceAccount`, which this row points at
    and which outlives any rotation or reconnect here (F1.1).
    """

    __tablename__ = "marketplace_connections"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String(36), nullable=False)
    marketplace    = Column(String(20), nullable=False)            # wildberries | ozon
    label          = Column(String(120), nullable=True)
    status         = Column(String(20), nullable=False, default="connected")  # connected|invalid|revoked
    scopes         = Column(JSON, nullable=False, default=list)    # ["feedbacks","prices",...]
    ozon_client_id = Column(String(64), nullable=True)             # Ozon needs Client-Id alongside key
    last_check_at  = Column(DateTime, nullable=True)
    # Identity links (F1.1). NULLABLE: rows predating F1.1 whose user has no workspace
    # cannot be backfilled (user_id carries no FK, so it may not resolve at all), and a
    # NOT NULL column would make the migration destructive for them. The API path always
    # populates both; tightening to NOT NULL waits until every writer is proven to.
    workspace_id           = Column(String(36), ForeignKey("workspaces.id"), nullable=True)
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_mp_conn_user", "user_id"),
        Index("ix_mp_conn_user_mp", "user_id", "marketplace"),
        Index("ix_mp_conn_account", "marketplace_account_id"),
    )
