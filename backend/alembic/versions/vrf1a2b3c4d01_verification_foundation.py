"""Verification audit and per-scope credential state (F1.2b-a)

Additive and marketplace-independent. No network call, no decryption, no secret is read
or transformed anywhere in this migration.

1. `connection_verification_attempts` — the append-only trail of what was asked of a
   marketplace and how the answer was classified. It stores an outcome, an HTTP status, a
   retry hint and a schema flag; it stores NO token, NO ciphertext, NO response body and
   NO error text. `credential_id` is nullable because an attempt can fail before any
   credential is selected (nothing stored for that scope, or decryption failed) — and
   those are precisely the attempts worth keeping.

2. `api_credentials` gains per-scope verification state. It has to live per scope, not per
   connection: Wildberries issues category-scoped tokens, so a "Цены и скидки" token is
   rejected by the feedbacks host, and a single flag on the connection cannot express
   "prices works, feedbacks does not". `(connection_id, scope)` is already unique (F1.2a),
   so the credential row IS the scope.

Backfill: every existing credential becomes `unverified` / NULL, which is simply true —
no credential in this database has ever been checked against a marketplace. The
connection-level `verification_status` from F1.2a is untouched here; it becomes a rollup
computed from these values in code.

Revision ID: vrf1a2b3c4d01
Revises: cri1a2b3c4d01
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "vrf1a2b3c4d01"
down_revision: Union[str, None] = "cri1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connection_verification_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        # No ON DELETE: connections are soft-revoked, never hard-deleted, and an audit
        # trail must outlive the thing it audits regardless.
        sa.Column("connection_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_connections.id"), nullable=False),
        sa.Column("credential_id", sa.String(length=36),
                  sa.ForeignKey("api_credentials.id"), nullable=True),
        sa.Column("marketplace_account_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_accounts.id"), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=True),
        sa.Column("probe_key", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("response_schema_status", sa.String(length=20), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cva_connection", "connection_verification_attempts",
                    ["connection_id", "created_at"])
    op.create_index("ix_cva_credential", "connection_verification_attempts",
                    ["credential_id", "created_at"])

    # server_default is required to add a NOT NULL column to a populated table; it also
    # backfills every existing credential to `unverified` in the same statement. No
    # secret_enc is read, and no ciphertext is touched.
    with op.batch_alter_table("api_credentials", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "verification_status", sa.String(length=32),
            nullable=False, server_default="unverified",
        ))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_credentials", schema=None) as batch_op:
        batch_op.drop_column("verified_at")
        batch_op.drop_column("verification_status")

    op.drop_index("ix_cva_credential", table_name="connection_verification_attempts")
    op.drop_index("ix_cva_connection", table_name="connection_verification_attempts")
    op.drop_table("connection_verification_attempts")
