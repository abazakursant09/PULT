"""PULT-LAUNCH-1.4.5E2 — marketplace_operations (WB orders/sales/returns/finance)

One normalized table for marketplace EVENTS. Orders, sales, returns, cancellations and each
financial component share one shape but different operation_type — kept out of ImportedFinanceRow
(which is summed as realized money) so an order never becomes revenue and a finance breakdown keeps
its components. Money is Numeric (never float); no buyer PII, no raw payload.

New table only; no backfill.

Revision ID: wbo1a2b3c4d01
Revises: wba1a2b3c4d01
Create Date: 2026-07-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "wbo1a2b3c4d01"
down_revision: Union[str, None] = "wba1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "marketplace_operations"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("marketplace_account_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_store_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_stores.id", ondelete="SET NULL"), nullable=True),
        sa.Column("product_id", sa.String(length=36),
                  sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="api"),
        sa.Column("external_operation_id", sa.String(length=64), nullable=False),
        sa.Column("external_parent_id", sa.String(length=64), nullable=True),
        sa.Column("operation_type", sa.String(length=20), nullable=False),
        sa.Column("provider_operation_code", sa.String(length=120), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("marketplace_account_id", "source", "external_operation_id",
                            "operation_type", name="uq_mp_operation"),
        sa.CheckConstraint("source IN ('api','csv')", name="ck_mp_operation_source"),
        sa.CheckConstraint(
            "operation_type IN ('order','sale','return','cancellation','commission','logistics',"
            "'penalty','deduction','other')", name="ck_mp_operation_type"),
    )
    op.create_index("ix_mp_operation_account_time", _TABLE, ["marketplace_account_id", "occurred_at"])
    op.create_index("ix_mp_operation_store_time", _TABLE, ["marketplace_store_id", "occurred_at"])
    op.create_index("ix_mp_operation_product", _TABLE, ["product_id"])
    op.create_index("ix_mp_operation_external", _TABLE, ["external_operation_id"])


def downgrade() -> None:
    op.drop_index("ix_mp_operation_external", table_name=_TABLE)
    op.drop_index("ix_mp_operation_product", table_name=_TABLE)
    op.drop_index("ix_mp_operation_store_time", table_name=_TABLE)
    op.drop_index("ix_mp_operation_account_time", table_name=_TABLE)
    op.drop_table(_TABLE)
