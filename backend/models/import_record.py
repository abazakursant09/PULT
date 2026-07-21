import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Text, ForeignKey
from database import Base


class ImportRecord(Base):
    __tablename__ = "import_records"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id       = Column(String(36), nullable=False, index=True)
    filename      = Column(String(255), nullable=False)
    file_hash     = Column(String(64),  nullable=False)           # SHA-256
    # PULT-LAUNCH-1.4.2: NEW records store the FULL identity marketplace (wildberries|ozon|yandex).
    # Legacy rows may hold short wb|ozon|ym and are read through the identity normalizer.
    marketplace   = Column(String(20),  nullable=False)
    import_type   = Column(String(20),  nullable=False)           # finance | products | returns | card_content
    # Status machine (1.4.2): pending → processing → confirmed | failed; expired = temp file gone.
    status        = Column(String(20),  nullable=False, default="pending")
    temp_path     = Column(String(500), nullable=True)            # cleared after confirm
    # Store binding (1.4.2). NULLABLE: legacy rows predate stores and cannot be confirmed without
    # re-upload. account_id CASCADE — deleting a cabinet removes its import records (commercial-data
    # policy); store_id SET NULL — archiving/removing ONE store keeps the record in the cabinet.
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=True)
    marketplace_store_id   = Column(String(36), ForeignKey("marketplace_stores.id",   ondelete="SET NULL"), nullable=True)
    source        = Column(String(10),  nullable=False, default="csv", server_default="csv")  # csv | api
    total_rows    = Column(Integer, nullable=False, default=0)
    valid_rows    = Column(Integer, nullable=False, default=0)
    skipped_rows  = Column(Integer, nullable=False, default=0)
    imported_count = Column(Integer, nullable=False, default=0)
    warnings_json  = Column(Text, nullable=True)                  # JSON list[str]
    created_at    = Column(DateTime, default=datetime.utcnow)
    confirmed_at  = Column(DateTime, nullable=True)
