import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, DateTime, Index
from database import Base


class OperatorDecision(Base):
    __tablename__ = "operator_decisions"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id             = Column(String(36), nullable=False)
    insight_type        = Column(String(100), nullable=False)   # high_ad_spend | seo_opportunity | ...
    marketplace         = Column(String(50),  nullable=True)
    product_name        = Column(String(200), nullable=True)
    action_taken        = Column(String(30),  nullable=False)   # accepted | ignored | dismissed_again
    accepted            = Column(Boolean, nullable=False, default=False)
    ignored             = Column(Boolean, nullable=False, default=False)
    resolved_after_days = Column(Integer, nullable=True)        # None = still open / monitoring
    created_at          = Column(DateTime, default=datetime.utcnow)
    # Sprint 26: outcome feedback — populated by validation job after sufficient observation period
    effect_observed        = Column(String(30),  nullable=True)   # stabilized | temporary | failed | unknown
    effect_duration_days   = Column(Integer,     nullable=True)   # how long the effect lasted
    recurrence_after_days  = Column(Integer,     nullable=True)   # days until signal returned
    validated_at           = Column(DateTime,    nullable=True)   # when feedback was recorded

    __table_args__ = (
        Index("ix_op_decision_user_type", "user_id", "insight_type"),
        Index("ix_op_decision_user_created", "user_id", "created_at"),
    )
