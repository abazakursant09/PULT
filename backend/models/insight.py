import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Index
from database import Base


class InsightRecord(Base):
    __tablename__ = "insight_records"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(String(36), nullable=False)
    insight_key = Column(String(200), nullable=False)
    status      = Column(String(20), nullable=False, default="active")
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_insight_user_key", "user_id", "insight_key"),
    )
