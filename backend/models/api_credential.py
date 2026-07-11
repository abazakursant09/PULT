import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, JSON, LargeBinary, ForeignKey, Index, UniqueConstraint,
)
from database import Base


class ApiCredential(Base):
    """Encrypted marketplace API token, scoped, separated from the connection.

    `(connection_id, scope)` is the logical identity of a credential — the router has
    always treated it that way (find-or-update), and since F1.2a the database enforces
    it. Without the constraint two rows could describe the same credential and every
    reader does `.first()`, so which token reached the marketplace would depend on the
    query plan.

    The FK carries NO ON DELETE clause, matching the real lifecycle: a connection is
    soft-revoked (`status = "revoked"`, routers/connections.py), never hard-deleted, so
    the referenced row does not disappear. A cascade would therefore never fire in
    production while quietly arming a path that destroys ciphertext.

    `expires_at` is stored metadata only: F1.2a does NOT enforce expiry, does not
    refresh, and keeps no rotation history. Nothing reads it today. It is kept because
    a future credential slice will populate it — not because it means anything now.
    """

    __tablename__ = "api_credentials"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_id = Column(String(36), ForeignKey("marketplace_connections.id"), nullable=False)
    scope         = Column(String(40), nullable=False)        # feedbacks|prices|advert|content|stocks|promotions
    secret_enc    = Column(LargeBinary, nullable=False)       # Fernet ciphertext — never plaintext
    meta          = Column(JSON, nullable=False, default=dict)
    expires_at    = Column(DateTime, nullable=True)           # stored, NOT enforced (see docstring)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("connection_id", "scope", name="uq_apicred_conn_scope"),
        Index("ix_apicred_conn", "connection_id"),
        Index("ix_apicred_conn_scope", "connection_id", "scope"),
    )
