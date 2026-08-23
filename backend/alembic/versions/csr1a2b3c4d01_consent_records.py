"""LEGAL-PRELAUNCH-F2 (blocker #6) — append-only server-side registration/recovery consent evidence.

Creates ONE new table `consent_records`: one row per consent action (registration / recovery), written
in the same transaction as the user. Columns: id (uuid str PK), user_id (FK users.id, indexed),
consent_at (server UTC, NOT NULL), consent_version (String(16), NOT NULL), context (String(32), NOT NULL,
CHECK in ('registration','recovery')), created_at (NOT NULL). No IP/UA/email/token/doc-text is stored.

Additive only: no existing table/column is touched, and NO backfill of existing users is performed
(legacy users legitimately have zero rows). Upgrade creates only this table + its index + CHECK;
downgrade drops only what this revision added. One Alembic head is preserved.

Revision ID: csr1a2b3c4d01
Revises: rob1a2b3c4d01
Create Date: 2026-08-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "csr1a2b3c4d01"
down_revision: Union[str, None] = "rob1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("consent_at", sa.DateTime(), nullable=False),
        sa.Column("consent_version", sa.String(length=16), nullable=False),
        sa.Column("context", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("context IN ('registration', 'recovery')", name="ck_consent_context"),
    )
    op.create_index("ix_consent_records_user", "consent_records", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_consent_records_user", table_name="consent_records")
    op.drop_table("consent_records")
