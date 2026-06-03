import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, UniqueConstraint
from database import Base


class MonitorEvent(Base):
    __tablename__ = "monitor_events"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title           = Column(String(255), nullable=False)
    description     = Column(Text,        nullable=False)
    source          = Column(String(50),  nullable=False)
    severity        = Column(String(20),  nullable=False)
    affected_module = Column(String(50),  nullable=False)
    action_required = Column(Text,        nullable=False)
    created_at      = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("title", name="uq_monitor_event_title"),)
