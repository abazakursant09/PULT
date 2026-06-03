"""Product Spine Step 1 — add nullable product_id FK to import tables

Phase A only: additive, nullable, FK ondelete=SET NULL + index. No backfill,
no NOT NULL, no unique constraint. Breaks zero existing readers (nothing reads
product_id today).

Revision ID: b1f3c0de5a01
Revises: 47beea1df0c1
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1f3c0de5a01"
down_revision: Union[str, None] = "47beea1df0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("imported_product_rows", schema=None) as batch_op:
        batch_op.add_column(sa.Column("product_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_imp_product_product_id", "products", ["product_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_imp_product_product_id", ["product_id"], unique=False)

    with op.batch_alter_table("imported_finance_rows", schema=None) as batch_op:
        batch_op.add_column(sa.Column("product_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_imp_finance_product_id", "products", ["product_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_imp_finance_product_id", ["product_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("imported_finance_rows", schema=None) as batch_op:
        batch_op.drop_index("ix_imp_finance_product_id")
        batch_op.drop_constraint("fk_imp_finance_product_id", type_="foreignkey")
        batch_op.drop_column("product_id")

    with op.batch_alter_table("imported_product_rows", schema=None) as batch_op:
        batch_op.drop_index("ix_imp_product_product_id")
        batch_op.drop_constraint("fk_imp_product_product_id", type_="foreignkey")
        batch_op.drop_column("product_id")
