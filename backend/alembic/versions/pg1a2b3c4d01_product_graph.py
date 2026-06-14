"""Product Graph (Doctrine §3/§7) — physical_products, product_listings, decisions

Additive: 3 new tables establishing the core model Товар / Листинг / Решение.
- physical_products: атом (COGS, документы, бренд, товарный знак) — общее по МП
- product_listings:  листинг на МП (+ матчинг §3.1) → physical_product
- decisions:         decision object (проблема→...→действие + PnL impact)

Bridges to legacy `products` (per-MP, listing-level) via
product_listings.legacy_product_id (SET NULL) — old table untouched.
Breaks zero existing readers.

Revision ID: pg1a2b3c4d01
Revises: me1a2b3c4d01
Create Date: 2026-06-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pg1a2b3c4d01"
down_revision: Union[str, None] = "me1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "physical_products",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=True),
        sa.Column("seller_sku", sa.String(length=255), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("cogs", sa.Float(), nullable=True),
        sa.Column("cogs_source", sa.String(length=20), nullable=True),
        sa.Column("trademark_status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("trademark_source", sa.String(length=20), nullable=True),
        sa.Column("chestny_znak_required", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_phys_prod_user", "physical_products", ["user_id"])
    op.create_index("ix_phys_prod_barcode", "physical_products", ["user_id", "barcode"])

    op.create_table(
        "product_listings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("physical_product_id", sa.String(length=36), sa.ForeignKey("physical_products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("legacy_product_id", sa.String(length=36), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("match_method", sa.String(length=20), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "marketplace", "external_id", name="uq_listing_user_mp_ext"),
    )
    op.create_index("ix_listing_phys", "product_listings", ["physical_product_id"])
    op.create_index("ix_listing_user_mp", "product_listings", ["user_id", "marketplace"])

    op.create_table(
        "decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("physical_product_id", sa.String(length=36), sa.ForeignKey("physical_products.id", ondelete="CASCADE"), nullable=True),
        sa.Column("listing_id", sa.String(length=36), sa.ForeignKey("product_listings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("problem", sa.String(length=500), nullable=False),
        sa.Column("cause", sa.Text(), nullable=True),
        sa.Column("effect", sa.Text(), nullable=True),
        sa.Column("action", sa.String(length=500), nullable=True),
        sa.Column("action_key", sa.String(length=64), nullable=True),
        sa.Column("pnl_impact", sa.Float(), nullable=True),
        sa.Column("pnl_level", sa.String(length=10), nullable=True),
        sa.Column("severity", sa.String(length=10), nullable=False, server_default="warn"),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="compute"),
        sa.Column("status", sa.String(length=15), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_decision_user_status", "decisions", ["user_id", "status"])
    op.create_index("ix_decision_phys", "decisions", ["physical_product_id"])
    op.create_index("ix_decision_listing", "decisions", ["listing_id"])


def downgrade() -> None:
    op.drop_index("ix_decision_listing", table_name="decisions")
    op.drop_index("ix_decision_phys", table_name="decisions")
    op.drop_index("ix_decision_user_status", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("ix_listing_user_mp", table_name="product_listings")
    op.drop_index("ix_listing_phys", table_name="product_listings")
    op.drop_table("product_listings")
    op.drop_index("ix_phys_prod_barcode", table_name="physical_products")
    op.drop_index("ix_phys_prod_user", table_name="physical_products")
    op.drop_table("physical_products")
