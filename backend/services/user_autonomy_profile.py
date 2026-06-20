"""
User-controlled autonomy profile (Slice 12: settings + constraints, no execution).

Returns a per-seller autonomy profile and provides PURE helpers that downstream
layers use to RESPECT it. This layer only filters/annotates — it never executes,
never modifies a Decision, never overrides the policy engine, never bypasses the
approval queue.

Constraint direction is one-way: the profile can only LOWER autonomy or tighten
risk, never raise a scored autonomy level or loosen a policy block. A policy
block (Slice 8) and the approval gate (Slice 10) remain authoritative.

No persistence in this slice: there is no autonomy-settings table yet, so a
conservative default profile is always returned (everything manual, max risk
low). Persisting user-edited profiles is a future slice (would add a table +
migration + write path).
"""
from __future__ import annotations

# How much autonomy each per-category setting permits (cap on Slice 11 levels).
_SETTING_MAX_LEVEL = {
    "manual": 0,
    "suggested": 1,
    "semi_auto": 2,
    "auto": 2,          # capped at 2 — no full autonomy exists in this system
}

_RISK_RANK = {"low": 0, "medium": 1, "high": 2}

# Conservative defaults until the user opts in.
DEFAULT_PROFILE = {
    "pricing": "manual",
    "content": "manual",
    "analytics": "manual",
    "max_risk_level": "low",
}

# action_type → profile category.
_PRICING_ACTIONS = frozenset({"set_price"})
_CONTENT_ACTIONS = frozenset({"update_card"})


def category_for(action_type: str | None) -> str:
    if action_type in _PRICING_ACTIONS:
        return "pricing"
    if action_type in _CONTENT_ACTIONS:
        return "content"
    return "analytics"


async def get_user_autonomy_profile(db, user_id: str) -> dict:
    """
    The seller's autonomy profile. Read-only. No settings store exists yet, so
    the conservative default profile is returned for every user.
    """
    return dict(DEFAULT_PROFILE)


def cap_autonomy_level(profile: dict, action_type: str | None, scored_level: int) -> int:
    """
    Lower a Slice-11 autonomy level to what the profile permits for the action's
    category. Never raises it (min). Unknown setting → manual (0).
    """
    setting = profile.get(category_for(action_type), "manual")
    cap = _SETTING_MAX_LEVEL.get(setting, 0)
    return min(scored_level, cap)


def risk_within_limit(profile: dict, risk_level: str) -> bool:
    """True if `risk_level` is at or below the profile's max_risk_level."""
    limit = _RISK_RANK.get(profile.get("max_risk_level", "low"), 0)
    return _RISK_RANK.get(risk_level, _RISK_RANK["high"]) <= limit
