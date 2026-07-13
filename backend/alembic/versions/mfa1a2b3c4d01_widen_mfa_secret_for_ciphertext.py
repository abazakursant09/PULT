"""Widen mfa_secrets.secret for encrypted TOTP seeds (security hardening)

The TOTP seed is now stored as a Fernet token (services/mfa_crypto) instead of a bare base32
seed, so anyone with a DB read can no longer regenerate 2FA codes. Fernet ciphertext runs ~120
chars, which does not fit the old String(64). This migration only widens the column to
String(255); it reads, decrypts and transforms NO secret — existing plaintext rows are left
exactly as they are and are read back transparently in code (mfa_crypto.load_secret falls back to
plaintext for a value that is not a Fernet token). New writes are encrypted; a legacy row becomes
encrypted the next time that user re-runs /setup.

Revision ID: mfa1a2b3c4d01
Revises: vrf1a2b3c4d01
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "mfa1a2b3c4d01"
down_revision: Union[str, None] = "vrf1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("mfa_secrets") as batch:
        batch.alter_column(
            "secret",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Not safe to auto-run against real data: encrypted seeds (~120 chars) do not fit back into
    # String(64) and would be truncated into garbage. Left as a no-op so an accidental downgrade
    # cannot silently corrupt 2FA. Widening a column back down must be a deliberate, manual step.
    pass
