import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Index
from database import Base


class TelegramNotificationLog(Base):
    __tablename__ = "telegram_notification_log"

    id               = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id          = Column(String(36), nullable=False)
    notification_key = Column(String(300), nullable=False)
    sent_at          = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_tnl_user_key",  "user_id", "notification_key"),
        Index("ix_tnl_user_sent", "user_id", "sent_at"),
    )
