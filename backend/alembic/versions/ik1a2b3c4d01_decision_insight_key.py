"""Decision insight_key — promotion dedup anchor

Additive: one nullable column linking a Decision to the Insight it fixates
(Insight → Decision bridge, Slice 1). Uniqueness on (user_id, insight_key) is
enforced via a UNIQUE INDEX, not an ALTER-added constraint — keeps the change
SQLite-safe (CREATE UNIQUE INDEX works on SQLite; ADD CONSTRAINT does not) and
Postgres-compatible. NULL insight_key rows (legacy/seed Decisions) do not
collide: NULLs are distinct in a unique index on both SQLite and Postgres.

Revision ID: ik1a2b3c4d01
Revises: el1a2b3c4d01
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ik1a2b3c4d01"
down_revision: Union[str, None] = "el1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "decisions",
        sa.Column("insight_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_decision_user_insight",
        "decisions",
        ["user_id", "insight_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_decision_user_insight", table_name="decisions")
    op.drop_column("decisions", "insight_key")
