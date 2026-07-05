"""Returns Import Foundation (Phase R0) — imported_return_rows

Additive, non-destructive, no backfill, no touch to existing tables. One new ingestion table for
the third import_type (returns), field-shape mirroring imported_finance_rows (period rows keyed
by date + sku, with a nullable Product Spine link). SQLite-safe (CREATE TABLE + CREATE INDEX) and
Postgres-compatible. Ingestion only: no producer, no advisory wiring, not in the Decision Feed.

Revision ID: ret1a2b3c4d01
Revises: prc1a2b3c4d01
Create Date: 2026-07-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ret1a2b3c4d01"
down_revision: Union[str, None] = "prc1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "imported_return_rows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("returns_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("return_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("product_id", sa.String(length=36),
                  sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_imp_returns_user_mp", "imported_return_rows", ["user_id", "marketplace"])
    op.create_index("ix_imp_returns_product_id", "imported_return_rows", ["product_id"])
    op.create_index("ix_imported_return_rows_import_id", "imported_return_rows", ["import_id"])


def downgrade() -> None:
    op.drop_index("ix_imported_return_rows_import_id", table_name="imported_return_rows")
    op.drop_index("ix_imp_returns_product_id", table_name="imported_return_rows")
    op.drop_index("ix_imp_returns_user_mp", table_name="imported_return_rows")
    op.drop_table("imported_return_rows")
