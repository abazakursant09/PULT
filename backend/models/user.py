import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email           = Column(String(255), unique=True, index=True, nullable=False)
    name            = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

    # Subscription plan: master | profi | maximum
    plan            = Column(String(50), nullable=False, default='master', server_default='master')
    # LEGAL-1A — the user-to-user "Биржа" chat was removed (149-FZ art. 10.1). These two
    # moderation columns are now INERT: no code reads or writes them after chat removal. They are
    # retained only so this cleanup did not require a users-table migration; they hold no message
    # content. Safe to drop in a later dedicated migration if desired.
    chat_violations = Column(Integer,  nullable=False, default=0,    server_default='0')
    chat_blocked    = Column(Boolean,  nullable=False, default=False, server_default='0')

    # Email verification (DEFAULT 1 so existing users stay verified after migration)
    is_verified         = Column(Boolean,  nullable=False, default=True,  server_default='1')
    verification_token  = Column(String(255), nullable=True)

    # Password reset
    reset_token         = Column(String(255), nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)

    # SECURITY-2C-1 — server-side session revocation. Every issued access JWT carries the user's
    # token_version at issue time (claim `ver`); get_current_user rejects a JWT whose `ver` != this.
    # Incremented (atomic SQL) on logout, on successful password reset, and on account deletion, so a
    # copied cookie dies immediately. Server-controlled, monotonically increasing, never user input.
    token_version       = Column(Integer, nullable=False, default=0, server_default='0')

    # Telegram
    telegram_chat_id = Column(String(100), nullable=True)

    # ── Referral system ─────────────────────────────────────────────────────────
    referral_code     = Column(String(20),  nullable=True, unique=True)
    referred_by_id    = Column(String(36),  nullable=True)   # FK-like, no hard FK for SQLite

    # ── Soft delete / Account recovery ─────────────────────────────────────────
    deleted_at    = Column(DateTime,  nullable=True)
    was_referrer  = Column(Boolean,   nullable=False, default=False, server_default='0')
    was_referred  = Column(Boolean,   nullable=False, default=False, server_default='0')
    is_restored   = Column(Boolean,   nullable=False, default=False, server_default='0')
    registered_ip = Column(String(45), nullable=True)

    # Subscription expiry (set when payment activates plan)
    subscription_end_date = Column(DateTime, nullable=True)

    products = relationship("Product", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
