import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from database import Base


class SuccessStory(Base):
    __tablename__ = "success_stories"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title       = Column(String(255), nullable=False)
    text        = Column(Text, nullable=False)
    author_name = Column(String(100), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
