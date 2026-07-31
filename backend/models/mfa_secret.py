import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, BigInteger, ForeignKey, CheckConstraint
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
    # SECURITY-2C-3A — the newest TOTP step (unix_time // 30) already spent on this account. A verify
    # only accepts a code whose matched step is strictly greater, so a code can be used at most once
    # (replay guard). NULL = no step spent yet (first-ever verify). Stores the step counter, never the
    # code. Advanced atomically via routers.mfa.claim_totp_step (UPDATE ... RETURNING).
    last_totp_step = Column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint("last_totp_step IS NULL OR last_totp_step >= 0",
                        name="ck_mfa_last_totp_step_nonneg"),
    )
