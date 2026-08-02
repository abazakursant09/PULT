"""SECURITY-2D-1C-C1 — additive dispatch-attempt columns on execution_logs.

Adds TWO columns, written ONLY by the executor's fencing CAS (pending→in_flight):
  attempt_count   Integer NOT NULL default 0 — provable dispatch attempts (each = a won ownership CAS).
  last_attempt_at DateTime(tz) NULL          — when the last attempt was taken.

CHECK: attempt_count >= 0. Existing rows get 0 / NULL. The 1B-B partial UNIQUE (uq_execlog_op_claim) and
the 1C-A / 1C-B columns / CHECKs / reconciliation enum are all preserved. SQLite uses batch (table
recreate) to add the CHECK portably; PostgreSQL adds it directly. Downgrade removes only the two columns
and their one CHECK. Additive-only — no data transform, no other table touched.

Revision ID: fcs1a2b3c4d01
Revises: rbp1a2b3c4d01
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fcs1a2b3c4d01"
down_revision: Union[str, None] = "rbp1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK = "ck_execlog_attempt_count_nonneg"
_CK_SQL = "attempt_count >= 0"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.add_column("execution_logs",
                      sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        op.add_column("execution_logs",
                      sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
        op.create_check_constraint(_CK, "execution_logs", _CK_SQL)
    else:
        with op.batch_alter_table("execution_logs") as batch_op:
            batch_op.add_column(
                sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(
                sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.create_check_constraint(_CK, _CK_SQL)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_CK, "execution_logs", type_="check")
        op.drop_column("execution_logs", "last_attempt_at")
        op.drop_column("execution_logs", "attempt_count")
    else:
        with op.batch_alter_table("execution_logs") as batch_op:
            batch_op.drop_constraint(_CK, type_="check")
            batch_op.drop_column("last_attempt_at")
            batch_op.drop_column("attempt_count")
