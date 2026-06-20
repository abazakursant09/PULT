"""Memory OS Phase 1 — chain tracking + decision_memory

Additive, non-destructive, no backfill:
- decisions.decision_chain_id (nullable, indexed) + decisions.step_in_chain
  (NOT NULL default 0). Old decisions remain chain_id=NULL, step_in_chain=0.
- decision_memory: append-only table (no updated_at, no mutable lifecycle).

SQLite-safe (ADD COLUMN + CREATE TABLE + CREATE INDEX; no constraint ALTER) and
Postgres-compatible.

Revision ID: dm1a2b3c4d01
Revises: ik1a2b3c4d01
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "dm1a2b3c4d01"
down_revision: Union[str, None] = "ik1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) chain tracking on decisions (additive)
    op.add_column("decisions", sa.Column("decision_chain_id", sa.String(length=36), nullable=True))
    op.add_column(
        "decisions",
        sa.Column("step_in_chain", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_decision_chain", "decisions", ["decision_chain_id", "step_in_chain"])

    # 2) append-only memory table
    op.create_table(
        "decision_memory",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("decision_id", sa.String(length=36), nullable=False),
        sa.Column("decision_chain_id", sa.String(length=36), nullable=True),
        sa.Column("step_in_chain", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("action_type", sa.String(length=64), nullable=True),
        sa.Column("context_group", sa.String(length=200), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("effect_value", sa.Float(), nullable=True),
        sa.Column("estimate_value", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_decision_memory_chain", "decision_memory", ["decision_chain_id"])
    op.create_index("ix_decision_memory_product", "decision_memory", ["product_id"])
    op.create_index("ix_decision_memory_decision", "decision_memory", ["decision_id"])
    op.create_index("ix_decision_memory_context", "decision_memory", ["context_group"])


def downgrade() -> None:
    op.drop_index("ix_decision_memory_context", table_name="decision_memory")
    op.drop_index("ix_decision_memory_decision", table_name="decision_memory")
    op.drop_index("ix_decision_memory_product", table_name="decision_memory")
    op.drop_index("ix_decision_memory_chain", table_name="decision_memory")
    op.drop_table("decision_memory")
    op.drop_index("ix_decision_chain", table_name="decisions")
    op.drop_column("decisions", "step_in_chain")
    op.drop_column("decisions", "decision_chain_id")
