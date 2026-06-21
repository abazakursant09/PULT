"""
Effect Measurement (Decision Outcome A7) — open/close a measurement window for a
promoted engine link and record an engine_effect_observation.

PROVEN result only. This never forecasts, never computes ROI, never fabricates
money. It reads the OBSERVED metric (finance-backed net_profit for the advertising
binding) at baseline (open) and again at close, and classifies a QUALITATIVE
effect_band from the two observed values. If either observation is unavailable the
band is not_evaluated with evidence.reason=insufficient_data — an honest "no
proof", never a failure and never "no effect".

Flow:
  open_effect_measurement  → for each promoted link without an observation yet,
                             read the baseline observed value, create an
                             EngineEffectObservation (baseline_captured_at set,
                             measured_at=None, effect_band=not_evaluated).
  close_effect_measurement → for each still-open observation, read the after value,
                             classify the band, fill measured_at + effect_band, and
                             flip the link to link_status=measured.

Only metrics with a real OBSERVED reader can be measured; everything else stays
not_evaluated. No execution, no API, no UI, no AI. Flush-only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation
from models.seo_signal import SeoSignal
from models.advertising_signal import AdvertisingSignal
from models.review_signal import ReviewSignal
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal

from services.marketplace.finance_metric_reader import read_net_profit
from services.marketplace.metric_reader import MetricSample
from .registry import BY_SIGNAL_KEY

_MODELS = {
    "seo": SeoSignal, "advertising": AdvertisingSignal, "review": ReviewSignal,
    "growth": GrowthSignal, "legal": LegalSignal,
}

# metric_key (from registry) → (reader, direction). direction +1 = higher is better.
# ONLY metrics with a real observed reader are measurable; the rest → not_evaluated.
_READERS = {
    "ad_profit_impact": (read_net_profit, +1),
}

_EPS_REL = 0.05   # within 5% of baseline magnitude → unchanged (no fabricated precision)

NOT_EVALUATED = "not_evaluated"
IMPROVED = "improved"
UNCHANGED = "unchanged"
WORSENED = "worsened"


@dataclass
class MeasureItem:
    link_id: str
    contour: str
    metric_key: Optional[str]
    effect_band: str
    outcome: str                 # opened | skipped | closed
    reason: Optional[str] = None
    observation_id: Optional[str] = None


@dataclass
class OpenResult:
    opened: int = 0
    skipped: int = 0
    items: List[MeasureItem] = field(default_factory=list)


@dataclass
class CloseResult:
    closed: int = 0
    skipped: int = 0
    items: List[MeasureItem] = field(default_factory=list)


def classify_band(before: Optional[float], after: Optional[float], direction: int) -> str:
    """Qualitative band from two OBSERVED values. No forecast, no magnitude claim."""
    if before is None or after is None:
        return NOT_EVALUATED
    delta = after - before
    scale = max(1.0, abs(before))
    if abs(delta) <= _EPS_REL * scale:
        return UNCHANGED
    improved = delta > 0 if direction > 0 else delta < 0
    return IMPROVED if improved else WORSENED


async def _metric_key_for(db, link) -> Optional[str]:
    model = _MODELS.get(link.contour)
    sig = await db.get(model, link.signal_id) if model is not None else None
    if sig is None:
        return None
    entry = BY_SIGNAL_KEY.get(sig.signal_key)
    return entry.default_metric_key if entry else None


async def _read_observed(db, link, metric_key: str, window_days: int, now: datetime):
    """Observed value for the metric, or None when unavailable (never fabricated)."""
    reader_dir = _READERS.get(metric_key)
    if reader_dir is None:
        return None, "no_observed_reader"
    reader, _ = reader_dir
    sample = await reader(db=db, user_id=link.user_id, marketplace=link.marketplace,
                          entity_id=link.sku, window_days=window_days, now=now)
    if isinstance(sample, MetricSample):
        return float(sample.value), None
    return None, getattr(sample, "reason", "unavailable")


async def open_effect_measurement(
    db: AsyncSession, *, user_id: str, window_days: int = 14, now: Optional[datetime] = None,
) -> OpenResult:
    """Open a measurement window (capture baseline) for promoted links. Flush-only."""
    ts = now or datetime.utcnow()
    links = (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == user_id,
        EngineSignalDecisionLink.link_status == "promoted",
        EngineSignalDecisionLink.decision_id.isnot(None)))).scalars().all()
    existing_link_ids = {o.link_id for o in (await db.execute(select(EngineEffectObservation).where(
        EngineEffectObservation.user_id == user_id))).scalars().all()}

    res = OpenResult()
    for link in links:
        if link.id in existing_link_ids:
            res.skipped += 1
            res.items.append(MeasureItem(link.id, link.contour, None, NOT_EVALUATED, "skipped",
                                         "observation already exists"))
            continue
        metric_key = await _metric_key_for(db, link)
        baseline, reason = await _read_observed(db, link, metric_key, window_days, ts) \
            if metric_key else (None, "no_metric")
        evidence = {"baseline": baseline}
        if baseline is None:
            evidence["reason"] = reason
        obs = EngineEffectObservation(
            link_id=link.id, user_id=user_id, insight_key=link.insight_key,
            metric_key=metric_key or "unknown", window_days=window_days,
            baseline_captured_at=ts, measured_at=None, effect_band=NOT_EVALUATED,
            evidence=json.dumps(evidence, ensure_ascii=False), created_at=ts)
        db.add(obs)
        await db.flush()
        res.opened += 1
        res.items.append(MeasureItem(link.id, link.contour, metric_key, NOT_EVALUATED, "opened",
                                     reason, obs.id))
    await db.flush()
    return res


async def close_effect_measurement(
    db: AsyncSession, *, user_id: str, now: Optional[datetime] = None,
) -> CloseResult:
    """Close still-open observations: read the after value, classify the band, flip
    the link to measured. Idempotent (only measured_at IS NULL rows). Flush-only."""
    ts = now or datetime.utcnow()
    open_obs = (await db.execute(select(EngineEffectObservation).where(
        EngineEffectObservation.user_id == user_id,
        EngineEffectObservation.measured_at.is_(None)))).scalars().all()

    res = CloseResult()
    for obs in open_obs:
        link = await db.get(EngineSignalDecisionLink, obs.link_id)
        ev = {}
        try:
            ev = json.loads(obs.evidence) if obs.evidence else {}
        except Exception:
            ev = {}
        baseline = ev.get("baseline")
        after, reason = (None, "no_link")
        direction = 1
        if link is not None:
            rd = _READERS.get(obs.metric_key)
            direction = rd[1] if rd else 1
            after, reason = await _read_observed(db, link, obs.metric_key, obs.window_days or 14, ts)

        band = classify_band(baseline, after, direction)
        if band == NOT_EVALUATED:
            ev["reason"] = "insufficient_data"
            ev.setdefault("missing", reason or ("baseline" if baseline is None else "after"))
        else:
            ev["after"] = after
            ev["observed_delta"] = round(after - baseline, 2)

        obs.effect_band = band
        obs.measured_at = ts
        obs.evidence = json.dumps(ev, ensure_ascii=False)
        if link is not None:
            link.link_status = "measured"
        res.closed += 1
        res.items.append(MeasureItem(obs.link_id, link.contour if link else "?",
                                     obs.metric_key, band, "closed", ev.get("reason"), obs.id))
    await db.flush()
    return res
