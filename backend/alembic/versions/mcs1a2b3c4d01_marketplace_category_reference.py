"""Marketplace category schema Reference tables (Phase C2b) — GLOBAL Reference Data

Additive, non-destructive, no backfill, no touch to existing tables. Two new GLOBAL Reference
tables (Reference Data Doctrine): marketplace category tree + per-category attribute schema. NO
user_id — these are marketplace-owned facts, not per-seller evidence. Versioned current-state
(captured_at + version, source provenance); immutable stored versions kept for replay. SQLite-safe
(CREATE TABLE + CREATE INDEX) and Postgres-compatible. Inert: no producer, no advisory wiring, not
in the Decision Feed, not called by the SEO engine (merges into CardSnapshot at build time, C2d).

Revision ID: mcs1a2b3c4d01
Revises: cci1a2b3c4d01
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "mcs1a2b3c4d01"
down_revision: Union[str, None] = "cci1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketplace_category_rows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mp_category_marketplace", "marketplace_category_rows", ["marketplace"])
    op.create_index("ix_mp_category_category_id", "marketplace_category_rows", ["category_id"])
    op.create_index("ix_mp_category_mp_cat", "marketplace_category_rows", ["marketplace", "category_id"])
    op.create_index("ix_mp_category_captured_at", "marketplace_category_rows", ["captured_at"])

    op.create_table(
        "marketplace_category_attribute_rows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=False),
        sa.Column("attribute_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_filterable", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_variant", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("max_length", sa.Integer(), nullable=True),
        sa.Column("allowed_values_json", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mp_cat_attr_marketplace", "marketplace_category_attribute_rows", ["marketplace"])
    op.create_index("ix_mp_cat_attr_category_id", "marketplace_category_attribute_rows", ["category_id"])
    op.create_index("ix_mp_cat_attr_mp_cat", "marketplace_category_attribute_rows", ["marketplace", "category_id"])
    op.create_index("ix_mp_cat_attr_captured_at", "marketplace_category_attribute_rows", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_mp_cat_attr_captured_at", table_name="marketplace_category_attribute_rows")
    op.drop_index("ix_mp_cat_attr_mp_cat", table_name="marketplace_category_attribute_rows")
    op.drop_index("ix_mp_cat_attr_category_id", table_name="marketplace_category_attribute_rows")
    op.drop_index("ix_mp_cat_attr_marketplace", table_name="marketplace_category_attribute_rows")
    op.drop_table("marketplace_category_attribute_rows")

    op.drop_index("ix_mp_category_captured_at", table_name="marketplace_category_rows")
    op.drop_index("ix_mp_category_mp_cat", table_name="marketplace_category_rows")
    op.drop_index("ix_mp_category_category_id", table_name="marketplace_category_rows")
    op.drop_index("ix_mp_category_marketplace", table_name="marketplace_category_rows")
    op.drop_table("marketplace_category_rows")
