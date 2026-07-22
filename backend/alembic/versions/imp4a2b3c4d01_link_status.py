"""PULT-LAUNCH-1.4.4 — link_status on the four Imported* tables

Adds link_status = linked | unassigned | conflict to ImportedProductRow / ImportedFinanceRow /
ImportedReturnRow / ImportedCardContentRow, so an ambiguous row can be saved as a conflict instead
of aborting the whole confirm. Backfill: product_id IS NOT NULL -> linked, else -> unassigned.
Legacy rows are NEVER marked conflict (conflicts were not tracked historically).

No import_record_id column is needed: the existing indexed `import_id` (= ImportRecord.id) already
selects a specific import's rows.

Revision ID: imp4a2b3c4d01
Revises: imp2a2b3c4d01
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "imp4a2b3c4d01"
down_revision: Union[str, None] = "imp2a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "imported_product_rows",
    "imported_finance_rows",
    "imported_return_rows",
    "imported_card_content_rows",
)


def upgrade() -> None:
    bind = op.get_bind()
    for tbl in _TABLES:
        with op.batch_alter_table(tbl) as b:
            b.add_column(sa.Column("link_status", sa.String(length=10),
                                   nullable=False, server_default="unassigned"))
            b.create_check_constraint(
                f"ck_{tbl}_link_status",
                "link_status IN ('linked','unassigned','conflict')")
        # Backfill from the existing product link. Conflicts are never assigned retroactively.
        bind.execute(text(
            f"UPDATE {tbl} SET link_status = CASE WHEN product_id IS NOT NULL "
            f"THEN 'linked' ELSE 'unassigned' END"))


def downgrade() -> None:
    for tbl in _TABLES:
        with op.batch_alter_table(tbl) as b:
            b.drop_constraint(f"ck_{tbl}_link_status", type_="check")
            b.drop_column("link_status")
