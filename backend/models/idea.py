import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer
from database import Base


class Idea(Base):
    __tablename__ = "ideas"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(String(36), nullable=True)
    author_name = Column(String(100), nullable=False, default="Аноним")
    topic       = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    importance  = Column(String(20), nullable=False, default="хотелка")
    status      = Column(String(30), nullable=False, default="на рассмотрении")
    votes       = Column(Integer, nullable=False, default=1)
    created_at  = Column(DateTime, default=datetime.utcnow)
