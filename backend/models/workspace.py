import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Index, ForeignKey
from database import Base


class Workspace(Base):
    """Ownership boundary (F1.0 — Marketplace Data & Identity Platform foundation).

    A Workspace owns marketplace accounts and, later, evidence. It exists so that
    ownership is never keyed on `users.id`: workspace_id will enter the evidence
    fact key, and a workspace must survive a change of membership without re-keying
    every evidence row.

    `id` is an INDEPENDENT uuid4 — deliberately NOT equal to `owner_user_id`. Both
    columns are String(36) uuid4 strings, so an id reused across the two entities
    would be silently interchangeable: passing a user id where a workspace id is
    expected would type-check, pass the ORM, pass the database, and write evidence
    under the wrong key. A distinct id makes that mistake fail loudly instead.

    Launch invariant: exactly one workspace per user, enforced at the database level
    by the unique index on owner_user_id. No members, no roles, no invitations —
    those are post-launch and do not require re-keying anything defined here.
    """
    __tablename__ = "workspaces"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Account deletion is a SOFT delete (routers/referrals.py: sets users.deleted_at,
    # the row is never removed) and re-registration restores the same row, so no
    # ON DELETE behaviour is needed here: the referenced user row does not disappear.
    owner_user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at    = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("uq_workspace_owner", "owner_user_id", unique=True),
    )
