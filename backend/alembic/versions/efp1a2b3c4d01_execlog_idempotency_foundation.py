"""SECURITY-2D-1B-A — additive idempotency foundation on execution_logs (unwired)

Adds TWO nullable columns, nothing else. No UNIQUE, no CHECK, no status change, no backfill — existing
rows get NULL/NULL and runtime behaviour is byte-for-byte unchanged. The DB-enforced claim (partial
UNIQUE) and the runtime wiring land in SECURITY-2D-1B-B.

  request_fingerprint : "fp1:" + 64 lowercase hex (= 68 chars) → String(72) for headroom. Describes WHAT
                        an operation does (its contents), never its identity.
  dispatch_started_at : timezone-aware; will be stamped just before the provider call in 1B-B, so a
                        crashed claim can be proven to be pre-dispatch (safe to retry) vs post-dispatch.

Revision ID: efp1a2b3c4d01
Revises: mfd1a2b3c4d01
Create Date: 2026-08-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "efp1a2b3c4d01"
down_revision: Union[str, None] = "mfd1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("execution_logs",
                  sa.Column("request_fingerprint", sa.String(length=72), nullable=True))
    op.add_column("execution_logs",
                  sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("execution_logs", "dispatch_started_at")
    op.drop_column("execution_logs", "request_fingerprint")
