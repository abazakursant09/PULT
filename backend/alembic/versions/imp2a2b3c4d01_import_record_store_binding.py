"""PULT-LAUNCH-1.4.2 — bind ImportRecord to a MarketplaceStore

Additive columns on import_records:
  * marketplace_account_id — FK marketplace_accounts (CASCADE: cabinet delete removes its
    import records, per the commercial-data policy).
  * marketplace_store_id   — FK marketplace_stores (SET NULL: archiving/removing one store
    keeps the record inside the cabinet).
  * source                 — 'csv' (api arrives with 1.5).

All nullable / defaulted: legacy rows keep working (read-only), but they carry no store and
are refused at confirm (must be re-uploaded). No status enum change — status stays a String;
the pending/processing/confirmed/failed/expired machine is application-level.

Revision ID: imp2a2b3c4d01
Revises: spl1a2b3c4d01
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "imp2a2b3c4d01"
down_revision: Union[str, None] = "spl1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("import_records") as b:
        b.add_column(sa.Column("marketplace_account_id", sa.String(length=36), nullable=True))
        b.add_column(sa.Column("marketplace_store_id", sa.String(length=36), nullable=True))
        b.add_column(sa.Column("source", sa.String(length=10), nullable=False, server_default="csv"))
        b.create_foreign_key("fk_import_record_account", "marketplace_accounts",
                             ["marketplace_account_id"], ["id"], ondelete="CASCADE")
        b.create_foreign_key("fk_import_record_store", "marketplace_stores",
                             ["marketplace_store_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("import_records") as b:
        b.drop_constraint("fk_import_record_store", type_="foreignkey")
        b.drop_constraint("fk_import_record_account", type_="foreignkey")
        b.drop_column("source")
        b.drop_column("marketplace_store_id")
        b.drop_column("marketplace_account_id")
