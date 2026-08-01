"""SECURITY-2C-4A — allow the MFA throttle actions in the auth_rate_limit_buckets CHECK

The durable auth throttle (SECURITY-2C-2) gains two MFA-guess actions: `mfa_login` (POST /login/mfa)
and `mfa_manage` (enable + disable). Both are guarded by the DB CHECK `action IN (...)`, which currently
only allows login / register / email / reset, so inserting an MFA bucket would be rejected. This
migration widens ONLY that CHECK. No column / table / index change; existing buckets are preserved.

downgrade deletes the ephemeral mfa_login / mfa_manage rows FIRST (they would violate the reverted,
stricter CHECK) and then restores the original 4-action CHECK. Those rows are throttle state, safe to
drop. No other action's rows are touched.

Revision ID: mfd1a2b3c4d01
Revises: lad1a2b3c4d01
Create Date: 2026-08-01
"""
from typing import Sequence, Union

from alembic import op


revision: str = "mfd1a2b3c4d01"
down_revision: Union[str, None] = "lad1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK = "ck_auth_throttle_action"
_OLD = "action IN ('login', 'register', 'email', 'reset')"
_NEW = "action IN ('login', 'register', 'email', 'reset', 'mfa_login', 'mfa_manage')"


def upgrade() -> None:
    # batch_alter_table so this runs on PostgreSQL (direct DROP/ADD CONSTRAINT) and SQLite (table
    # rebuild, which is the only way SQLite can alter a CHECK). Existing rows are copied through.
    with op.batch_alter_table("auth_rate_limit_buckets") as b:
        b.drop_constraint(_CK, type_="check")
        b.create_check_constraint(_CK, _NEW)


def downgrade() -> None:
    # Remove the new actions' ephemeral rows BEFORE re-adding the stricter CHECK, or the recreate fails.
    op.execute("DELETE FROM auth_rate_limit_buckets WHERE action IN ('mfa_login', 'mfa_manage')")
    with op.batch_alter_table("auth_rate_limit_buckets") as b:
        b.drop_constraint(_CK, type_="check")
        b.create_check_constraint(_CK, _OLD)
