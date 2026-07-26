"""PULT-LAUNCH-1.4.5E — API sync state + external_row_id for WB ingestion

Adds:
  * api_sync_states — one cursor/schedule row per (connection, store, data_type), so API ingestion
    resumes from a persisted cursor and backs off per connection. Control data only: no token, no
    secret body, no raw marketplace error.
  * external_row_id on imported_product_rows and imported_card_content_rows — the stable WB nmID of
    an API row — with a PARTIAL unique index over the non-null value, so a repeated page or a
    repeated full sync updates a row in place instead of inserting a duplicate. CSV rows keep
    external_row_id NULL and are never constrained.

Revision ID: wba1a2b3c4d01
Revises: apf1a2b3c4d01
Create Date: 2026-07-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "wba1a2b3c4d01"
down_revision: Union[str, None] = "apf1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_sync_states",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("marketplace_connection_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_account_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_store_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("cursor", sa.String(length=500), nullable=True),
        sa.Column("covered_from", sa.String(length=10), nullable=True),
        sa.Column("covered_to", sa.String(length=10), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_safe_error_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("marketplace_connection_id", "marketplace_store_id", "data_type",
                            name="uq_api_sync_conn_store_type"),
    )
    op.create_index("ix_api_sync_next_run", "api_sync_states", ["next_run_at"])
    op.create_index("ix_api_sync_conn", "api_sync_states", ["marketplace_connection_id"])

    with op.batch_alter_table("imported_product_rows") as b:
        b.add_column(sa.Column("external_row_id", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_imp_product_api_row", "imported_product_rows",
        ["marketplace_account_id", "marketplace_store_id", "source", "external_row_id"],
        unique=True,
        sqlite_where=sa.text("external_row_id IS NOT NULL"),
        postgresql_where=sa.text("external_row_id IS NOT NULL"),
    )

    with op.batch_alter_table("imported_card_content_rows") as b:
        b.add_column(sa.Column("external_row_id", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_imp_card_api_row", "imported_card_content_rows",
        ["marketplace_account_id", "source", "external_row_id"],
        unique=True,
        sqlite_where=sa.text("external_row_id IS NOT NULL"),
        postgresql_where=sa.text("external_row_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_imp_card_api_row", table_name="imported_card_content_rows")
    with op.batch_alter_table("imported_card_content_rows") as b:
        b.drop_column("external_row_id")
    op.drop_index("uq_imp_product_api_row", table_name="imported_product_rows")
    with op.batch_alter_table("imported_product_rows") as b:
        b.drop_column("external_row_id")
    op.drop_index("ix_api_sync_conn", table_name="api_sync_states")
    op.drop_index("ix_api_sync_next_run", table_name="api_sync_states")
    op.drop_table("api_sync_states")
