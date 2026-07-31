"""SECURITY-2C-1 — users.token_version for server-side session revocation (additive)

Adds one column: users.token_version (Integer, NOT NULL, server_default '0'). Every access JWT will
carry the issuing user's token_version as the `ver` claim; get_current_user rejects any JWT whose `ver`
does not match the current value. logout / successful password reset / account deletion increment it
(atomic SQL), so a copied cookie stops working immediately and all of a user's sessions end at once.

Existing users get 0 via server_default. Access JWTs issued before this migration carry NO `ver` claim
and are therefore rejected (forced re-login) — the intended pre-beta behaviour. Purely additive; no
other table is touched.

Revision ID: tkv1a2b3c4d01
Revises: rpa1a2b3c4d01
Create Date: 2026-07-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "tkv1a2b3c4d01"
down_revision: Union[str, None] = "rpa1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
