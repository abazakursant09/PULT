import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from database import Base


class MFASecret(Base):
    __tablename__ = "mfa_secrets"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, unique=True, index=True)
    # Holds a Fernet token (services/mfa_crypto), not the bare seed. Widened from 64 because
    # ciphertext (~120 chars) does not fit the old base32-seed width. Legacy plaintext rows
    # still fit and are read back transparently by mfa_crypto.load_secret.
    secret     = Column(String(255), nullable=False)
    enabled    = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow)
