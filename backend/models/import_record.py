import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Text
from database import Base


class ImportRecord(Base):
    __tablename__ = "import_records"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id       = Column(String(36), nullable=False, index=True)
    filename      = Column(String(255), nullable=False)
    file_hash     = Column(String(64),  nullable=False)           # SHA-256
    marketplace   = Column(String(20),  nullable=False)           # wb | ozon | ym
    import_type   = Column(String(20),  nullable=False)           # finance | products
    status        = Column(String(20),  nullable=False, default="pending")  # pending | confirmed | failed
    temp_path     = Column(String(500), nullable=True)            # cleared after confirm
    total_rows    = Column(Integer, nullable=False, default=0)
    valid_rows    = Column(Integer, nullable=False, default=0)
    skipped_rows  = Column(Integer, nullable=False, default=0)
    imported_count = Column(Integer, nullable=False, default=0)
    warnings_json  = Column(Text, nullable=True)                  # JSON list[str]
    created_at    = Column(DateTime, default=datetime.utcnow)
    confirmed_at  = Column(DateTime, nullable=True)
