import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Index
from database import Base


class UserEvent(Base):
    __tablename__ = "user_events"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(String(36), nullable=False, index=True)
    event_type  = Column(String(64),  nullable=False)
    event_scope = Column(String(64),  nullable=False, default="unknown")
    entity_id   = Column(String(255), nullable=True)
    metadata_json = Column(Text,      nullable=True)
    created_at  = Column(DateTime,    nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_user_events_user_created", "user_id", "created_at"),
        Index("ix_user_events_user_type",    "user_id", "event_type"),
    )
