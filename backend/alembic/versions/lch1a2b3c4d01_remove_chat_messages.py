"""LEGAL-1A — remove the user-to-user "Биржа" chat completely.

Legal driver (149-FZ art. 10.1): the "Биржа" was genuine user-to-user electronic messaging —
the fact pattern that makes a service an организатор распространения информации (ОРИ). Owner
decision: remove the chat entirely before beta.

This migration removes the whole on-disk footprint of that feature:
  1. the chat_messages table — the ONLY place message CONTENT was ever stored;
  2. users.chat_violations — the per-user chat-moderation counter (no other reader/writer);
  3. users.chat_blocked    — the per-user chat-ban flag (no other reader/writer).

Both user columns were part of the "Биржа" moderation only; after the router/model/frontend
removal nothing in billing / auth / security / abuse reads or writes them.

DATA: chat_messages and the two columns have only ever existed in local dev DBs and ephemeral CI
databases — no staging/production deployment exists — so no real user messages, counters or bans
are destroyed. No backup table is created and no correspondence is copied (copying user messages
would defeat the legal purpose).

DOWNGRADE recreates the EMPTY chat_messages table structure and re-adds the two user columns with
their original type / default / nullability (Integer NOT NULL DEFAULT 0, Boolean NOT NULL DEFAULT 0).
It does NOT and CANNOT restore any messages, violation counters or ban states — those values are
gone by design.

Revision ID: lch1a2b3c4d01
Revises: rcb1a2b3c4d01
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "lch1a2b3c4d01"
down_revision: Union[str, None] = "rcb1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # chat_messages has no inbound foreign keys and no indexes beyond its primary key,
    # so a plain DROP TABLE is safe on both SQLite and PostgreSQL.
    op.drop_table("chat_messages")
    # SQLite cannot DROP COLUMN in-place on older engines — batch_alter_table recreates the table
    # portably; PostgreSQL runs a native ALTER TABLE ... DROP COLUMN.
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("chat_violations")
        batch_op.drop_column("chat_blocked")


def downgrade() -> None:
    # Re-add the two moderation columns with their exact original definition (baseline 47beea1df0c1:
    # Integer NOT NULL server_default '0'; Boolean NOT NULL server_default '0'). No index, no CHECK.
    # Existing rows get the default 0 — the pre-drop counters/ban states are NOT restored.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("chat_violations", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column("chat_blocked", sa.Boolean(), nullable=False, server_default="0"))
    # Recreate the EMPTY chat table structure only — this honestly restores no messages.
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
