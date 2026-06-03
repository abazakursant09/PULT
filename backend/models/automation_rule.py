import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, JSON, Boolean, Index
from database import Base


class AutomationRule(Base):
    """
    L4 automation rule. When enabled (and the global automation switch is on),
    the scheduler may invoke the SAME executor path a user would for L3, in
    `automated_l4` mode, subject to the rule's guard.
    """

    __tablename__ = "automation_rules"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(String(36), nullable=False)
    contour     = Column(String(30), nullable=False)          # reputation|money|growth|advertising
    action_type = Column(String(60), nullable=False)
    trigger     = Column(JSON, nullable=False, default=dict)  # {metric, op, value, window}
    guard       = Column(JSON, nullable=False, default=dict)  # {min_margin, max_step, daily_cap, negative_never}
    mode        = Column(String(20), nullable=False, default="confirm")  # confirm (L3) | auto (L4)
    enabled     = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_autorule_user", "user_id"),
        Index("ix_autorule_user_action", "user_id", "action_type"),
    )
