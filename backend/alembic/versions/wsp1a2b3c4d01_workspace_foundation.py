"""Workspace ownership foundation (F1.0) — Marketplace Data & Identity Platform

Additive, non-destructive. One new table: `workspaces` — the ownership boundary that
marketplace accounts and (later) evidence will be keyed on, so that ownership never
depends on `users.id` directly.

`workspaces.id` is an INDEPENDENT uuid4, deliberately not equal to `owner_user_id`:
both are String(36) uuid4 strings, so a shared value would make the two silently
interchangeable — and workspace_id will enter the evidence fact key.

Backfill: exactly one workspace per existing user. Idempotent by result (WHERE NOT
EXISTS + UNIQUE(owner_user_id)). uuid4 is generated in Python, not by the database:
SQLite has no gen_random_uuid() and this migration must run on SQLite and PostgreSQL
alike. No row of `users` is modified. No existing table gains a column.

FK carries no ON DELETE clause: account deletion is a SOFT delete (routers/referrals.py
sets users.deleted_at; the row is never removed) and re-registration restores the same
row, so the referenced user never disappears.

Revision ID: wsp1a2b3c4d01
Revises: mcs1a2b3c4d01
Create Date: 2026-07-11
"""
import uuid
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "wsp1a2b3c4d01"
down_revision: Union[str, None] = "mcs1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("uq_workspace_owner", "workspaces", ["owner_user_id"], unique=True)

    # Backfill: one workspace per existing user, for users that do not have one yet.
    bind = op.get_bind()
    missing = bind.execute(sa.text(
        "SELECT u.id FROM users u "
        "WHERE NOT EXISTS (SELECT 1 FROM workspaces w WHERE w.owner_user_id = u.id)"
    )).fetchall()

    if missing:
        now = datetime.utcnow()
        bind.execute(
            sa.text(
                "INSERT INTO workspaces (id, owner_user_id, created_at) "
                "VALUES (:id, :owner_user_id, :created_at)"
            ),
            [
                {"id": str(uuid.uuid4()), "owner_user_id": row[0], "created_at": now}
                for row in missing
            ],
        )


def downgrade() -> None:
    op.drop_index("uq_workspace_owner", table_name="workspaces")
    op.drop_table("workspaces")
