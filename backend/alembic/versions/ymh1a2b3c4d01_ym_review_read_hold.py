"""Expand stable accounts with selected YM review-read hold authority.

Old readers ignore these columns: selected egress must be quiesced during rollout
or code rollback. Retain columns on normal rollback. Explicit schema contraction
is allowed only after every hold is empty/expired and determinate; never clear a
hold here to make downgrade succeed. No credentials or provider calls are used.
"""
import sqlalchemy as sa
from alembic import op


revision = "ymh1a2b3c4d01"
down_revision = "csr1a2b3c4d01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("marketplace_accounts", sa.Column(
        "ym_review_read_not_before", sa.DateTime(timezone=True), nullable=True))
    op.add_column("marketplace_accounts", sa.Column(
        "ym_review_read_indeterminate", sa.Boolean(), nullable=False,
        server_default=sa.false()))


def downgrade() -> None:
    # Inspection must precede either DROP, including in offline SQL generation.
    if op.get_context().as_sql:
        raise RuntimeError("Cannot inspect YM review-read holds in offline downgrade")
    bind = op.get_bind()
    accounts = sa.table("marketplace_accounts",
        sa.column("ym_review_read_not_before", sa.DateTime(timezone=True)),
        sa.column("ym_review_read_indeterminate", sa.Boolean()))
    deadline = accounts.c.ym_review_read_not_before
    if bind.dialect.name == "postgresql":
        # Serialize inspection/contraction against concurrent authority writers.
        bind.execute(sa.text("LOCK TABLE marketplace_accounts IN ACCESS EXCLUSIVE MODE"))
        live = deadline >= sa.func.clock_timestamp()
    elif bind.dialect.name == "sqlite":
        # Acquire write ownership before inspecting; no caller data is modified.
        bind.execute(sa.update(accounts).values(
            ym_review_read_indeterminate=accounts.c.ym_review_read_indeterminate))
        parsed = sa.func.julianday(deadline)
        # Floor the current UTC clock, never the stored lower bound. Refusing
        # for up to one extra second is safer than an early fractional expiry.
        now = sa.func.julianday(sa.func.strftime("%Y-%m-%d %H:%M:%S", "now"))
        live = sa.or_(parsed.is_(None), parsed >= now)
    else:
        raise RuntimeError("Cannot safely inspect YM review-read holds on this database")
    unsafe = sa.or_(accounts.c.ym_review_read_indeterminate.is_not(False),
                    sa.and_(deadline.is_not(None), live))
    if bind.execute(sa.select(sa.literal(1)).select_from(accounts).where(unsafe).limit(1)).first():
        raise RuntimeError("Refusing downgrade: live or indeterminate YM review-read hold")
    op.drop_column("marketplace_accounts", "ym_review_read_indeterminate")
    op.drop_column("marketplace_accounts", "ym_review_read_not_before")
