"""
Decision recommendation engine (Slice 6: RULE-BASED only).

Deterministic, threshold rules over the Slice 5 aggregator output. NO ML, NO
scoring model, NO optimization, NO learning, NO auto-execution, NO scheduler, NO
DB writes. Reads only via decision_effect_aggregator (itself read-only).

Rules:
  action  success_rate > 0.7              → "increase usage of {action_key}"  (medium)
  action  success_rate < 0.3              → "reduce usage of {action_key}"    (high)
  insight refuted_rate  > 0.5             → "review insight type {itype}"      (high)
  insight insufficient_data/total > 0.5   → "insufficient data for {itype}"    (low)

Rates that are None (nothing decided yet) never trigger a rule — no fabricated
signal. Output order is stable: actions (sorted by action_key) then insights
(sorted by insight_type), exactly as the aggregator returns them.
"""
from __future__ import annotations

from services import decision_effect_aggregator as agg

_HIGH_SUCCESS = 0.7
_LOW_SUCCESS = 0.3
_HIGH_REFUTED = 0.5
_HIGH_INSUFFICIENT = 0.5


def _rec(kind: str, target: str, message: str, priority: str) -> dict:
    return {"type": kind, "target": target, "message": message, "priority": priority}


async def generate_recommendations(db, user_id: str) -> list[dict]:
    """Rule-based recommendations for one seller. Read-only, deterministic."""
    recs: list[dict] = []

    # ── action-level rules ────────────────────────────────────────────────────
    for a in await agg.get_action_performance(db, user_id):
        sr = a["success_rate"]
        if sr is None:
            continue
        target = a["action_key"]
        if sr > _HIGH_SUCCESS:
            recs.append(_rec("action", target, f"increase usage of {target}", "medium"))
        elif sr < _LOW_SUCCESS:
            recs.append(_rec("action", target, f"reduce usage of {target}", "high"))

    # ── insight-level rules ───────────────────────────────────────────────────
    for e in await agg.get_insight_effectiveness(db, user_id):
        itype = e["insight_type"]
        rr = e["refuted_rate"]
        if rr is not None and rr > _HIGH_REFUTED:
            recs.append(_rec("insight", itype, f"review insight type {itype}", "high"))
        total = e["total"]
        if total > 0 and (e["insufficient_data"] / total) > _HIGH_INSUFFICIENT:
            recs.append(_rec("insight", itype, f"insufficient data for {itype}", "low"))

    return recs
