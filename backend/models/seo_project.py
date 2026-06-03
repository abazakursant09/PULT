import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime, String, Text
from database import Base


class SeoProject(Base):
    __tablename__ = "seo_projects"

    id                = Column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id           = Column(String(36),  index=True, nullable=False)
    name              = Column(String(255), nullable=False, default="")
    product_name      = Column(String(255), nullable=False, default="")
    marketplace       = Column(String(20),  nullable=False, default="all")
    preset            = Column(String(50),  nullable=False, default="premium")
    category          = Column(String(50),  nullable=False, default="auto")
    typography_preset = Column(String(50),  default="wb-aggressive")
    current_price     = Column(String(50),  default="")
    old_price         = Column(String(50),  default="")
    advantages_json   = Column(Text,        default="[]")
    template_set      = Column(String(50),  default="default")
    image_urls_json   = Column(Text,        default="[]")
    created_at        = Column(DateTime,    default=datetime.utcnow)
