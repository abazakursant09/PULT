"""
Decision policy engine (Slice 8: GOVERNANCE, read-only).

Filters, prioritizes, and gates decision candidates (Slice 7) under strict,
deterministic rules. It governs PROPOSALS only — it never executes, never
creates/mutates a Decision, never calls the executor, never schedules, never
writes. NO ML, NO optimization.

Precedence (highest first):
  1. confidence filter   — drop candidates with confidence < 0.3.
  2. insufficient override — if insufficient-data ratio > 0.7, suppress all
        action candidates and return only diagnostic (insight) candidates.
  3. margin-proxy safety — if the action-performance margin proxy < 0.4, block
        every non-pricing candidate (pricing stays allowed).
  4. priority           — pricing=HIGH, content=MEDIUM, insight=LOW; rank stable.

The "margin proxy" is a SIMULATED stand-in (overall confirmed/decided across
actions), not a real financial margin.
"""
from __future__ import annotations

from services import decision_effect_aggregator as agg

MIN_CONFIDENCE = 0.3
MARGIN_PROXY_THRESHOLD = 0.4
INSUFFICIENT_OVERRIDE = 0.7

PRICING_ACTIONS = frozenset({"set_price"})
CONTENT_ACTIONS = frozenset({"update_card"})

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _priority(c: dict) -> str:
    if c.get("type") == "action_based":
        if c.get("target") in PRICING_ACTIONS:
            return "high"
        if c.get("target") in CONTENT_ACTIONS:
            return "medium"
        return "low"
    return "low"  # insight_based


def _is_pricing(c: dict) -> bool:
    return c.get("type") == "action_based" and c.get("target") in PRICING_ACTIONS


def _with_priority(c: dict) -> dict:
    return {**c, "priority": _priority(c)}


def _ranked(items: list[dict]) -> list[dict]:
    # Stable sort by priority only — preserves input order within a tier.
    return sorted(items, key=lambda x: _PRIORITY_RANK.get(x["priority"], 3))


async def apply_decision_policy(db, user_id: str, candidates: list[dict]) -> dict:
    """
    Govern `candidates` for one seller. Returns
    {allowed_actions, blocked_actions, priority_ranking}. Read-only, deterministic.
    """
    filtered = [c for c in candidates if c.get("confidence", 0.0) >= MIN_CONFIDENCE]

    perf = await agg.get_action_performance(db, user_id)
    total = sum(p["total"] for p in perf)
    insufficient = sum(p["insufficient_data"] for p in perf)
    insufficient_ratio = (insufficient / total) if total > 0 else 0.0

    # Rule B2 — diagnostic-only override (highest precedence after the filter).
    if insufficient_ratio > INSUFFICIENT_OVERRIDE:
        diagnostics = [_with_priority(c) for c in filtered if c.get("type") == "insight_based"]
        return {
            "allowed_actions": [],
            "blocked_actions": [],
            "priority_ranking": _ranked(diagnostics),
        }

    # Rule B1 — margin proxy (simulated): confirmed / decided across all actions.
    decided = sum(p["confirmed"] + p["refuted"] for p in perf)
    confirmed = sum(p["confirmed"] for p in perf)
    margin_proxy = (confirmed / decided) if decided > 0 else None
    margin_low = margin_proxy is not None and margin_proxy < MARGIN_PROXY_THRESHOLD

    allowed: list[dict] = []
    blocked: list[dict] = []
    for c in filtered:
        pc = _with_priority(c)
        if margin_low and not _is_pricing(c):
            blocked.append({**pc, "blocked_reason": "margin_proxy_below_threshold"})
        else:
            allowed.append(pc)

    return {
        "allowed_actions": allowed,
        "blocked_actions": blocked,
        "priority_ranking": _ranked(allowed),
    }
