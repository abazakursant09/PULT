"""Auto Reviews AR0 — ReviewResponse lifecycle foundation (additive)

Prepares review_responses for the real Auto Reviews lifecycle. Purely additive: six nullable /
defaulted columns, two lookup indexes, and one PARTIAL unique index for duplicate-ingestion
protection. No data is read, transformed or moved; existing rows stay valid untouched. /sync,
/publish, auto_publish and the Advisory Runtime are not involved.

The unique index is partial (WHERE external_review_id IS NOT NULL) so legacy / not-yet-synced
rows with a NULL external id are exempt and never collide — this is what keeps it backward
compatible. Both Postgres (prod) and SQLite (tests) support partial indexes.

Revision ID: arv1a2b3c4d01
Revises: mfa1a2b3c4d01
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "arv1a2b3c4d01"
down_revision: Union[str, None] = "mfa1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("review_responses", sa.Column("review_created_at", sa.DateTime(), nullable=True))
    op.add_column("review_responses", sa.Column("safety_category", sa.String(length=20), nullable=True))
    op.add_column("review_responses", sa.Column("manual_required_reason", sa.String(length=255), nullable=True))
    op.add_column("review_responses", sa.Column("failure_reason", sa.String(length=255), nullable=True))
    op.add_column(
        "review_responses",
        sa.Column("publication_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("review_responses", sa.Column("retry_next_at", sa.DateTime(), nullable=True))

    op.create_index("ix_review_responses_product_id", "review_responses", ["product_id"])
    op.create_index("ix_review_responses_status", "review_responses", ["status"])
    op.create_index(
        "uq_review_responses_external",
        "review_responses",
        ["product_id", "external_review_id", "marketplace"],
        unique=True,
        sqlite_where=sa.text("external_review_id IS NOT NULL"),
        postgresql_where=sa.text("external_review_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_review_responses_external", table_name="review_responses")
    op.drop_index("ix_review_responses_status", table_name="review_responses")
    op.drop_index("ix_review_responses_product_id", table_name="review_responses")
    op.drop_column("review_responses", "retry_next_at")
    op.drop_column("review_responses", "publication_attempts")
    op.drop_column("review_responses", "failure_reason")
    op.drop_column("review_responses", "manual_required_reason")
    op.drop_column("review_responses", "safety_category")
    op.drop_column("review_responses", "review_created_at")
