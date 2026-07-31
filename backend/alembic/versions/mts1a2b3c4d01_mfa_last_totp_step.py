"""SECURITY-2C-3A — mfa_secrets.last_totp_step (TOTP replay guard)

Adds ONE nullable column: mfa_secrets.last_totp_step (BigInteger) = the newest TOTP step
(unix_time // 30) already spent on the account. A verify only accepts a code whose matched step is
strictly greater than this, so a single code can authenticate at most once (no replay, no two
concurrent verifies of the same code both succeeding). The column stores the step counter, never the
code or the secret.

Existing rows get NULL (no step spent yet) — the first verify after this migration is accepted and
records its step. A CHECK keeps the value non-negative. Purely additive; no other table is touched,
no reset-password / LoginAttempt / throttle change.

Revision ID: mts1a2b3c4d01
Revises: atl1a2b3c4d01
Create Date: 2026-08-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "mts1a2b3c4d01"
down_revision: Union[str, None] = "atl1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK = "ck_mfa_last_totp_step_nonneg"


def upgrade() -> None:
    # batch_alter_table so the same migration runs on PostgreSQL (plain ALTER) and SQLite (table
    # rebuild) — SQLite cannot ADD a CHECK constraint via bare ALTER.
    with op.batch_alter_table("mfa_secrets") as b:
        b.add_column(sa.Column("last_totp_step", sa.BigInteger(), nullable=True))
        b.create_check_constraint(_CK, "last_totp_step IS NULL OR last_totp_step >= 0")


def downgrade() -> None:
    with op.batch_alter_table("mfa_secrets") as b:
        b.drop_constraint(_CK, type_="check")
        b.drop_column("last_totp_step")
