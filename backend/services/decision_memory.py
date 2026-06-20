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

from sqlalchemy import func, select
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


# ── read-only chain helpers (Slice 5) ────────────────────────────────────────
# Pure reads over decision_memory. NO write, NO refuted-loop, NO candidate
# creation, NO similarity/learning. Just answer questions about a chain.

async def get_used_actions(db: AsyncSession, decision_chain_id: str) -> set[str]:
    """action_type values already tried in the chain (any outcome). Null/empty ignored."""
    if not decision_chain_id:
        return set()
    rows = (
        await db.execute(
            select(DecisionMemory.action_type).where(
                DecisionMemory.decision_chain_id == decision_chain_id
            )
        )
    ).scalars().all()
    return {a for a in rows if a}


async def get_current_step(db: AsyncSession, decision_chain_id: str) -> int:
    """max(step_in_chain) for the chain; 0 when no rows. Null step → 0."""
    if not decision_chain_id:
        return 0
    m = (
        await db.execute(
            select(func.max(DecisionMemory.step_in_chain)).where(
                DecisionMemory.decision_chain_id == decision_chain_id
            )
        )
    ).scalar()
    return int(m or 0)


async def get_chain_status(db: AsyncSession, decision_chain_id: str) -> str:
    """
    'confirmed' if any row confirmed; else 'stopped' if max step >= 3 AND that
    max step has a refuted row; else 'open'. Insufficient never stops/advances.
    """
    if not decision_chain_id:
        return "open"
    rows = (
        await db.execute(
            select(DecisionMemory.step_in_chain, DecisionMemory.outcome).where(
                DecisionMemory.decision_chain_id == decision_chain_id
            )
        )
    ).all()
    if not rows:
        return "open"
    if any(outcome == "confirmed" for _step, outcome in rows):
        return "confirmed"
    max_step = max((step or 0) for step, _outcome in rows)
    if max_step >= 3 and any((step or 0) == max_step and outcome == "refuted"
                             for step, outcome in rows):
        return "stopped"
    return "open"
