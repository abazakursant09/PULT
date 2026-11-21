"""Dormant server-owned DRAFT queue, not an external erasure journal.

Keep this table/revision on code rollback; older code needs AUTO_MIGRATE=0 or a
retained migration-aware launcher (never a backwards stamp). Its IDs cannot
prove external enrollment, freshness, dispatch, erasure or restore permission.
"""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, UniqueConstraint

from database import Base


class ErasurePreparation(Base):
    __tablename__ = "erasure_preparations"
    __table_args__ = (
        # Stronger than per-epoch uniqueness: an older unresolved draft blocks
        # every later closure until separately reviewed supersession exists.
        UniqueConstraint("user_id", name="uq_erasure_preparations_user"),
        CheckConstraint("state = 'DRAFT'", name="ck_erasure_preparations_draft_only"),
        CheckConstraint("length(id) = 36", name="ck_erasure_preparations_id_length"),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    closure_policy_version = Column(String(32), nullable=False)
    closure_started_at = Column(DateTime(timezone=True), nullable=False)
    prepared_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(String(16), nullable=False, default="DRAFT", server_default="DRAFT")
