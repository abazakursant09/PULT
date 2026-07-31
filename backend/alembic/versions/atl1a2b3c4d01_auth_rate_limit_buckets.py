"""SECURITY-2C-2 — auth_rate_limit_buckets (PostgreSQL-atomic brute-force throttle state)

Creates ONE new table for the auth throttle: per (action, dimension, key_hash) sliding-window counters.
key_hash is an HMAC-SHA256 hex digest (never a plaintext email/IP); there is no User FK, so an unknown
email is throttled identically to a real one. Additive only — no MFA / reset / LoginAttempt change.

Revision ID: atl1a2b3c4d01
Revises: tkv1a2b3c4d01
Create Date: 2026-07-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "atl1a2b3c4d01"
down_revision: Union[str, None] = "tkv1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS = "action IN ('login', 'register', 'email', 'reset')"
_DIMS = "dimension IN ('pair', 'identity', 'ip')"


def upgrade() -> None:
    op.create_table(
        "auth_rate_limit_buckets",
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("action", "dimension", "key_hash", name="pk_auth_throttle"),
        sa.CheckConstraint("attempts >= 0", name="ck_auth_throttle_attempts_nonneg"),
        sa.CheckConstraint(_ACTIONS, name="ck_auth_throttle_action"),
        sa.CheckConstraint(_DIMS, name="ck_auth_throttle_dimension"),
    )
    op.create_index("ix_auth_throttle_expires", "auth_rate_limit_buckets", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_throttle_expires", table_name="auth_rate_limit_buckets")
    op.drop_table("auth_rate_limit_buckets")
