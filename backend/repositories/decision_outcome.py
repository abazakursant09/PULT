"""
Decision Outcome repository (foundation).

Persists outcome labels + Observation references for a decision. Foundation
scope ONLY — no execute hook, no baseline capture, no validation closer, no
attribution, no learning.

Hard rules (doctrine boundaries):
- MUST NOT call marketplace adapters.
- MUST NOT call metric_reader (measurement is a later slice).
- Only persists labels + observation references.
- realized_delta may be set only when realized_observation_id is present.
- MUST NOT mutate Decision.status.

Lifecycle model: ONE row per decision, updated in place over its lifecycle
(unique decision_id). Chosen over append-only events — smaller and matches the
"one active outcome per decision" architecture; the append-only fact history
already lives in `observations`, so duplicating an event stream here adds no
truth, only surface.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision_outcome import DecisionOutcome, DecisionOutcomeLabel


async def get_by_decision_id(db: AsyncSession, decision_id: str) -> Optional[DecisionOutcome]:
    res = await db.execute(
        select(DecisionOutcome).where(DecisionOutcome.decision_id == decision_id)
    )
    return res.scalar_one_or_none()


async def create_still_open_outcome(
    db: AsyncSession,
    *,
    decision_id: str,
    metric_name: str,
    expected_window_days: int,
    baseline_observation_id: Optional[str] = None,
) -> DecisionOutcome:
    row = DecisionOutcome(
        decision_id=decision_id,
        metric_name=metric_name,
        expected_window_days=expected_window_days,
        baseline_observation_id=baseline_observation_id,
        outcome_label=DecisionOutcomeLabel.STILL_OPEN.value,
    )
    db.add(row)
    await db.flush()
    return row


async def _require(db: AsyncSession, decision_id: str) -> DecisionOutcome:
    row = await get_by_decision_id(db, decision_id)
    if row is None:
        raise ValueError(f"no decision_outcome for decision {decision_id}")
    return row


async def _mark_realized(
    db: AsyncSession,
    decision_id: str,
    label: str,
    *,
    realized_observation_id: str,
    realized_delta: Optional[float],
    measured_at: Optional[datetime],
) -> DecisionOutcome:
    if realized_observation_id is None:
        raise ValueError(f"{label} requires realized_observation_id")
    if realized_delta is not None and realized_observation_id is None:
        raise ValueError("realized_delta requires realized_observation_id")
    row = await _require(db, decision_id)
    row.outcome_label = label
    row.realized_observation_id = realized_observation_id
    row.realized_delta = realized_delta
    row.measured_at = measured_at or datetime.utcnow()
    await db.flush()
    return row


async def mark_confirmed(
    db: AsyncSession, *, decision_id: str, realized_observation_id: str,
    realized_delta: float, measured_at: Optional[datetime] = None,
) -> DecisionOutcome:
    return await _mark_realized(
        db, decision_id, DecisionOutcomeLabel.CONFIRMED.value,
        realized_observation_id=realized_observation_id,
        realized_delta=realized_delta, measured_at=measured_at,
    )


async def mark_refuted(
    db: AsyncSession, *, decision_id: str, realized_observation_id: str,
    realized_delta: float, measured_at: Optional[datetime] = None,
) -> DecisionOutcome:
    return await _mark_realized(
        db, decision_id, DecisionOutcomeLabel.REFUTED.value,
        realized_observation_id=realized_observation_id,
        realized_delta=realized_delta, measured_at=measured_at,
    )


async def mark_not_taken(
    db: AsyncSession, *, decision_id: str, measured_at: Optional[datetime] = None,
) -> DecisionOutcome:
    row = await _require(db, decision_id)
    row.outcome_label = DecisionOutcomeLabel.NOT_TAKEN.value
    row.measured_at = measured_at or datetime.utcnow()
    # no observations, no delta
    await db.flush()
    return row


async def mark_insufficient_data(
    db: AsyncSession, *, decision_id: str, measured_at: Optional[datetime] = None,
) -> DecisionOutcome:
    row = await _require(db, decision_id)
    row.outcome_label = DecisionOutcomeLabel.INSUFFICIENT_DATA.value
    # explicitly NO fabricated delta — realized_delta stays None
    row.measured_at = measured_at or datetime.utcnow()
    await db.flush()
    return row
