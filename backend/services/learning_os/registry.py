"""
Learning OS Foundation (v1) — Learning Observation Registry.

A READ MODEL over closed measurements. Source of truth: EngineEffectObservation
rows with measured_at IS NOT NULL, joined to their EngineSignalDecisionLink for the
marketplace + action_key. PULT learns ONLY from proven outcomes.

Descriptive, not predictive. Observed facts only — counts of how a given action's
effect band came out. NO AI, NO LLM ranking, NO forecast, NO probability/score, NO
ROI, NO stored percentages. Percentages, if ever shown, are a read-time concern of
the caller — this layer stores none.

MARKETPLACE ISOLATION IS A HARD RULE. Aggregation keys on the CANONICAL marketplace
(normalize_marketplace: wb / ozon / yandex / megamarket). Different marketplaces are
NEVER merged — "stop_auto_promotion on wb" and "stop_auto_promotion on ozon" are two
separate facts, never a blended one. A null/unknown marketplace is its own bucket,
never folded into a real marketplace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.engine_effect_observation import EngineEffectObservation
from models.engine_signal_decision_link import EngineSignalDecisionLink
from services.product_resolver import normalize_marketplace

# observed effect bands (mirror effect_measurement)
_IMPROVED = "improved"
_WORSENED = "worsened"
_UNCHANGED = "unchanged"
_NOT_EVALUATED = "not_evaluated"


@dataclass
class LearningAggregate:
    """Observed outcome counts for one (marketplace, action_key, metric_key).
    No percentages, no scores — counts only."""
    marketplace: Optional[str]
    action_key: Optional[str]
    metric_key: Optional[str]
    total_count: int = 0
    improved_count: int = 0
    worsened_count: int = 0
    unchanged_count: int = 0
    not_evaluated_count: int = 0

    def _add(self, band: str) -> None:
        self.total_count += 1
        if band == _IMPROVED:
            self.improved_count += 1
        elif band == _WORSENED:
            self.worsened_count += 1
        elif band == _UNCHANGED:
            self.unchanged_count += 1
        else:
            self.not_evaluated_count += 1


def _mp_key(value: Optional[str]) -> Optional[str]:
    """Canonical marketplace for the aggregation key. None/unknown stays its own
    bucket — never folded into a real marketplace."""
    if not value:
        return None
    return normalize_marketplace(value)


async def aggregate_learning_observations(
    db: AsyncSession, *, user_id: str,
    marketplace: Optional[str] = None, action_key: Optional[str] = None,
    metric_key: Optional[str] = None,
) -> List[LearningAggregate]:
    """Aggregate every measured observation for the seller into per
    (marketplace, action_key, metric_key) outcome counts.

    Only measured_at IS NOT NULL rows are counted (proven outcomes). Optional
    filters narrow the result; marketplace is compared on the CANONICAL slug so an
    alias never leaks across marketplaces. Read-only."""
    mp_filter = _mp_key(marketplace) if marketplace else None

    rows = (await db.execute(
        select(EngineEffectObservation, EngineSignalDecisionLink)
        .join(EngineSignalDecisionLink,
              EngineSignalDecisionLink.id == EngineEffectObservation.link_id)
        .where(
            EngineEffectObservation.user_id == user_id,
            EngineEffectObservation.measured_at.isnot(None),
        ))).all()

    buckets: dict[tuple, LearningAggregate] = {}
    for obs, link in rows:
        mp = _mp_key(link.marketplace)
        ak = link.action_key
        mk = obs.metric_key
        if mp_filter is not None and mp != mp_filter:
            continue
        if action_key is not None and ak != action_key:
            continue
        if metric_key is not None and mk != metric_key:
            continue
        key = (mp, ak, mk)               # marketplace isolation lives in the key
        agg = buckets.get(key)
        if agg is None:
            agg = buckets[key] = LearningAggregate(marketplace=mp, action_key=ak, metric_key=mk)
        agg._add(obs.effect_band or _NOT_EVALUATED)

    return list(buckets.values())


async def get_action_learning_summary(
    db: AsyncSession, *, user_id: str, marketplace: str, action_key: str,
    metric_key: Optional[str] = None,
) -> Optional[LearningAggregate]:
    """Observed outcome counts for ONE action on ONE marketplace (canonical).

    Combines the per-metric groups of that (marketplace, action_key) into a single
    aggregate. Returns None when there is no measured history. metric_key is set
    only when the history is over a single metric (else None). Observed counts only —
    no prediction, no ranking, no percentage."""
    groups = await aggregate_learning_observations(
        db, user_id=user_id, marketplace=marketplace, action_key=action_key,
        metric_key=metric_key)
    if not groups:
        return None

    mp = _mp_key(marketplace)
    out = LearningAggregate(marketplace=mp, action_key=action_key, metric_key=None)
    metrics = set()
    for g in groups:
        out.total_count += g.total_count
        out.improved_count += g.improved_count
        out.worsened_count += g.worsened_count
        out.unchanged_count += g.unchanged_count
        out.not_evaluated_count += g.not_evaluated_count
        if g.metric_key:
            metrics.add(g.metric_key)
    out.metric_key = next(iter(metrics)) if len(metrics) == 1 else None
    return out


async def rank_action_keys_by_observed(
    db: AsyncSession, *, user_id: str, marketplace: str, action_keys: List[str],
    metric_key: Optional[str] = None, min_sample: int = 10,
) -> List[str]:
    """SORT-ONLY observed ranking of action_keys for ONE marketplace.

    Never filters, never drops — returns exactly the given action_keys, reordered.
    Marketplace-isolated: each action's record is read only for the CANONICAL
    `marketplace`; another marketplace is NEVER a fallback. Descriptive, observed
    counts only — no percentage, no score, no probability, no forecast.

    Order rule (read-time only):
      1. an action with >= min_sample measured outcomes ranks ahead of one without
         (cold-start actions keep the deterministic order, never ranked on thin data);
      2. among ranked actions: higher improved_count first, then lower worsened_count;
      3. final tiebreaker (and the order for all sub-sample actions): the original
         deterministic position passed in.
    """
    originals = list(action_keys)
    summaries = {}
    for ak in originals:
        summaries[ak] = await get_action_learning_summary(
            db, user_id=user_id, marketplace=marketplace, action_key=ak, metric_key=metric_key)

    def _key(item):
        idx, ak = item
        s = summaries.get(ak)
        if s is None or s.total_count < min_sample:
            return (1, 0, 0, idx)            # sub-sample → keep deterministic order
        return (0, -s.improved_count, s.worsened_count, idx)

    return [ak for _, ak in sorted(enumerate(originals), key=_key)]
