"""SECURITY-2D-1C-A — additive recovery/fencing schema foundation on execution_logs.

Adds TWO columns, nothing else (no runtime reads them; executor untouched):
  claim_generation      Integer NOT NULL default 0 — fencing token; a future controlled re-own (1C-C)
                        bumps it and the executor's ownership CAS checks it, fencing out a revived worker.
  reconciliation_status String(20) NULL — read-only recovery classification written by the 1C-B service.

DB CHECKs: claim_generation >= 0; reconciliation_status is NULL or one of the 7 allowed values. Existing
rows get claim_generation=0 / reconciliation_status=NULL. The 1B-B partial UNIQUE (uq_execlog_op_claim) and
all other indexes are preserved. SQLite uses batch (table recreate) to add a CHECK; PostgreSQL adds it
directly. Downgrade removes only the two columns and their two CHECKs.

Revision ID: rcv1a2b3c4d01
Revises: uqc1a2b3c4d01
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "rcv1a2b3c4d01"
down_revision: Union[str, None] = "uqc1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RECON = ("('pending_recon','reconciling','intent_observed','not_observed',"
          "'still_unknown','manual_attention','resolved')")
_CK_GEN = "ck_execlog_claim_generation_nonneg"
_CK_RECON = "ck_execlog_reconciliation_status"
_GEN_SQL = "claim_generation >= 0"
_RECON_SQL = f"reconciliation_status IS NULL OR reconciliation_status IN {_RECON}"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.add_column("execution_logs",
                      sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"))
        op.add_column("execution_logs",
                      sa.Column("reconciliation_status", sa.String(length=20), nullable=True))
        op.create_check_constraint(_CK_GEN, "execution_logs", _GEN_SQL)
        op.create_check_constraint(_CK_RECON, "execution_logs", _RECON_SQL)
    else:
        # SQLite cannot ADD a CHECK via ALTER — batch recreates the table (preserving the 1B-B partial
        # UNIQUE + other indexes) with the two new columns and two CHECKs.
        with op.batch_alter_table("execution_logs") as batch_op:
            batch_op.add_column(
                sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(
                sa.Column("reconciliation_status", sa.String(length=20), nullable=True))
            batch_op.create_check_constraint(_CK_GEN, _GEN_SQL)
            batch_op.create_check_constraint(_CK_RECON, _RECON_SQL)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_CK_RECON, "execution_logs", type_="check")
        op.drop_constraint(_CK_GEN, "execution_logs", type_="check")
        op.drop_column("execution_logs", "reconciliation_status")
        op.drop_column("execution_logs", "claim_generation")
    else:
        with op.batch_alter_table("execution_logs") as batch_op:
            batch_op.drop_constraint(_CK_RECON, type_="check")
            batch_op.drop_constraint(_CK_GEN, type_="check")
            batch_op.drop_column("reconciliation_status")
            batch_op.drop_column("claim_generation")
