"""SECURITY-2C-3C — drop the write-only login_attempts table (plaintext PII removal)

login_attempts stored a plaintext email + IP for every login / register / MFA attempt. It had ZERO
readers: no auth decision consulted it, no endpoint or report exposed it, and the durable brute-force
state lives entirely in auth_rate_limit_buckets as HMAC fingerprints (SECURITY-2C-2). The table only
accumulated PII, so the runtime writer was removed and this migration drops the table and every
historical row in it.

upgrade  — DROP TABLE login_attempts. This is IRREVERSIBLE for the data: the historical email/IP rows
           are deleted permanently and cannot be recovered.
downgrade — recreates the ORIGINAL empty structure (columns + indexes) exactly, so the schema chain
            round-trips, but it does NOT and CANNOT restore any deleted row. No backup table is made,
            no PII is copied, no fingerprint replacement is introduced. auth_rate_limit_buckets is not
            touched.

Revision ID: lad1a2b3c4d01
Revises: mts1a2b3c4d01
Create Date: 2026-08-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "lad1a2b3c4d01"
down_revision: Union[str, None] = "mts1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drops the table and its indexes on both PostgreSQL and SQLite; the rows are gone for good.
    op.drop_table("login_attempts")


def downgrade() -> None:
    # Rebuilds only the EMPTY original structure — deleted rows are NOT recoverable. Columns, types and
    # indexes match the historical baseline (47beea1df0c1) exactly.
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("login_attempts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_login_attempts_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_login_attempts_email"), ["email"], unique=False)
