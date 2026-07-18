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
    # AR-CONTROL: Auto Reviews are managed PER marketplace connection. `connection_id` binds this
    # rule to exactly one MarketplaceConnection (logical relation to marketplace_connections.id;
    # integrity is enforced in review_automation_gate, which always resolves the connection scoped to
    # the same user_id — a hard DB FK is avoided so the migration stays sqlite/Postgres-portable).
    # NULLABLE: legacy rules predate this and carry NULL — they can never auto-publish (the gate
    # blocks a rule with no connection / no consent).
    connection_id      = Column(String(36), nullable=True)
    # Explicit seller consent for automated publishing. default OFF = all three NULL. A rule may only
    # be enabled with mode=auto when consent_at is set, consent_version is supported, and consent has
    # not been revoked. Never auto-populated for existing users.
    consent_at         = Column(DateTime, nullable=True)
    consent_version    = Column(String(16), nullable=True)
    consent_revoked_at = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_autorule_user", "user_id"),
        Index("ix_autorule_user_action", "user_id", "action_type"),
        # One Auto Reviews rule per (seller, connection, action). NULL connection_id (legacy) is
        # exempt because NULLs are distinct in both SQLite and Postgres unique indexes.
        Index("ix_autorule_user_conn_action", "user_id", "connection_id", "action_type", unique=True),
    )
