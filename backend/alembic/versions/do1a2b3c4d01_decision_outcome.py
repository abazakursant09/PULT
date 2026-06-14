"""Decision Outcome foundation — decision_outcomes table

Additive: one new table `decision_outcomes`. Binds a Decision to observed metric
facts (by Observation reference). Records observed state only — NOT causality.
No marketplace column. No raw duplicated metric values (FK to observations).

Revision ID: do1a2b3c4d01
Revises: mc1a2b3c4d01
Create Date: 2026-06-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "do1a2b3c4d01"
down_revision: Union[str, None] = "mc1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_outcomes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("decision_id", sa.String(length=36),
                  sa.ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_name", sa.String(length=40), nullable=False),
        sa.Column("baseline_observation_id", sa.String(length=36),
                  sa.ForeignKey("observations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("realized_observation_id", sa.String(length=36),
                  sa.ForeignKey("observations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expected_window_days", sa.Integer(), nullable=False),
        sa.Column("outcome_label", sa.String(length=20), nullable=False,
                  server_default="still_open"),
        sa.Column("realized_delta", sa.Float(), nullable=True),
        sa.Column("measured_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("decision_id", name="uq_decision_outcome_decision"),
    )
    op.create_index("ix_decision_outcome_label", "decision_outcomes", ["outcome_label"])


def downgrade() -> None:
    op.drop_index("ix_decision_outcome_label", table_name="decision_outcomes")
    op.drop_table("decision_outcomes")
