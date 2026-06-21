"""
Effect Summary / Learning Feedback (Decision Outcome A8) — read-only.

Surfaces the PROVEN outcome of promoted decisions and shapes an honest feedback
record for the Learning OS. It reads engine_signal_decision_link +
engine_effect_observation + Decision and reports, per decision, whether the
observed metric improved / stayed unchanged / worsened — or that it is not
measured yet / not evaluated.

NOT a dashboard, NOT BI. No score, no forecast, no ROI, no money promise, no fake
success. Text is cautious and observed-only. A not_evaluated / not_measured_yet
result is reported truthfully, never dressed up as a win.
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
from models.decision import Decision

# effect_status values
PROVEN_IMPROVED = "proven_improved"
PROVEN_UNCHANGED = "proven_unchanged"
PROVEN_WORSENED = "proven_worsened"
NOT_MEASURED_YET = "not_measured_yet"
NOT_EVALUATED = "not_evaluated"

_BAND_TO_STATUS = {
    "improved": PROVEN_IMPROVED,
    "unchanged": PROVEN_UNCHANGED,
    "worsened": PROVEN_WORSENED,
    "not_evaluated": NOT_EVALUATED,
}

# cautious, observed-only copy (no money, no forecast, no guarantee)
_TEXT = {
    PROVEN_IMPROVED: (
        "Решение показало улучшение по наблюдаемой метрике.",
        "Наблюдаемая метрика после решения выше базовой.",
        "Можно закрепить действие и продолжить наблюдение.",
    ),
    PROVEN_UNCHANGED: (
        "Заметного изменения не зафиксировано.",
        "Наблюдаемая метрика осталась примерно на прежнем уровне.",
        "Продолжить наблюдение.",
    ),
    PROVEN_WORSENED: (
        "После решения метрика ухудшилась, стоит пересмотреть действие.",
        "Наблюдаемая метрика после решения ниже базовой.",
        "Пересмотреть действие.",
    ),
    NOT_EVALUATED: (
        "Недостаточно данных, чтобы доказать эффект.",
        "Не хватает наблюдаемых значений до/после решения.",
        "Нужно больше данных или повторное измерение.",
    ),
    NOT_MEASURED_YET: (
        "Измерение ещё не закрыто.",
        "Окно измерения открыто, итог пока не зафиксирован.",
        "Дождаться закрытия окна измерения.",
    ),
}


@dataclass
class DecisionEffectSummary:
    decision_id: Optional[str]
    insight_key: Optional[str]
    contour: str
    marketplace: Optional[str]
    sku: Optional[str]
    action_key: Optional[str]
    metric_key: Optional[str]
    link_status: str
    effect_band: Optional[str]
    effect_status: str
    measured_at: Optional[datetime]
    evidence: Mapping[str, object] = field(default_factory=dict)
    what_happened: str = ""
    what_it_means: str = ""
    next_action: str = ""


def _loads(text: Optional[str]) -> dict:
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def _status_for(obs: Optional[EngineEffectObservation]) -> str:
    if obs is None or obs.measured_at is None:
        return NOT_MEASURED_YET
    return _BAND_TO_STATUS.get(obs.effect_band, NOT_EVALUATED)


def _summary(link: EngineSignalDecisionLink, obs: Optional[EngineEffectObservation]) -> DecisionEffectSummary:
    status = _status_for(obs)
    what, means, nxt = _TEXT[status]
    return DecisionEffectSummary(
        decision_id=link.decision_id, insight_key=link.insight_key, contour=link.contour,
        marketplace=link.marketplace, sku=link.sku, action_key=link.action_key,
        metric_key=(obs.metric_key if obs else None), link_status=link.link_status,
        effect_band=(obs.effect_band if obs else None), effect_status=status,
        measured_at=(obs.measured_at if obs else None),
        evidence=_loads(obs.evidence) if obs else {},
        what_happened=what, what_it_means=means, next_action=nxt,
    )


async def build_effect_summaries(
    db: AsyncSession, *, user_id: str, contour: Optional[str] = None,
    effect_status: Optional[str] = None,
) -> List[DecisionEffectSummary]:
    """One summary per promoted decision (link with a decision_id). Read-only."""
    stmt = select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == user_id,
        EngineSignalDecisionLink.decision_id.isnot(None))
    if contour:
        stmt = stmt.where(EngineSignalDecisionLink.contour == contour)
    links = (await db.execute(stmt)).scalars().all()

    # latest observation per link
    obs_rows = (await db.execute(select(EngineEffectObservation).where(
        EngineEffectObservation.user_id == user_id))).scalars().all()
    by_link: dict = {}
    for o in obs_rows:
        cur = by_link.get(o.link_id)
        if cur is None or (o.measured_at or o.created_at) >= (cur.measured_at or cur.created_at):
            by_link[o.link_id] = o

    out = [_summary(l, by_link.get(l.id)) for l in links]
    if effect_status:
        out = [s for s in out if s.effect_status == effect_status]
    return out


def aggregate_counts(summaries: List[DecisionEffectSummary]) -> dict:
    """Honest counts per effect_status — NOT a score, NOT an index."""
    counts = {PROVEN_IMPROVED: 0, PROVEN_UNCHANGED: 0, PROVEN_WORSENED: 0,
              NOT_EVALUATED: 0, NOT_MEASURED_YET: 0}
    for s in summaries:
        counts[s.effect_status] = counts.get(s.effect_status, 0) + 1
    counts["total"] = len(summaries)
    return counts
