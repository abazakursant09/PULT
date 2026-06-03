import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, JSON, LargeBinary, Index
from database import Base


class ApiCredential(Base):
    """Encrypted marketplace API token, scoped, separated from the connection."""

    __tablename__ = "api_credentials"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_id = Column(String(36), nullable=False)
    scope         = Column(String(40), nullable=False)        # feedbacks|prices|advert|content|stocks|promotions
    secret_enc    = Column(LargeBinary, nullable=False)       # Fernet ciphertext — never plaintext
    meta          = Column(JSON, nullable=False, default=dict)
    expires_at    = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_apicred_conn", "connection_id"),
        Index("ix_apicred_conn_scope", "connection_id", "scope"),
    )
