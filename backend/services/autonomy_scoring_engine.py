"""
Autonomy scoring engine (Slice 11: GRADATION ONLY — no autonomous execution).

Scores a single execution-plan item with an autonomy LEVEL (0/1/2). It does NOT
execute, auto-approve, call the executor, write, or schedule. The approval queue
(Slice 10) remains the gate; this layer only annotates how much autonomy an item
*could* warrant, capped at Level 2 (safe-auto candidate, still human-gated).

Levels:
  0 — manual only:  blocked by policy, OR confidence < 0.5 (high risk).
  2 — safe-auto candidate: ALL of confidence > 0.8, success_rate > 0.7,
        policy risk low (confidence > 0.8), action in the SAFE subset.
  1 — suggested:    everything in between (medium confidence, or high confidence
        that fails a Level-2 condition).

SAFE_ACTIONS is intentionally minimal — update_card only. No pricing auto in
this slice. Level 2 is a *candidate* marker; nothing auto-executes.
"""
from __future__ import annotations

from services import decision_effect_aggregator as agg

_HIGH_CONF = 0.8
_LOW_CONF = 0.5
_GOOD_SUCCESS = 0.7

# Safe subset eligible for Level 2. update_card only — never pricing in this slice.
SAFE_ACTIONS = frozenset({"update_card"})


def _risk_score(confidence: float) -> float:
    """0 (safe) .. 1 (risky), derived purely from confidence."""
    return round(max(0.0, min(1.0, 1.0 - confidence)), 4)


async def compute_autonomy_level(db, user_id: str, execution_plan_item: dict) -> dict:
    """
    {autonomy_level, reason, risk_score} for one execution-plan item. Read-only,
    deterministic. Never executes or auto-approves.
    """
    conf = execution_plan_item.get("confidence") or 0.0
    action = execution_plan_item.get("action_type") or execution_plan_item.get("target")
    blocked = bool(execution_plan_item.get("blocked_reason"))
    risk_score = _risk_score(conf)

    # ── Level 0 — manual only ────────────────────────────────────────────────
    if blocked:
        return {"autonomy_level": 0, "reason": "blocked by policy", "risk_score": risk_score}
    if conf < _LOW_CONF:
        return {"autonomy_level": 0, "reason": f"low confidence {conf} (< {_LOW_CONF})",
                "risk_score": risk_score}

    # success_rate for this action (None if nothing decided yet).
    success_rate = None
    for p in await agg.get_action_performance(db, user_id):
        if p["action_key"] == action:
            success_rate = p["success_rate"]
            break

    # ── Level 2 — safe-auto candidate (all conditions) ───────────────────────
    policy_risk_low = conf > _HIGH_CONF
    if (conf > _HIGH_CONF and policy_risk_low
            and action in SAFE_ACTIONS
            and success_rate is not None and success_rate > _GOOD_SUCCESS):
        return {"autonomy_level": 2,
                "reason": (f"safe-auto candidate: confidence {conf} > {_HIGH_CONF}, "
                           f"success_rate {success_rate} > {_GOOD_SUCCESS}, "
                           f"action {action} in safe subset"),
                "risk_score": risk_score}

    # ── Level 1 — suggested ──────────────────────────────────────────────────
    return {"autonomy_level": 1,
            "reason": (f"suggested: confidence {conf}, success_rate {success_rate}, "
                       f"action {action} not eligible for safe-auto"),
            "risk_score": risk_score}
