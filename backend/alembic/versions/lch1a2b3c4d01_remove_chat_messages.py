"""LEGAL-1A — remove the user-to-user "Биржа" chat: drop the chat_messages table.

Legal driver (149-FZ art. 10.1): the "Биржа" was genuine user-to-user electronic messaging —
the fact pattern that makes a service an организатор распространения информации (ОРИ). Owner
decision: remove the chat entirely before beta. This migration removes the ONLY place message
CONTENT was ever stored: the chat_messages table.

Scope is deliberately narrow — this migration drops ONLY chat_messages. The two vestigial
moderation columns on users (chat_violations, chat_blocked) hold no message content and are left
in place to keep this migration off the users table; they are inert after the router/model removal.

DATA: chat_messages has only ever existed in local dev DBs and ephemeral CI databases — no
staging/production deployment exists — so no real user messages are destroyed. No backup table is
created and no conversation is copied (copying user correspondence would defeat the legal purpose).

DOWNGRADE recreates ONLY the empty table structure. It does NOT and CANNOT restore any messages —
the messages are gone by design.

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


def downgrade() -> None:
    # Recreate the EMPTY structure only — this honestly restores no messages.
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
