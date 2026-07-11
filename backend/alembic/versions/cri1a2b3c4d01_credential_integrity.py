"""Credential integrity and honest verification state (F1.2a)

Two independent things, both additive and both marketplace-agnostic. No network call, no
decryption, no secret is read or transformed anywhere in this migration.

1. `marketplace_connections` gains a SECOND state axis. `status` is the execution gate
   (`connected` is what the executor, the measurement bridge and the WB review sync
   require) and is left EXACTLY as it is — this migration does not touch a single status
   value. The new `verification_status` answers a different question: did a marketplace
   ever confirm these credentials? Every existing row backfills to `unverified`, because
   that is simply true: `POST /connections` encrypts whatever string it is handed and
   calls no marketplace, so no connection in this database has ever been verified. The
   backfill claims nothing about execution availability; it records the absence of a
   verification history.

2. `api_credentials` gets the integrity its readers already assume. `(connection_id,
   scope)` has always been the logical identity of a credential — the router does
   find-or-update on exactly that pair — but nothing enforced it, and every reader does
   `.first()`, so a duplicate would make "which token reached the marketplace" a question
   about the query plan. A FK likewise never existed, so a credential could outlive its
   connection and stay decryptable forever.

   The FK carries NO ON DELETE clause. That is a lifecycle decision, not a default:
   connections are SOFT-revoked (`status = "revoked"`) and never hard-deleted anywhere in
   the codebase, so a cascade could never fire in production while arming a path that
   silently destroys ciphertext.

PREFLIGHT: duplicates and orphans are DETECTED, never repaired. If either exists the
migration fails loudly and changes nothing. Deleting one of two ciphertexts, or dropping
an orphan, would destroy a seller's credential on a guess about which one is real — and
this migration cannot decrypt them to find out, nor should it. A human decides that.

Revision ID: cri1a2b3c4d01
Revises: mpa1a2b3c4d01
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cri1a2b3c4d01"
down_revision: Union[str, None] = "mpa1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── Preflight: detect, never repair ──────────────────────────────────────────
    duplicates = bind.execute(sa.text(
        "SELECT connection_id, scope, COUNT(*) AS n FROM api_credentials "
        "GROUP BY connection_id, scope HAVING COUNT(*) > 1"
    )).fetchall()
    if duplicates:
        raise RuntimeError(
            "F1.2a integrity preflight FAILED: duplicate (connection_id, scope) credentials "
            f"found in {len(duplicates)} group(s). UNIQUE(connection_id, scope) cannot be "
            "applied without deciding which ciphertext is the real one — and this migration "
            "will not guess, delete, or merge encrypted credentials. Resolve the duplicates "
            "deliberately, then re-run. Groups (connection_id, scope, count): "
            + "; ".join(f"({d[0]}, {d[1]}, {d[2]})" for d in duplicates)
        )

    orphans = bind.execute(sa.text(
        "SELECT c.id, c.connection_id FROM api_credentials c "
        "WHERE NOT EXISTS (SELECT 1 FROM marketplace_connections m WHERE m.id = c.connection_id)"
    )).fetchall()
    if orphans:
        raise RuntimeError(
            "F1.2a integrity preflight FAILED: "
            f"{len(orphans)} credential(s) reference a marketplace_connection that does not "
            "exist. The FK cannot be installed over them. This migration will not delete "
            "them, invent a connection, or reassign them to another one — any of those would "
            "silently destroy or misattribute an encrypted credential. Resolve deliberately, "
            "then re-run. Credential ids: " + ", ".join(str(o[0]) for o in orphans)
        )

    # ── 1. Verification axis on marketplace_connections ──────────────────────────
    # server_default is REQUIRED: without it a NOT NULL column cannot be added to a table
    # that already has rows. It also backfills every existing row to `unverified` in the
    # same statement — no separate UPDATE, and no status value is read or written.
    with op.batch_alter_table("marketplace_connections", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "verification_status", sa.String(length=32),
            nullable=False, server_default="unverified",
        ))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(), nullable=True))

    # ── 2. Credential integrity ──────────────────────────────────────────────────
    with op.batch_alter_table("api_credentials", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_apicred_connection", "marketplace_connections", ["connection_id"], ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_apicred_conn_scope", ["connection_id", "scope"],
        )


def downgrade() -> None:
    with op.batch_alter_table("api_credentials", schema=None) as batch_op:
        batch_op.drop_constraint("uq_apicred_conn_scope", type_="unique")
        batch_op.drop_constraint("fk_apicred_connection", type_="foreignkey")

    with op.batch_alter_table("marketplace_connections", schema=None) as batch_op:
        batch_op.drop_column("verified_at")
        batch_op.drop_column("verification_status")
