"""Expand explicit account closure state; no backfill or deletion.

Rollback of application code requires closure/recovery endpoints quiesced: older
code does not enforce the deadline. Normal rollback retains these columns.
"""
import sqlalchemy as sa
from alembic import op

revision = 'acl1a2b3c4d01'
down_revision = 'ymh1a2b3c4d01'
branch_labels = None
depends_on = None

FIELDS = ('closure_policy_version', 'closure_started_at',
          'closure_restore_requested_at', 'closure_purge_started_at')


def upgrade():
    op.add_column('users', sa.Column(FIELDS[0], sa.String(32), nullable=True))
    for field in FIELDS[1:]:
        op.add_column('users', sa.Column(field, sa.DateTime(timezone=True), nullable=True))


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError('Cannot inspect closure records in offline downgrade')
    bind = op.get_bind()
    users = sa.table('users', *(sa.column(name) for name in FIELDS))
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text('LOCK TABLE users IN ACCESS EXCLUSIVE MODE'))
    elif bind.dialect.name == 'sqlite':
        bind.execute(sa.update(users).values(closure_policy_version=users.c.closure_policy_version))
    else:
        raise RuntimeError('Unsupported closure downgrade dialect')
    if bind.execute(sa.select(sa.literal(1)).select_from(users).where(
            sa.or_(*(users.c[name].is_not(None) for name in FIELDS))).limit(1)).first():
        raise RuntimeError('Closure evidence exists; retain lifecycle columns')
    for field in reversed(FIELDS):
        op.drop_column('users', field)
