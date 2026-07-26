"""PULT-LAUNCH-1.4.5H — source policy: StoreDataSourcePolicy + provider_dataset + coverage

Three additive changes so API and CSV never double-count:
  * store_data_source_policies — one seller decision per (store, metric_type). Absent ⇒ effective csv,
    so today's behaviour is preserved until a seller opts in.
  * marketplace_operations.provider_dataset — the feed a row came from, so the SAME sale seen in two
    feeds (e.g. WB sales + WB finance) is counted from one only. Existing rows backfill to 'legacy',
    which never enters a user total automatically. Then NOT NULL + CHECK.
  * api_sync_states.coverage_complete / skipped_rows_count — honest period coverage: a cursor proves
    progress, not completeness.

Revision ID: sdp1a2b3c4d01
Revises: wbo1a2b3c4d01
Create Date: 2026-07-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "sdp1a2b3c4d01"
down_revision: Union[str, None] = "wbo1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPS = "marketplace_operations"
_STATE = "api_sync_states"
_POLICY = "store_data_source_policies"

_DATASET_CHECK = ("provider_dataset IN ('orders','sales','finance','returns','fbo_postings',"
                  "'fbs_postings','legacy')")


def upgrade() -> None:
    # 1. Seller source-preference table.
    op.create_table(
        _POLICY,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("marketplace_store_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_type", sa.String(length=20), nullable=False),
        sa.Column("preference", sa.String(length=4), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("marketplace_store_id", "metric_type", name="uq_store_source_policy"),
        sa.CheckConstraint("preference IN ('auto','api','csv')", name="ck_source_policy_preference"),
        sa.CheckConstraint(
            "metric_type IN ('catalog','card_content','price','stock','orders','revenue',"
            "'marketplace_fees','logistics','penalties','deductions','returns','cogs','ad_spend')",
            name="ck_source_policy_metric_type"),
    )
    op.create_index("ix_source_policy_store", _POLICY, ["marketplace_store_id"])

    # 2. provider_dataset on marketplace_operations. server_default backfills every existing row to
    #    'legacy'; batch mode recreates the table so the CHECK + composite index apply on SQLite too.
    with op.batch_alter_table(_OPS, schema=None) as b:
        b.add_column(sa.Column("provider_dataset", sa.String(length=20), nullable=False,
                               server_default="legacy"))
        b.create_check_constraint("ck_mp_operation_dataset", _DATASET_CHECK)
        b.create_index("ix_mp_operation_store_dataset_time",
                       ["marketplace_store_id", "provider_dataset", "occurred_at"])

    # 3. Honest coverage on api_sync_states.
    op.add_column(_STATE, sa.Column("coverage_complete", sa.Boolean(), nullable=False,
                                    server_default=sa.false()))
    op.add_column(_STATE, sa.Column("skipped_rows_count", sa.Integer(), nullable=False,
                                    server_default="0"))


def downgrade() -> None:
    op.drop_column(_STATE, "skipped_rows_count")
    op.drop_column(_STATE, "coverage_complete")

    with op.batch_alter_table(_OPS, schema=None) as b:
        b.drop_index("ix_mp_operation_store_dataset_time")
        b.drop_constraint("ck_mp_operation_dataset", type_="check")
        b.drop_column("provider_dataset")

    op.drop_index("ix_source_policy_store", table_name=_POLICY)
    op.drop_table(_POLICY)
