"""SECURITY-2D-1C-C3B — global UNIQUE(correlation_id) on execution_recovery_audit.

Additive idempotency strengthening: one operator Idempotency-Key (correlation_id) may occur AT MOST ONCE
across the whole audit table, so the same key reused on a different log/action is a detectable 409
mismatch instead of a silent second row. The pre-existing composite UNIQUE
(execution_log_id, correlation_id, action), the FK execution_logs.id ON DELETE RESTRICT, the action and
reason_code CHECKs, the ix_recovery_audit_execlog index and all column types/nullability are preserved.
SQLite recreates the table via batch; PostgreSQL adds the constraint directly. Downgrade drops ONLY the
new constraint. No data transform; the C3B writer arrives in the same PR but never runs here.

Revision ID: rob1a2b3c4d01
Revises: rop1a2b3c4d01
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op


revision: str = "rob1a2b3c4d01"
down_revision: Union[str, None] = "rop1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UQ = "uq_recovery_audit_correlation"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.create_unique_constraint(_UQ, "execution_recovery_audit", ["correlation_id"])
    else:
        # batch recreate reflects and re-applies the existing composite UNIQUE, FK, CHECKs and index.
        with op.batch_alter_table("execution_recovery_audit") as batch_op:
            batch_op.create_unique_constraint(_UQ, ["correlation_id"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_UQ, "execution_recovery_audit", type_="unique")
    else:
        with op.batch_alter_table("execution_recovery_audit") as batch_op:
            batch_op.drop_constraint(_UQ, type_="unique")
