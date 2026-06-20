"""Decision multi-action uniqueness — (user_id, insight_key, action_key)

A2.6: a single Insight may promote multiple alternative actions (e.g.
margin_crisis → set_price AND reduce_discount). Replace the unique index
(user_id, insight_key) with (user_id, insight_key, action_key). Non-destructive:
drop + create index only (SQLite-safe, no table rewrite, no data change). NULL
action_key rows (seed/legacy/manual) stay distinct under the new index.

Revision ID: ma1a2b3c4d01
Revises: dm1a2b3c4d01
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op


revision: str = "ma1a2b3c4d01"
down_revision: Union[str, None] = "dm1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_decision_user_insight", table_name="decisions")
    op.create_index(
        "uq_decision_user_insight_action",
        "decisions",
        ["user_id", "insight_key", "action_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_decision_user_insight_action", table_name="decisions")
    op.create_index(
        "uq_decision_user_insight",
        "decisions",
        ["user_id", "insight_key"],
        unique=True,
    )
