"""
Decision measurement — CLOSE side (validation).

Post-window, reads the realized metric fact and records the observed outcome:
confirmed | refuted | insufficient_data. It records observed post-window state
ONLY. It does NOT perform attribution — `confirmed` means the target metric
moved favorably across the window, NOT that the decision caused it.

Schema: no new fields. Realized read context (entity_id + marketplace) is taken
from the baseline Observation referenced by the outcome; direction from the
metric catalog; due timing from created_at + expected_window_days.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.marketplace import metric_catalog, metric_reader
from services.marketplace.metric_reader import MetricSample
from repositories import decision_outcome as outcome_repo
from models.decision_outcome import DecisionOutcome, DecisionOutcomeLabel
from models.observation import Observation


async def select_due_outcomes(
    db: AsyncSession, *, now: Optional[datetime] = None, limit: Optional[int] = None
) -> list[DecisionOutcome]:
    """
    still_open outcomes whose window has elapsed:
    created_at + expected_window_days <= now. Window math in Python for SQLite/
    Postgres portability.
    """
    now = now or datetime.utcnow()
    res = await db.execute(
        select(DecisionOutcome).where(
            DecisionOutcome.outcome_label == DecisionOutcomeLabel.STILL_OPEN.value
        )
    )
    due = [
        o for o in res.scalars().all()
        if o.created_at is not None
        and (now - o.created_at).total_seconds() >= (o.expected_window_days or 0) * 86400
    ]
    return due[:limit] if limit else due


async def close_measurement(
    db: AsyncSession,
    *,
    outcome: DecisionOutcome,
    token: str,
    now: Optional[datetime] = None,
    min_favorable_delta: float = 0.0,
) -> DecisionOutcome:
    """
    Close one still_open outcome. Idempotent — already-closed outcomes are
    returned untouched.

    insufficient_data (honest, no fabricated delta) when: no baseline fact, the
    baseline row or metric spec is missing, or the realized metric is unreadable.
    Otherwise confirmed/refuted by observed delta vs the metric's direction.
    """
    now = now or datetime.utcnow()

    if outcome.outcome_label != DecisionOutcomeLabel.STILL_OPEN.value:
        return outcome  # already closed

    spec = metric_catalog.get(outcome.metric_name)

    # Need a baseline fact + its entity context to measure anything.
    if outcome.baseline_observation_id is None or spec is None:
        return await outcome_repo.mark_insufficient_data(db, decision_id=outcome.decision_id, measured_at=now)

    baseline = await db.get(Observation, outcome.baseline_observation_id)
    if baseline is None:
        return await outcome_repo.mark_insufficient_data(db, decision_id=outcome.decision_id, measured_at=now)

    sample = await metric_reader.read_metric(
        token=token, marketplace=baseline.marketplace, metric_name=outcome.metric_name,
        entity_id=baseline.entity_id, window_days=outcome.expected_window_days, now=now,
    )
    if not isinstance(sample, MetricSample):
        # MetricUnavailable → honest insufficient_data, never a fabricated value
        return await outcome_repo.mark_insufficient_data(db, decision_id=outcome.decision_id, measured_at=now)

    realized = metric_reader.build_observation(
        sample, user_id=baseline.user_id, entity_grain=baseline.entity_grain,
        entity_id=baseline.entity_id, metric_name=outcome.metric_name,
        marketplace=baseline.marketplace, window_days=outcome.expected_window_days,
    )
    db.add(realized)
    await db.flush()

    delta = sample.value - baseline.value
    favorable = (
        (spec.direction == "higher_better" and delta >  min_favorable_delta) or
        (spec.direction == "lower_better"  and delta < -min_favorable_delta)
    )

    if favorable:
        return await outcome_repo.mark_confirmed(
            db, decision_id=outcome.decision_id, realized_observation_id=realized.id,
            realized_delta=delta, measured_at=now,
        )
    return await outcome_repo.mark_refuted(
        db, decision_id=outcome.decision_id, realized_observation_id=realized.id,
        realized_delta=delta, measured_at=now,
    )
