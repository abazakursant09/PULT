"""
Action Binding Audit (Action Catalog Expansion data foundation, A2) — append-only.

Immutable record of a binding decision: for a given signal type, which catalog
action was bound (if any) and why. Foundation for transparency + a future gate;
A2 only ships the schema — no logic writes it yet. Append-only: written once,
never rewritten — no `updated_at`. Marketplace-agnostic, soft refs. No score, no
forecast, no priority — this is a binding ledger, not analytics.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Index
from database import Base


class ActionBindingAudit(Base):
    __tablename__ = "action_binding_audit"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String(36), nullable=False)    # soft ref → users.id
    signal_type    = Column(String(64), nullable=False)    # canonical engine signal_key
    action_key     = Column(String(64), nullable=True)     # catalog action, or null when not bound

    # bound | no_catalog_action | payload_not_derivable | capability_missing
    binding_status = Column(String(30), nullable=False)
    reason         = Column(Text, nullable=True)
    marketplace    = Column(String(20), nullable=True)     # provenance / context only

    created_at     = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_action_binding_audit_user", "user_id"),
        Index("ix_action_binding_audit_signal", "signal_type"),
        Index("ix_action_binding_audit_status", "binding_status"),
    )
