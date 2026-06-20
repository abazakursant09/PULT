"""
Insight → Decision bridge (Slice 1: promotion only).

Fixates a promotable production Insight into a Decision (intent layer). This is
the ONLY edge implemented here: Insight → Decision. It does NOT apply, execute,
open/close measurement, schedule, attribute, or learn. No marketplace client,
no executor, no validation.

Idempotent: one Decision per (user_id, insight_key). Re-promotion returns the
existing Decision with created=False. Uniqueness is backed by the
`uq_decision_user_insight` unique index (race-safe via IntegrityError reselect).

Marketplace-agnostic: marketplace/sku normalized through product_resolver; no
WB-only logic. Product/listing resolution against the spine ProductListing is
best-effort — a miss still produces a Decision with null product/listing.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision import Decision
from models.product_listing import ProductListing
from services.marketplace import action_metric_binding
from services.product_resolver import normalize_marketplace, normalize_sku

# Deterministic insight-type → executor action_key. Explicit by design: only the
# three mapped intents get an action_key; everything else is a manual Decision
# (action_key=None). NOTE: action_engine emits "high_rating" (NOT "rating_good")
# and it is intentionally unmapped here — no naming-mismatch carry-over.
_ACTION_KEY: dict[str, Optional[str]] = {
    "margin_crisis":   "set_price",
    "high_ad_spend":   "ad_set_bid",
    "seo_opportunity": "update_card",
    "sales_growth":    None,
    "low_stock":       None,
    "high_rating":     None,
}


@dataclass
class DecisionCandidate:
    """One emitted alternative for an insight: its action and parsed context."""
    insight_key: str
    itype: str
    action_key: str
    marketplace: Optional[str]
    sku: Optional[str]


def emit_candidates(insight_key: str) -> list["DecisionCandidate"]:
    """
    Declarative alternatives emission (A2.5). For a problem with a declared
    action space (margin_crisis → set_price + reduce_discount) emit one candidate
    per action; otherwise fall back to the single legacy action_key. Pure and
    deterministic — NO ranking, NO scores, NO learning, NO DB writes, no Decision
    creation. Persisting multiple candidates as separate measurable Decisions is
    a later slice (needs a (user_id, insight_key, action_key) uniqueness change).
    """
    parts = (insight_key or "").split(":")
    itype = parts[0] if parts and parts[0] else ""
    marketplace = parts[1] if len(parts) > 1 else None
    sku = parts[2] if len(parts) > 2 else None

    space = list(action_metric_binding.problem_action_space(itype))
    if not space:
        single = action_key_for(itype)
        space = [single] if single else []

    return [DecisionCandidate(insight_key, itype, a, marketplace, sku) for a in space if a]


async def emit_ranked_candidates(
    db, *, user_id: str, insight_key: str, context_group: str
) -> list["DecisionCandidate"]:
    """
    Same candidates as emit_candidates, but for margin_crisis SORTED by outcome
    memory ranking (L2). Sort-only — never filters/drops a candidate. Non-margin
    problems and the no-history / below-min_sample cases keep the deterministic
    emit_candidates order. Read-only; creates no Decision.
    """
    candidates = emit_candidates(insight_key)
    if not candidates:
        return []
    itype = candidates[0].itype
    if itype != "margin_crisis":            # ranking applies to margin only
        return candidates

    from services.outcome_memory_ranking import rank_actions
    ranked = await rank_actions(
        db, user_id=user_id, problem_type=itype, context_group=context_group,
        available_actions=[c.action_key for c in candidates],
    )
    by_action = {c.action_key: c for c in candidates}
    ordered = [by_action[r["action_key"]] for r in ranked if r["action_key"] in by_action]
    # Defensive: never drop a candidate the ranker didn't return (it returns all).
    seen = {r["action_key"] for r in ranked}
    ordered.extend(c for c in candidates if c.action_key not in seen)
    return ordered


@dataclass
class InsightPromotionDTO:
    """Minimal, router-free view of an Insight needed to fixate a Decision."""
    insight_key: str
    itype: str
    marketplace: Optional[str]
    sku: Optional[str]
    problem: Optional[str] = None
    cause: Optional[str] = None
    effect: Optional[str] = None
    action: Optional[str] = None
    pnl_impact: Optional[float] = None
    severity: Optional[str] = None
    is_demo: bool = False


@dataclass
class PromoteResult:
    decision_id: Optional[str]
    created: bool
    promotable: bool
    reason: Optional[str] = None


def action_key_for(itype: str) -> Optional[str]:
    """Deterministic action_key for an insight type, or None (manual)."""
    return _ACTION_KEY.get(itype)


def _is_promotable(dto: InsightPromotionDTO) -> bool:
    """False when sku is missing/unknown — such a key is a display-only anchor."""
    if dto.insight_key.endswith(":unknown"):
        return False
    sku_n = normalize_sku(dto.sku)
    return sku_n is not None and sku_n != "UNKNOWN"


async def _resolve_listing(
    db: AsyncSession, user_id: str, marketplace: Optional[str], sku: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """
    Best-effort spine resolution → (physical_product_id, listing_id). Match is
    marketplace-agnostic: canon marketplace + case-insensitive external_id vs
    normalized sku. Miss → (None, None).
    """
    mp = normalize_marketplace(marketplace)
    sku_n = normalize_sku(sku)
    if not mp or sku_n is None:
        return None, None
    listing = (
        await db.execute(
            select(ProductListing).where(
                ProductListing.user_id == user_id,
                ProductListing.marketplace == mp,
                func.upper(ProductListing.external_id) == sku_n,
            )
        )
    ).scalars().first()
    if listing is None:
        return None, None
    return listing.physical_product_id, listing.id


_UNSET = object()  # "action_key not supplied" → use the legacy itype mapping


async def _get_existing(
    db: AsyncSession, user_id: str, insight_key: str, action_key: Optional[str]
) -> Optional[Decision]:
    # SQLAlchemy renders `== None` as `IS NULL`, so null action_key dedups too.
    return (
        await db.execute(
            select(Decision).where(
                Decision.user_id == user_id,
                Decision.insight_key == insight_key,
                Decision.action_key == action_key,
            )
        )
    ).scalars().first()


async def promote_insight_to_decision(
    db: AsyncSession, *, user_id: str, insight: InsightPromotionDTO, action_key=_UNSET
) -> PromoteResult:
    """
    Create or get the Decision for a promotable Insight + action. Idempotent per
    (user_id, insight_key, action_key) — a single insight can hold multiple
    alternative-action Decisions (A2.6). `action_key` defaults to the legacy
    itype mapping (single-action behavior); pass an explicit action_key to
    promote a specific alternative. Promotion only — no apply/measurement.
    """
    # ── hard blocks (no DB write) ────────────────────────────────────────────
    if insight.is_demo:
        return PromoteResult(None, created=False, promotable=False, reason="demo")
    if not user_id:
        return PromoteResult(None, created=False, promotable=False, reason="no_scope")
    if not _is_promotable(insight):
        return PromoteResult(None, created=False, promotable=False, reason="non_promotable_sku")

    ak = action_key_for(insight.itype) if action_key is _UNSET else action_key

    # ── idempotency: one Decision per (user_id, insight_key, action_key) ──────
    existing = await _get_existing(db, user_id, insight.insight_key, ak)
    if existing is not None:
        return PromoteResult(existing.id, created=False, promotable=True, reason=None)

    phys_id, listing_id = await _resolve_listing(
        db, user_id, insight.marketplace, insight.sku
    )

    decision = Decision(
        user_id=user_id,
        insight_key=insight.insight_key,
        physical_product_id=phys_id,
        listing_id=listing_id,
        problem=(insight.problem or insight.itype),
        cause=insight.cause,
        effect=insight.effect,
        action=insight.action,
        action_key=ak,
        pnl_impact=insight.pnl_impact,
        severity=(insight.severity or "warn"),
        source="insight",
        status="open",
        # Memory OS Phase 1: a new promotion starts a new chain at step 0.
        # Existing decisions (returned above) keep their chain untouched.
        decision_chain_id=str(uuid.uuid4()),
        step_in_chain=0,
    )
    db.add(decision)
    try:
        await db.flush()
    except IntegrityError:
        # Race: another promotion won the unique index. Return the winner.
        await db.rollback()
        winner = await _get_existing(db, user_id, insight.insight_key, ak)
        if winner is not None:
            return PromoteResult(winner.id, created=False, promotable=True, reason=None)
        raise

    return PromoteResult(decision.id, created=True, promotable=True, reason=None)


async def promote_insight_alternatives(
    db: AsyncSession, *, user_id: str, insight: InsightPromotionDTO
) -> list[PromoteResult]:
    """
    Promote every declared alternative action for the insight (A2.6). For
    margin_crisis this persists Decision(set_price) AND Decision(reduce_discount),
    each idempotent per (user_id, insight_key, action_key). Single-action insights
    yield one result (unchanged behavior). Promotion only — no apply/measurement.
    """
    candidates = emit_candidates(insight.insight_key)
    if not candidates:
        # No declared action space → fall back to the single legacy promotion.
        return [await promote_insight_to_decision(db, user_id=user_id, insight=insight)]
    results: list[PromoteResult] = []
    for c in candidates:
        results.append(await promote_insight_to_decision(
            db, user_id=user_id, insight=insight, action_key=c.action_key))
    return results
