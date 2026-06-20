"""
Decision measurement — OPEN side only.

When a decision is applied, capture the baseline fact of its target metric and
open a still_open outcome. This slice OPENS measurement; it does not close it.

It records: "here is the baseline metric fact at the moment the decision was
applied." It must NOT claim "the decision worked" or "the decision caused later
metric changes." No realized capture, no label transition beyond still_open, no
attribution, no learning here.

Orchestration only (sits ABOVE the repository, which must stay adapter-free):
  action_metric_binding → metric_reader.read_metric → persist baseline
  Observation → repositories.decision_outcome.create_still_open_outcome.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.marketplace import action_metric_binding, metric_reader
from services.marketplace.metric_reader import MetricSample
from repositories import decision_outcome as outcome_repo
from models.decision_outcome import DecisionOutcome


async def open_measurement(
    db: AsyncSession,
    *,
    decision,                       # Decision ORM (.id, .user_id, .action_key)
    entity_id,
    marketplace: str,
    window_days: int,
    token: str,
    now: Optional[datetime] = None,
    tariffs: Optional[set[str]] = None,
) -> Optional[DecisionOutcome]:
    """
    Open measurement for an applied decision. Returns the opened (or existing)
    still_open DecisionOutcome, or None if the decision's action has no
    measurable target metric.

    Baseline is captured when the metric is readable; when it is not, the
    outcome is still opened with baseline_observation_id=None — an honest
    'measurement window started, baseline fact unavailable' state, never a
    fabricated value.
    """
    now = now or datetime.utcnow()

    # Problem-aware binding: derive problem_type from the insight_key prefix so
    # margin_crisis measures net_profit while other problems keep their action
    # binding (pricing set_price → revenue). Malformed/no key → no override.
    ikey = getattr(decision, "insight_key", None)
    problem_type = ikey.split(":", 1)[0] if ikey and ":" in ikey else None
    metric = action_metric_binding.target_metric(
        getattr(decision, "action_key", None), problem_type=problem_type
    )
    if metric is None:
        return None  # nothing measurable to open

    # Idempotent: never reopen / duplicate an existing outcome.
    existing = await outcome_repo.get_by_decision_id(db, decision.id)
    if existing is not None:
        return existing

    sample = await metric_reader.read_metric(
        token=token, marketplace=marketplace, metric_name=metric,
        entity_id=entity_id, window_days=window_days, tariffs=tariffs, now=now,
        db=db, user_id=getattr(decision, "user_id", None),  # compute metrics (net_profit) need them
    )

    baseline_id = None
    if isinstance(sample, MetricSample):
        obs = metric_reader.build_observation(
            sample, user_id=decision.user_id, entity_grain="listing",
            entity_id=entity_id, metric_name=metric, marketplace=marketplace,
            window_days=window_days,
        )
        db.add(obs)
        await db.flush()
        baseline_id = obs.id
    # else: MetricUnavailable → open still_open with no baseline fact (honest)

    return await outcome_repo.create_still_open_outcome(
        db, decision_id=decision.id, metric_name=metric,
        expected_window_days=window_days, baseline_observation_id=baseline_id,
    )
