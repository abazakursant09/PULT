import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean
from database import Base


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email      = Column(String(255), nullable=False, index=True)
    ip_address = Column(String(45), nullable=True)
    success    = Column(Boolean, nullable=False)
    action     = Column(String(100), nullable=True)   # "login" | "register" | "mfa_verify"
    reason     = Column(String(255), nullable=True)   # failure reason
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
