"""SECURITY-2D-1C-B — additive reconciliation-scheduling columns on execution_logs.

Adds THREE columns (written ONLY by the read-only recovery sweep; the executor is untouched; no runtime
path in 1C-B reads them from the hot path):
  reconciliation_attempts Integer NOT NULL default 0 — bound the number of read-rechecks.
  last_reconciled_at      DateTime(tz) NULL           — when the last reconciliation read happened.
  next_reconcile_at       DateTime(tz) NULL           — when to recheck (eventual-consistency backoff).

CHECK: reconciliation_attempts >= 0. Existing rows get 0/NULL/NULL. The 1B-B partial UNIQUE
(uq_execlog_op_claim) and the 1C-A columns/CHECKs/reconciliation_status enum are preserved. SQLite uses
batch (table recreate) to add the CHECK; PostgreSQL adds it directly. Downgrade removes only the three
columns and their one CHECK.

Revision ID: rcb1a2b3c4d01
Revises: rcv1a2b3c4d01
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "rcb1a2b3c4d01"
down_revision: Union[str, None] = "rcv1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK = "ck_execlog_reconciliation_attempts_nonneg"
_CK_SQL = "reconciliation_attempts >= 0"

# SECURITY-2D-1C-B renames the reconciliation_status value "not_observed" → "target_not_observed" (a
# current-state mismatch is NOT proof the operation was never applied; the old name could be misread as
# a retry authorisation). No rows use the column yet (the sweep is OFF), so this is a pure vocabulary swap.
_CK_RECON = "ck_execlog_reconciliation_status"
_RECON_OLD = ("('pending_recon','reconciling','intent_observed','not_observed','still_unknown',"
              "'manual_attention','resolved')")
_RECON_NEW = ("('pending_recon','reconciling','intent_observed','target_not_observed','still_unknown',"
              "'manual_attention','resolved')")


def _recon_sql(vals: str) -> str:
    return f"reconciliation_status IS NULL OR reconciliation_status IN {vals}"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.add_column("execution_logs",
                      sa.Column("reconciliation_attempts", sa.Integer(), nullable=False, server_default="0"))
        op.add_column("execution_logs",
                      sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("execution_logs",
                      sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True))
        op.create_check_constraint(_CK, "execution_logs", _CK_SQL)
        op.drop_constraint(_CK_RECON, "execution_logs", type_="check")           # swap enum vocabulary
        op.create_check_constraint(_CK_RECON, "execution_logs", _recon_sql(_RECON_NEW))
    else:
        with op.batch_alter_table("execution_logs") as batch_op:
            batch_op.add_column(
                sa.Column("reconciliation_attempts", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(
                sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(
                sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.create_check_constraint(_CK, _CK_SQL)
            batch_op.drop_constraint(_CK_RECON, type_="check")
            batch_op.create_check_constraint(_CK_RECON, _recon_sql(_RECON_NEW))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_CK_RECON, "execution_logs", type_="check")           # restore old vocabulary
        op.create_check_constraint(_CK_RECON, "execution_logs", _recon_sql(_RECON_OLD))
        op.drop_constraint(_CK, "execution_logs", type_="check")
        op.drop_column("execution_logs", "next_reconcile_at")
        op.drop_column("execution_logs", "last_reconciled_at")
        op.drop_column("execution_logs", "reconciliation_attempts")
    else:
        with op.batch_alter_table("execution_logs") as batch_op:
            batch_op.drop_constraint(_CK_RECON, type_="check")
            batch_op.create_check_constraint(_CK_RECON, _recon_sql(_RECON_OLD))
            batch_op.drop_constraint(_CK, type_="check")
            batch_op.drop_column("next_reconcile_at")
            batch_op.drop_column("last_reconciled_at")
            batch_op.drop_column("reconciliation_attempts")
