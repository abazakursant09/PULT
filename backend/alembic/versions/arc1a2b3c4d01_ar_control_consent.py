"""AR-CONTROL — per-connection Auto Reviews consent (additive)

Adds four nullable columns to automation_rules so Auto Reviews can be managed PER marketplace
connection under explicit seller consent, and one unique index guaranteeing at most one Auto Reviews
rule per (user_id, connection_id, action_type). Purely additive to the schema.

Safety: existing publish_review_response rules predate the consent model and carry no consent, so
this migration DISABLES any that were enabled — nothing may auto-publish again until the seller
grants fresh consent. No rule is ever auto-enabled. No other table is touched. NULL connection_id
rows are exempt from the unique index (NULLs are distinct in both SQLite and Postgres).

Revision ID: arc1a2b3c4d01
Revises: arv1a2b3c4d01
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "arc1a2b3c4d01"
down_revision: Union[str, None] = "arv1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Minimal table reflection for the data-only safety update (DB-agnostic booleans).
_automation_rules = sa.table(
    "automation_rules",
    sa.column("action_type", sa.String),
    sa.column("enabled", sa.Boolean),
)


def upgrade() -> None:
    op.add_column("automation_rules", sa.Column("connection_id", sa.String(length=36), nullable=True))
    op.add_column("automation_rules", sa.Column("consent_at", sa.DateTime(), nullable=True))
    op.add_column("automation_rules", sa.Column("consent_version", sa.String(length=16), nullable=True))
    op.add_column("automation_rules", sa.Column("consent_revoked_at", sa.DateTime(), nullable=True))

    op.create_index(
        "ix_autorule_user_conn_action",
        "automation_rules",
        ["user_id", "connection_id", "action_type"],
        unique=True,
    )

    # Safety: disable any pre-existing review-automation rule (no consent existed before). Nothing is
    # ever auto-enabled; this only turns things OFF.
    op.execute(
        _automation_rules.update()
        .where(_automation_rules.c.action_type == "publish_review_response")
        .where(_automation_rules.c.enabled.is_(True))
        .values(enabled=False)
    )


def downgrade() -> None:
    op.drop_index("ix_autorule_user_conn_action", table_name="automation_rules")
    op.drop_column("automation_rules", "consent_revoked_at")
    op.drop_column("automation_rules", "consent_version")
    op.drop_column("automation_rules", "consent_at")
    op.drop_column("automation_rules", "connection_id")
