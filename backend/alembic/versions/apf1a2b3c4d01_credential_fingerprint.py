"""PULT-LAUNCH-1.4.5D — credential_fingerprint on marketplace_connections

Wildberries exposes no stable seller id, so the ONLY way to notice the same WB token entered
against a second cabinet is a keyed fingerprint of the token. This adds a nullable column plus a
partial unique index over the non-null value: a duplicate WB token is refused at the DB, while
NULLs (every non-WB row and every tokenless row) stay distinct in both SQLite and PostgreSQL.

The value is HMAC(token, vault-secret) — never the token, never reversible, never returned or
logged. It answers one yes/no question and never drives a merge.

Revision ID: apf1a2b3c4d01
Revises: imp4a2b3c4d01
Create Date: 2026-07-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "apf1a2b3c4d01"
down_revision: Union[str, None] = "imp4a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_mp_conn_fingerprint"
_TABLE = "marketplace_connections"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as b:
        b.add_column(sa.Column("credential_fingerprint", sa.String(length=64), nullable=True))
    # Partial unique: only non-null fingerprints are constrained. Both engines treat NULLs as
    # distinct, so the WHERE keeps tokenless / non-WB rows out of the constraint entirely.
    op.create_index(
        _INDEX, _TABLE, ["credential_fingerprint"], unique=True,
        sqlite_where=sa.text("credential_fingerprint IS NOT NULL"),
        postgresql_where=sa.text("credential_fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as b:
        b.drop_column("credential_fingerprint")
