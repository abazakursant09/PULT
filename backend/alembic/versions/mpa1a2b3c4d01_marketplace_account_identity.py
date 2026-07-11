"""Marketplace account identity foundation (F1.1)

Additive, non-destructive. One new table `marketplace_accounts` — the stable identity of
an external seller cabinet, distinct from the credentials that currently reach it — plus
two nullable identity links on `marketplace_connections`.

Ownership invariant: one VERIFIED external cabinet belongs to at most ONE workspace,
globally. `UNIQUE(marketplace, external_account_id)` carries it, deliberately WITHOUT
workspace_id in the key: a key containing it would permit the same cabinet to be claimed
by two workspaces at once, which is exactly what the invariant forbids.

Backfill: every existing connection whose user resolves to a workspace gets its OWN
`unverified_legacy` account. Legacy connections are never merged just because their
`marketplace` values match — their real external identities are unknown, and merging on a
guess would fabricate shared ownership. `external_account_id` stays NULL for all of them:
no external id is invented from user_id, connection_id, ozon_client_id, a token, its
ciphertext, or its hash, and no marketplace API is called (a migration must not do I/O).
NULL rows coexist freely because SQLite and PostgreSQL both treat NULLs as distinct in an
ordinary UNIQUE constraint — so this backfill cannot collide, whatever the data holds.

`marketplace_connections.user_id` carries no FK, so a connection may reference a user that
does not exist and therefore has no workspace. Those rows keep NULL identity links rather
than failing the migration or inventing a workspace for them.

uuid4 is generated in Python, not by the database: SQLite has no gen_random_uuid() and this
migration must run on SQLite (tests) and PostgreSQL (production) alike — the F1.0 pattern.

Revision ID: mpa1a2b3c4d01
Revises: wsp1a2b3c4d01
Create Date: 2026-07-11
"""
import uuid
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "mpa1a2b3c4d01"
down_revision: Union[str, None] = "wsp1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketplace_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id"),
                  nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("external_account_id", sa.String(length=128), nullable=True),
        sa.Column("identity_status", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("marketplace", "external_account_id", name="uq_mp_account_mp_ext"),
    )
    op.create_index("ix_mp_account_ws", "marketplace_accounts", ["workspace_id"], unique=False)

    with op.batch_alter_table("marketplace_connections", schema=None) as batch_op:
        batch_op.add_column(sa.Column("workspace_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("marketplace_account_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_mp_conn_workspace", "workspaces", ["workspace_id"], ["id"])
        batch_op.create_foreign_key(
            "fk_mp_conn_account", "marketplace_accounts", ["marketplace_account_id"], ["id"])
        batch_op.create_index("ix_mp_conn_account", ["marketplace_account_id"], unique=False)

    # ── Legacy backfill: one own account per existing connection ──────────────────
    bind = op.get_bind()
    legacy = bind.execute(sa.text(
        "SELECT c.id, c.marketplace, c.label, c.created_at, w.id AS workspace_id "
        "FROM marketplace_connections c "
        "JOIN workspaces w ON w.owner_user_id = c.user_id"
    )).fetchall()

    now = datetime.utcnow()
    for conn_id, marketplace, label, conn_created_at, workspace_id in legacy:
        account_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO marketplace_accounts "
                "(id, workspace_id, marketplace, external_account_id, identity_status, "
                " label, created_at, updated_at) "
                "VALUES (:id, :workspace_id, :marketplace, NULL, 'unverified_legacy', "
                " :label, :created_at, :updated_at)"
            ),
            {"id": account_id, "workspace_id": workspace_id, "marketplace": marketplace,
             "label": label, "created_at": conn_created_at or now, "updated_at": now},
        )
        bind.execute(
            sa.text(
                "UPDATE marketplace_connections "
                "SET workspace_id = :workspace_id, marketplace_account_id = :account_id "
                "WHERE id = :conn_id"
            ),
            {"workspace_id": workspace_id, "account_id": account_id, "conn_id": conn_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("marketplace_connections", schema=None) as batch_op:
        batch_op.drop_index("ix_mp_conn_account")
        batch_op.drop_constraint("fk_mp_conn_account", type_="foreignkey")
        batch_op.drop_constraint("fk_mp_conn_workspace", type_="foreignkey")
        batch_op.drop_column("marketplace_account_id")
        batch_op.drop_column("workspace_id")

    op.drop_index("ix_mp_account_ws", table_name="marketplace_accounts")
    op.drop_table("marketplace_accounts")
