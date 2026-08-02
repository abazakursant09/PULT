"""SECURITY-2D-1B-B — partial UNIQUE on (user_id, idempotency_key) WHERE key LIKE 'v1:%'.

DB-enforced at-most-one claim per (user, v1 operation key). The index is SCOPED to the new 'v1:%'
namespace, so legacy content-derived keys (price:/bid:/state:/card:, which legitimately repeat over
time) and NULL keys are exempt and cannot make index creation collide. Before creating it we PREFLIGHT
for any already-v1 duplicates (expected 0 pre-1B-B) and fail closed with a NUMERIC count only — never
printing user / key / payload, never deleting, renaming or picking "the newest" row.

No column change; request_fingerprint / dispatch_started_at (efp1a2b3c4d01) are untouched. Downgrade
drops only this index.

Revision ID: uqc1a2b3c4d01
Revises: efp1a2b3c4d01
Create Date: 2026-08-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "uqc1a2b3c4d01"
down_revision: Union[str, None] = "efp1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_execlog_op_claim"
_WHERE = "idempotency_key LIKE 'v1:%'"


def upgrade() -> None:
    bind = op.get_bind()
    # Fail-closed preflight: no two rows may already share (user_id, v1 idempotency_key).
    dup_count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM ("
        "  SELECT user_id, idempotency_key FROM execution_logs"
        "  WHERE idempotency_key LIKE 'v1:%'"
        "  GROUP BY user_id, idempotency_key HAVING COUNT(*) > 1"
        ") d"
    )).scalar()
    if dup_count and int(dup_count) > 0:
        raise RuntimeError(
            f"SECURITY-2D-1B-B preflight: {int(dup_count)} duplicate (user_id, v1 idempotency_key) "
            "group(s) present — refusing to create the unique claim index. Resolve manually."
        )
    op.create_index(_INDEX, "execution_logs", ["user_id", "idempotency_key"],
                    unique=True, sqlite_where=sa.text(_WHERE), postgresql_where=sa.text(_WHERE))


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="execution_logs")
