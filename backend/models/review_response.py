import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Text, Index
from sqlalchemy.orm import relationship
from database import Base


class ReviewResponse(Base):
    __tablename__ = "review_responses"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    review_text = Column(Text, nullable=True)
    author = Column(String(255), nullable=True)
    rating = Column(Integer, nullable=True)
    response_text = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    # ── Marketplace Execution Layer (ME-2): real publish, not local imitation ──
    external_review_id = Column(String(120), nullable=True)   # WB/Ozon feedback id
    marketplace = Column(String(20), nullable=True)           # wildberries | ozon
    published_at = Column(DateTime, nullable=True)            # set only on real API success
    execution_log_id = Column(String(36), nullable=True)      # link to ExecutionLog of the publish
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Auto Reviews lifecycle foundation (AR0). Columns only — populated in later phases;
    #    /sync and /publish are unchanged here. All nullable / defaulted, so existing rows
    #    stay valid without any data transformation. ────────────────────────────────────
    review_created_at    = Column(DateTime, nullable=True)          # real review time on the marketplace
    safety_category      = Column(String(20), nullable=True)        # SAFE | ATTENTION | RISK
    manual_required_reason = Column(String(255), nullable=True)     # why the auto path is blocked
    failure_reason       = Column(String(255), nullable=True)       # last publish failure
    publication_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    retry_next_at        = Column(DateTime, nullable=True)          # future retry scheduling

    product = relationship("Product", back_populates="reviews")

    __table_args__ = (
        Index("ix_review_responses_product_id", "product_id"),
        Index("ix_review_responses_status", "status"),
        # Duplicate-ingestion guard: one real marketplace review (identified by its external id
        # on a given marketplace) maps to at most one row. Partial — rows with a NULL
        # external_review_id (legacy / not-yet-synced) are exempt and never collide, so this is
        # purely additive and safe for existing data. Enforced under concurrency at the DB level.
        Index(
            "uq_review_responses_external",
            "product_id", "external_review_id", "marketplace",
            unique=True,
            sqlite_where=external_review_id.isnot(None),
            postgresql_where=external_review_id.isnot(None),
        ),
    )
