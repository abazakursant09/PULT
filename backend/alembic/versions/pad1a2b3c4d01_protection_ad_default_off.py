"""PULT-LAUNCH-2.4 — protection: include_ad_spend default OFF

Advertising is deducted only when the seller explicitly opts in AND the ad spend is provably
attributed to the product+store. A silent default ON would understate cost, so the default flips
to false. The feature is still OFF and no real seller protection settings exist yet, so every
existing row is backfilled to false honestly.

Downgrade restores the previous server_default (true). It CANNOT honestly restore per-row values
that were true before this migration: this migration set them all to false and did not record the
prior value, so downgrade leaves the data as-is (false) and only the default reverts — documented
here rather than fabricating history.

Revision ID: pad1a2b3c4d01
Revises: plp1a2b3c4d01
Create Date: 2026-07-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pad1a2b3c4d01"
down_revision: Union[str, None] = "plp1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "protection_policies"


def upgrade() -> None:
    # backfill existing rows to false (feature OFF, no real settings yet), then flip the default
    op.execute(sa.text(f"UPDATE {_T} SET include_ad_spend = 0"))
    with op.batch_alter_table(_T, schema=None) as b:
        b.alter_column("include_ad_spend", server_default="0")


def downgrade() -> None:
    # only the default reverts; per-row values are NOT restored (prior values were not recorded)
    with op.batch_alter_table(_T, schema=None) as b:
        b.alter_column("include_ad_spend", server_default="1")
