"""Card-Content Import Foundation (Phase C0) — imported_card_content_rows

Additive, non-destructive, no backfill, no touch to existing tables. One new ingestion table for
the fourth import_type (card_content) — UPLOAD Evidence for SEO. Holds the card content fields
(title/description/brand/category/characteristics/media) the SEO CardSnapshot needs but internal
data lacks. Field shape mirrors imported_return_rows / imported_product_rows (dated rows with a
nullable Product Spine link). SQLite-safe (CREATE TABLE + CREATE INDEX) and Postgres-compatible.
Ingestion only: no producer, no advisory wiring, not in the Decision Feed.

Revision ID: cci1a2b3c4d01
Revises: rsg1a2b3c4d01
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cci1a2b3c4d01"
down_revision: Union[str, None] = "rsg1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "imported_card_content_rows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("characteristics_json", sa.Text(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=True),
        sa.Column("image_urls_json", sa.Text(), nullable=True),
        sa.Column("product_id", sa.String(length=36),
                  sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_imp_card_user_mp", "imported_card_content_rows", ["user_id", "marketplace"])
    op.create_index("ix_imp_card_product_id", "imported_card_content_rows", ["product_id"])
    op.create_index("ix_imported_card_content_rows_import_id", "imported_card_content_rows", ["import_id"])


def downgrade() -> None:
    op.drop_index("ix_imported_card_content_rows_import_id", table_name="imported_card_content_rows")
    op.drop_index("ix_imp_card_product_id", table_name="imported_card_content_rows")
    op.drop_index("ix_imp_card_user_mp", table_name="imported_card_content_rows")
    op.drop_table("imported_card_content_rows")
