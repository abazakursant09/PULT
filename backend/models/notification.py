import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index
from database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type       = Column(String(50),   nullable=False)
    title      = Column(String(255),  nullable=False)
    message    = Column(String(1000), nullable=False)
    is_read    = Column(Boolean, nullable=False, default=False, server_default='0')
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_notifications_user_id", "user_id"),)
