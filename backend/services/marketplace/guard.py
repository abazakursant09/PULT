"""
Centralized guard (RFC §5.3 step 4). Runs BEFORE any network call. All
risk-limiting policy lives here so no action path can bypass it.

Hard rules:
  * negative reviews are NEVER auto-published (L4) — only drafted + escalated.
  * L4 (automated_l4) requires an enabled AutomationRule whose guard passed.
  * per-action daily caps and step/margin limits (used by money/ads slices).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.execution_log import ExecutionLog
from .errors import ExecutionError

NEGATIVE_RATING_MAX = 3  # rating <= 3 is treated as non-positive


async def check(
    *,
    db: AsyncSession,
    user_id: str,
    action_type: str,
    payload: dict,
    mode: str,
    rule: dict | None,
) -> None:
    """Raise ExecutionError.guard(...) if the action must not proceed."""

    # ── Hard rule: never auto-publish a non-positive review ───────────────────
    if action_type == "publish_review_response" and mode == "automated_l4":
        rating = payload.get("rating")
        if rating is None or rating <= NEGATIVE_RATING_MAX:
            raise ExecutionError.guard(
                "NEGATIVE_NEVER_AUTO",
                "non-positive reviews are never auto-published; draft + escalate instead",
            )

    # ── L4 requires an enabled rule ───────────────────────────────────────────
    if mode == "automated_l4":
        if not rule or not rule.get("enabled"):
            raise ExecutionError.guard("NO_ACTIVE_RULE", "L4 requires an enabled AutomationRule")

    # ── Hard rule (AR4): auto-publish ONLY a SAFE review ──────────────────────
    # Rating alone is not enough — a 5★ review whose text carries a complaint marker is classified
    # RISK by the safety policy, and must never auto-publish. This is a second, independent belt to
    # the candidate-query filter, keyed on the persisted safety_category, not on the rating. Placed
    # after the NO_ACTIVE_RULE check so a rule-less call is rejected as NO_ACTIVE_RULE first; either
    # way a non-SAFE review can never proceed.
    if action_type == "publish_review_response" and mode == "automated_l4":
        if payload.get("safety_category") != "SAFE":
            raise ExecutionError.guard(
                "UNSAFE_NEVER_AUTO",
                "only SAFE reviews are auto-published; ATTENTION/RISK are manual",
            )

    rule_guard = (rule or {}).get("guard", {}) if rule else {}

    # ── Daily cap (any action_type that sets one) ─────────────────────────────
    daily_cap = rule_guard.get("daily_cap")
    if daily_cap is not None:
        since = datetime.utcnow() - timedelta(days=1)
        count = await db.scalar(
            select(func.count(ExecutionLog.id)).where(
                ExecutionLog.user_id == user_id,
                ExecutionLog.action_type == action_type,
                ExecutionLog.status == "success",
                ExecutionLog.created_at >= since,
            )
        )
        if (count or 0) >= int(daily_cap):
            raise ExecutionError.guard("DAILY_CAP", f"daily cap {daily_cap} reached")

    # ── Per-window burst cap (AR4) ────────────────────────────────────────────
    max_per_window = rule_guard.get("max_per_window")
    window_seconds = rule_guard.get("window_seconds")
    if max_per_window is not None and window_seconds:
        wsince = datetime.utcnow() - timedelta(seconds=int(window_seconds))
        wcount = await db.scalar(
            select(func.count(ExecutionLog.id)).where(
                ExecutionLog.user_id == user_id,
                ExecutionLog.action_type == action_type,
                ExecutionLog.status == "success",
                ExecutionLog.created_at >= wsince,
            )
        )
        if (wcount or 0) >= int(max_per_window):
            raise ExecutionError.guard(
                "WINDOW_CAP", f"per-window cap {max_per_window}/{window_seconds}s reached")

    # ── Money/ads limits (used by ME-3/ME-4; harmless here) ───────────────────
    max_step = rule_guard.get("max_step_pct")
    step = payload.get("step_pct")
    if max_step is not None and step is not None and abs(step) > float(max_step):
        raise ExecutionError.guard("MAX_STEP", f"step {step}% exceeds limit {max_step}%")

    min_margin = rule_guard.get("min_margin")
    projected = payload.get("projected_margin")
    if min_margin is not None and projected is not None and projected < float(min_margin):
        raise ExecutionError.guard("MIN_MARGIN", f"projected margin {projected} below floor {min_margin}")

    # ── Pricing ceiling ───────────────────────────────────────────────────────
    if action_type == "set_price":
        new_price = payload.get("price")
        max_price = rule_guard.get("max_price")
        if max_price is not None and new_price is not None and float(new_price) > float(max_price):
            raise ExecutionError.guard("MAX_PRICE", f"price {new_price} exceeds ceiling {max_price}")
        min_price = rule_guard.get("min_price")
        if min_price is not None and new_price is not None and float(new_price) < float(min_price):
            raise ExecutionError.guard("MIN_PRICE", f"price {new_price} below floor {min_price}")
