"""
Decision effect aggregator (Slice 5: READ-ONLY aggregation).

Pure statistics over DecisionOutcome, joined to Decision for action_key /
insight_type. NO ML, NO prediction, NO scoring/ranking model, NO optimization,
NO scheduling, NO writes. Every function is a read: it opens no transaction,
adds/updates/deletes nothing.

Honesty of rates:
- A measurement only has a verdict once closed to confirmed or refuted. Rates are
  computed over the DECIDED set (confirmed + refuted); still_open and
  insufficient_data carry no verdict and are excluded from the rate denominator.
  insufficient_rate is reported separately over the full total so the unreadable
  share stays visible rather than hidden.
- Rates are None when the denominator is 0 (never a fabricated 0.0).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision import Decision
from models.decision_outcome import DecisionOutcome

_CONFIRMED = "confirmed"
_REFUTED = "refuted"
_INSUFFICIENT = "insufficient_data"
_STILL_OPEN = "still_open"


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """Ratio rounded to 4dp, or None when there is nothing to divide by."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _insight_type(insight_key: Optional[str]) -> str:
    """itype prefix of '<itype>:<mp>:<sku>'. Null/legacy → 'unknown'."""
    if not insight_key:
        return "unknown"
    return insight_key.split(":", 1)[0]


async def _load_rows(db: AsyncSession, user_id: str) -> list[tuple[Optional[str], Optional[str], str]]:
    """(action_key, insight_key, outcome_label) for one seller. Read-only."""
    res = await db.execute(
        select(Decision.action_key, Decision.insight_key, DecisionOutcome.outcome_label)
        .join(DecisionOutcome, DecisionOutcome.decision_id == Decision.id)
        .where(Decision.user_id == user_id)
    )
    return [tuple(r) for r in res.all()]


def _empty_counts() -> dict:
    return {"total": 0, _CONFIRMED: 0, _REFUTED: 0, _INSUFFICIENT: 0, _STILL_OPEN: 0}


def _tally(counts: dict, label: str) -> None:
    counts["total"] += 1
    if label in counts:
        counts[label] += 1


async def get_decision_summary(db: AsyncSession, user_id: str) -> dict:
    """Per-seller outcome counts. Read-only."""
    counts = _empty_counts()
    for _action, _insight, label in await _load_rows(db, user_id):
        _tally(counts, label)
    return counts


async def get_action_performance(db: AsyncSession, user_id: str) -> list[dict]:
    """
    Grouped by Decision.action_key. success_rate over decided (confirmed+refuted);
    insufficient_rate over total. action_key None → 'unmapped'.
    """
    groups: dict[str, dict] = {}
    for action_key, _insight, label in await _load_rows(db, user_id):
        key = action_key or "unmapped"
        _tally(groups.setdefault(key, _empty_counts()), label)

    out: list[dict] = []
    for key, c in sorted(groups.items()):
        decided = c[_CONFIRMED] + c[_REFUTED]
        out.append({
            "action_key": key,
            "total": c["total"],
            "confirmed": c[_CONFIRMED],
            "refuted": c[_REFUTED],
            "insufficient_data": c[_INSUFFICIENT],
            "still_open": c[_STILL_OPEN],
            "success_rate": _rate(c[_CONFIRMED], decided),
            "insufficient_rate": _rate(c[_INSUFFICIENT], c["total"]),
        })
    return out


async def get_insight_effectiveness(db: AsyncSession, user_id: str) -> list[dict]:
    """
    Grouped by insight type (Decision.insight_key prefix). success_rate +
    refuted_rate over decided (confirmed+refuted).
    """
    groups: dict[str, dict] = {}
    for _action, insight_key, label in await _load_rows(db, user_id):
        _tally(groups.setdefault(_insight_type(insight_key), _empty_counts()), label)

    out: list[dict] = []
    for itype, c in sorted(groups.items()):
        decided = c[_CONFIRMED] + c[_REFUTED]
        out.append({
            "insight_type": itype,
            "total": c["total"],
            "confirmed": c[_CONFIRMED],
            "refuted": c[_REFUTED],
            "insufficient_data": c[_INSUFFICIENT],
            "still_open": c[_STILL_OPEN],
            "success_rate": _rate(c[_CONFIRMED], decided),
            "refuted_rate": _rate(c[_REFUTED], decided),
        })
    return out
