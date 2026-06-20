"""
Decision candidate engine (Slice 7: PROPOSALS ONLY).

Generates decision *candidates* (proposals) from the Slice 5 read-only
aggregation. This is a suggestion surface — it NEVER creates a Decision, calls
the executor, writes any table, schedules anything, or auto-applies. NO ML, NO
optimization, NO ranking-for-execution.

Rules (deterministic thresholds):
  action  success_rate > 0.7              → scale usage         conf = success_rate
  action  success_rate < 0.3              → reduce / review     conf = 1 - success_rate
  insight refuted_rate  > 0.5             → revise insight logic conf = refuted_rate
  insight insufficient_data/total > 0.5   → improve data collect conf = insufficient share

`confidence` is just the magnitude of the triggering rate (rounded) — a
transparent rule strength, NOT a learned/statistical score. Rates that are None
(nothing decided) never trigger a candidate.
"""
from __future__ import annotations

from services import decision_effect_aggregator as agg

_HIGH_SUCCESS = 0.7
_LOW_SUCCESS = 0.3
_HIGH_REFUTED = 0.5
_HIGH_INSUFFICIENT = 0.5


def _candidate(kind: str, target: str, candidate: str, reason: str, confidence: float) -> dict:
    return {
        "type": kind,
        "target": target,
        "candidate": candidate,
        "reason": reason,
        "confidence": round(confidence, 4),
    }


async def generate_decision_candidates(db, user_id: str) -> list[dict]:
    """Rule-based decision candidates for one seller. Read-only, deterministic."""
    summary = await agg.get_decision_summary(db, user_id)
    if summary["total"] == 0:
        return []

    out: list[dict] = []

    # ── action-based candidates ───────────────────────────────────────────────
    for a in await agg.get_action_performance(db, user_id):
        sr = a["success_rate"]
        if sr is None:
            continue
        target = a["action_key"]
        if sr > _HIGH_SUCCESS:
            out.append(_candidate(
                "action_based", target, f"scale usage of {target}",
                f"success_rate {sr} above {_HIGH_SUCCESS}", sr))
        elif sr < _LOW_SUCCESS:
            out.append(_candidate(
                "action_based", target, f"reduce or review usage of {target}",
                f"success_rate {sr} below {_LOW_SUCCESS}", 1 - sr))

    # ── insight-based candidates ──────────────────────────────────────────────
    for e in await agg.get_insight_effectiveness(db, user_id):
        itype = e["insight_type"]
        rr = e["refuted_rate"]
        if rr is not None and rr > _HIGH_REFUTED:
            out.append(_candidate(
                "insight_based", itype, f"revise insight logic for {itype}",
                f"refuted_rate {rr} above {_HIGH_REFUTED}", rr))
        total = e["total"]
        if total > 0:
            share = e["insufficient_data"] / total
            if share > _HIGH_INSUFFICIENT:
                out.append(_candidate(
                    "insight_based", itype, f"improve data collection for {itype}",
                    f"insufficient share {round(share, 4)} above {_HIGH_INSUFFICIENT}", share))

    return out
