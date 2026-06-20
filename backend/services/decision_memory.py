"""
Decision Memory service (Memory OS Phase 1, Slice 3) — append-only recording.

Single safe API to remember a decision outcome. INSERT only: it never updates or
deletes a memory row, never mutates Decision or DecisionOutcome, and does not
own a transaction (flush only — the caller commits). NO similarity, NO learning,
NO refuted-loop, NO propagation. NOT yet wired to any close path (Slice 4).

context_group deliberately EXCLUDES action_type, so a future slice can compare
different actions inside the same business context. category / price_band /
margin_band have no spine source yet → "unknown" (honest; Phase 1 never reads
context_group, it only records it).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision_memory import DecisionMemory
from models.product_listing import ProductListing

_UNKNOWN = "unknown"


def build_context_group(
    marketplace: Optional[str],
    category: Optional[str],
    price_band: Optional[str],
    margin_band: Optional[str],
) -> str:
    """
    Deterministic business-context key: marketplace | category | price_band |
    margin_band. action_type is intentionally NOT a parameter. Null → "unknown".
    Same inputs → same output. No ML, no fuzzy matching.
    """
    def _norm(v: Optional[str]) -> str:
        s = (v or _UNKNOWN).strip().lower()
        return s or _UNKNOWN

    return "|".join([_norm(marketplace), _norm(category), _norm(price_band), _norm(margin_band)])


async def _resolve_marketplace(db: AsyncSession, decision) -> str:
    """Marketplace of the decision's listing, or 'unknown' when unavailable."""
    listing_id = getattr(decision, "listing_id", None)
    if not listing_id:
        return _UNKNOWN
    mp = (
        await db.execute(
            select(ProductListing.marketplace).where(ProductListing.id == listing_id)
        )
    ).scalar_one_or_none()
    return mp or _UNKNOWN


async def record_decision_memory(
    db: AsyncSession,
    *,
    decision,
    outcome: str,
    effect_value: Optional[float] = None,
    estimate_value: Optional[float] = None,
    now: Optional[datetime] = None,
) -> DecisionMemory:
    """
    Append one immutable memory row for a decision outcome. Flush only — no
    commit, no mutation of Decision/DecisionOutcome. Tolerates missing optional
    fields. See module docstring.
    """
    marketplace = await _resolve_marketplace(db, decision)
    context_group = build_context_group(
        marketplace=marketplace,
        category=None,      # no spine source yet → unknown
        price_band=None,
        margin_band=None,
    )

    row = DecisionMemory(
        decision_id=decision.id,
        decision_chain_id=getattr(decision, "decision_chain_id", None),
        step_in_chain=getattr(decision, "step_in_chain", 0) or 0,
        product_id=getattr(decision, "physical_product_id", None),
        marketplace=marketplace,
        action_type=getattr(decision, "action_key", None),
        context_group=context_group,
        outcome=outcome,
        effect_value=effect_value,                                   # measured only
        estimate_value=estimate_value if estimate_value is not None  # estimate kept separate
        else getattr(decision, "pnl_impact", None),
        created_at=now or datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    return row
