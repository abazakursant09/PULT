"""SECURITY-2D-1C-C3A — operator recovery read-only foundation.

Two additive, UNWIRED changes (no runtime path writes either in C3A):
  1. execution_logs gains three nullable operator manual-resolution columns + a CHECK:
       manual_resolution String(24)  — NULL or one of the 4 allowed operator conclusions
       resolved_by       String(64)  — server-side actor id (set by a future C3B writer)
       resolved_at       DateTime(tz)
     CHECK ck_execlog_manual_resolution: manual_resolution IS NULL OR IN (…4 values…).
  2. A new append-only table execution_recovery_audit (created empty; no writer in C3A).

The 1B-B partial UNIQUE (uq_execlog_op_claim) and every earlier recovery column/CHECK are preserved:
SQLite recreates execution_logs via batch (reflecting and re-applying the partial index), PostgreSQL adds
the columns + CHECK directly. Downgrade removes ONLY the audit table and the three new columns (+ CHECK).
Additive-only — no data transform, no other existing table touched.

Revision ID: rop1a2b3c4d01
Revises: rwn1a2b3c4d01
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "rop1a2b3c4d01"
down_revision: Union[str, None] = "rwn1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept identical to models.execution_log._MANUAL_RESOLUTIONS and models.execution_recovery_audit enums
# (a guard test asserts the migrated DB CHECK matches the model set).
_MANUAL_RESOLUTIONS = ("confirmed_applied", "confirmed_not_applied", "retry_authorized", "manual_closed")
_MR_CK = "ck_execlog_manual_resolution"
_MR_CK_SQL = ("manual_resolution IS NULL OR manual_resolution IN ("
              + ", ".join(f"'{v}'" for v in _MANUAL_RESOLUTIONS) + ")")

_AUDIT_ACTIONS = ("confirm_applied", "confirm_not_applied", "close", "authorize_retry")
_REASON_CODES = (
    "operator_confirmed_applied", "operator_confirmed_not_applied", "operator_closed_no_action",
    "operator_authorized_retry", "stale_pending_review", "ambiguous_needs_review",
)
_ACTION_CK_SQL = "action IN (" + ", ".join(f"'{a}'" for a in _AUDIT_ACTIONS) + ")"
_REASON_CK_SQL = "reason_code IN (" + ", ".join(f"'{r}'" for r in _REASON_CODES) + ")"


def _create_audit_table() -> None:
    op.create_table(
        "execution_recovery_audit",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("execution_log_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("previous_status", sa.String(length=20), nullable=True),
        sa.Column("previous_resolution", sa.String(length=24), nullable=True),
        sa.Column("new_resolution", sa.String(length=24), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=40), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_log_id"], ["execution_logs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("execution_log_id", "correlation_id", "action",
                            name="uq_recovery_audit_op_corr_action"),
        sa.CheckConstraint(_ACTION_CK_SQL, name="ck_recovery_audit_action"),
        sa.CheckConstraint(_REASON_CK_SQL, name="ck_recovery_audit_reason_code"),
    )
    op.create_index("ix_recovery_audit_execlog", "execution_recovery_audit", ["execution_log_id"])


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.add_column("execution_logs", sa.Column("manual_resolution", sa.String(length=24), nullable=True))
        op.add_column("execution_logs", sa.Column("resolved_by", sa.String(length=64), nullable=True))
        op.add_column("execution_logs",
                      sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
        op.create_check_constraint(_MR_CK, "execution_logs", _MR_CK_SQL)
    else:
        with op.batch_alter_table("execution_logs") as batch_op:
            batch_op.add_column(sa.Column("manual_resolution", sa.String(length=24), nullable=True))
            batch_op.add_column(sa.Column("resolved_by", sa.String(length=64), nullable=True))
            batch_op.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.create_check_constraint(_MR_CK, _MR_CK_SQL)
    _create_audit_table()


def downgrade() -> None:
    op.drop_index("ix_recovery_audit_execlog", table_name="execution_recovery_audit")
    op.drop_table("execution_recovery_audit")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_MR_CK, "execution_logs", type_="check")
        op.drop_column("execution_logs", "resolved_at")
        op.drop_column("execution_logs", "resolved_by")
        op.drop_column("execution_logs", "manual_resolution")
    else:
        with op.batch_alter_table("execution_logs") as batch_op:
            batch_op.drop_constraint(_MR_CK, type_="check")
            batch_op.drop_column("resolved_at")
            batch_op.drop_column("resolved_by")
            batch_op.drop_column("manual_resolution")
