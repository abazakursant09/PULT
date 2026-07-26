"""PULT-LAUNCH-2.5A — ProtectionEvaluation: economic_verdict + actionability columns

Two nullable, VALIDATED columns so the 2.4 engine's other two results are stored honestly (never
folded into the calculation-status `verdict`, never only in the JSON inputs_snapshot):

  economic_verdict ∈ safe | below_target_margin | emergency_zero_or_loss   (or NULL)
  actionability    ∈ executable | manual_only | unsupported                (or NULL)

Additive only. `verdict` (calculation_status) is untouched; inputs_snapshot is untouched. Any row
written before this slice keeps NULL/NULL — there is no backfill and no guessing from the snapshot
(no runtime writes real evaluations yet). Each CHECK validates its OWN dictionary independently: the
DB never enforces a cross-result rule, so a complete + emergency + unsupported row is allowed.

Downgrade removes only the two columns and their constraints; existing data survives.

Revision ID: pev1a2b3c4d01
Revises: pad1a2b3c4d01
Create Date: 2026-07-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pev1a2b3c4d01"
down_revision: Union[str, None] = "pad1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "protection_evaluations"
_ECON = ("economic_verdict IS NULL OR economic_verdict IN "
         "('safe','below_target_margin','emergency_zero_or_loss')")
_ACT = ("actionability IS NULL OR actionability IN "
        "('executable','manual_only','unsupported')")


def upgrade() -> None:
    with op.batch_alter_table(_T, schema=None) as b:
        b.add_column(sa.Column("economic_verdict", sa.String(length=24), nullable=True))
        b.add_column(sa.Column("actionability", sa.String(length=16), nullable=True))
        b.create_check_constraint("ck_evaluation_economic_verdict", _ECON)
        b.create_check_constraint("ck_evaluation_actionability", _ACT)


def downgrade() -> None:
    with op.batch_alter_table(_T, schema=None) as b:
        b.drop_constraint("ck_evaluation_actionability", type_="check")
        b.drop_constraint("ck_evaluation_economic_verdict", type_="check")
        b.drop_column("actionability")
        b.drop_column("economic_verdict")
