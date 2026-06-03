"""Marketplace Execution Layer (ME-1)

Additive: 4 new tables (marketplace_connections, api_credentials,
execution_logs, automation_rules) + 4 nullable columns on review_responses for
real publish provenance. Breaks zero existing readers.

Revision ID: me1a2b3c4d01
Revises: b1f3c0de5a01
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "me1a2b3c4d01"
down_revision: Union[str, None] = "b1f3c0de5a01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketplace_connections",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="connected"),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("ozon_client_id", sa.String(length=64), nullable=True),
        sa.Column("last_check_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_mp_conn_user", "marketplace_connections", ["user_id"])
    op.create_index("ix_mp_conn_user_mp", "marketplace_connections", ["user_id", "marketplace"])

    op.create_table(
        "api_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_apicred_conn", "api_credentials", ["connection_id"])
    op.create_index("ix_apicred_conn_scope", "api_credentials", ["connection_id", "scope"])

    op.create_table(
        "execution_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column("insight_key", sa.String(length=200), nullable=True),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("api_request_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("reverted_from", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_execlog_user", "execution_logs", ["user_id"])
    op.create_index("ix_execlog_user_action", "execution_logs", ["user_id", "action_type"])
    op.create_index("ix_execlog_idem", "execution_logs", ["user_id", "action_type", "idempotency_key"])

    op.create_table(
        "automation_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("contour", sa.String(length=30), nullable=False),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("trigger", sa.JSON(), nullable=False),
        sa.Column("guard", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="confirm"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_autorule_user", "automation_rules", ["user_id"])
    op.create_index("ix_autorule_user_action", "automation_rules", ["user_id", "action_type"])

    with op.batch_alter_table("review_responses", schema=None) as batch_op:
        batch_op.add_column(sa.Column("external_review_id", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("marketplace", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("execution_log_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("review_responses", schema=None) as batch_op:
        batch_op.drop_column("execution_log_id")
        batch_op.drop_column("published_at")
        batch_op.drop_column("marketplace")
        batch_op.drop_column("external_review_id")

    op.drop_index("ix_autorule_user_action", table_name="automation_rules")
    op.drop_index("ix_autorule_user", table_name="automation_rules")
    op.drop_table("automation_rules")

    op.drop_index("ix_execlog_idem", table_name="execution_logs")
    op.drop_index("ix_execlog_user_action", table_name="execution_logs")
    op.drop_index("ix_execlog_user", table_name="execution_logs")
    op.drop_table("execution_logs")

    op.drop_index("ix_apicred_conn_scope", table_name="api_credentials")
    op.drop_index("ix_apicred_conn", table_name="api_credentials")
    op.drop_table("api_credentials")

    op.drop_index("ix_mp_conn_user_mp", table_name="marketplace_connections")
    op.drop_index("ix_mp_conn_user", table_name="marketplace_connections")
    op.drop_table("marketplace_connections")
