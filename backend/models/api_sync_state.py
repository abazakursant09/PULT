import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Integer, Index, ForeignKey, UniqueConstraint
from database import Base


class ApiSyncState(Base):
    """Per (connection, store, data_type) cursor + schedule for API ingestion (PULT-LAUNCH-1.4.5E).

    One row tracks how far one kind of data has been pulled for one store through one connection,
    so a restart resumes from the persisted cursor rather than starting over, and a failure backs
    off without touching any other connection.

    It stores ONLY control data. No token, no secret response body, no personal data, and no raw
    marketplace error text — `last_safe_error_code` is a short classifier (auth / rate_limit /
    timeout / marketplace_5xx / …), never the marketplace's message.
    """

    __tablename__ = "api_sync_states"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace_connection_id = Column(
        String(36), ForeignKey("marketplace_connections.id", ondelete="CASCADE"), nullable=False)
    marketplace_account_id = Column(
        String(36), ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=False)
    marketplace_store_id = Column(
        String(36), ForeignKey("marketplace_stores.id", ondelete="CASCADE"), nullable=False)
    data_type = Column(String(20), nullable=False)   # products | card_content | prices | ...

    # Opaque resume cursor for the provider (e.g. a WB updatedAt+nmID pair, serialized). Never a
    # page number or an array index — those are not stable across a changing dataset.
    cursor = Column(String(500), nullable=True)
    # The interval this state has fully covered, for the source policy in 1.4.5H. Left NULL for
    # snapshot data types that have no period.
    covered_from = Column(String(10), nullable=True)   # YYYY-MM-DD
    covered_to = Column(String(10), nullable=True)      # YYYY-MM-DD

    status = Column(String(20), nullable=False, default="pending")  # pending|running|synced|paused|failed
    last_attempt_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)       # earliest a scheduler may retry; NULL = now
    fail_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_safe_error_code = Column(String(32), nullable=True)   # a classifier, never the raw message
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("marketplace_connection_id", "marketplace_store_id", "data_type",
                         name="uq_api_sync_conn_store_type"),
        Index("ix_api_sync_next_run", "next_run_at"),
        Index("ix_api_sync_conn", "marketplace_connection_id"),
    )
