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
from models.product import Product
from models.imported_finance import ImportedFinanceRow

_UNKNOWN = "unknown"

# Deterministic band thresholds (L3). Missing data → "unknown", never fabricated.
PRICE_BAND_LOW = 500.0     # ₽ — below = low
PRICE_BAND_HIGH = 2000.0   # ₽ — at/above = high
MARGIN_BAND_LOW = 10.0     # % — below = low_margin
MARGIN_BAND_HIGH = 25.0    # % — at/above = high_margin

# Canonical marketplace → finance-row aliases (imports may store raw labels).
_MP_ALIASES = {
    "wb": ("wb", "wildberries"),
    "ozon": ("ozon",),
    "yandex": ("yandex", "yandex_market", "ym"),
}


def price_band(price: Optional[float]) -> str:
    """Deterministic price band: low | mid | high; None → unknown."""
    if price is None:
        return _UNKNOWN
    if price < PRICE_BAND_LOW:
        return "low"
    if price < PRICE_BAND_HIGH:
        return "mid"
    return "high"


def margin_band(margin_pct: Optional[float]) -> str:
    """Deterministic margin band: low_margin | mid_margin | high_margin; None → unknown."""
    if margin_pct is None:
        return _UNKNOWN
    if margin_pct < MARGIN_BAND_LOW:
        return "low_margin"
    if margin_pct < MARGIN_BAND_HIGH:
        return "mid_margin"
    return "high_margin"


def _sku_from_key(insight_key: Optional[str]) -> Optional[str]:
    if not insight_key:
        return None
    parts = insight_key.split(":")
    return parts[2] if len(parts) > 2 else None


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


def _norm_mp(mp: Optional[str]) -> str:
    s = (mp or "").strip().lower()
    return s


async def _compute_margin_pct(db: AsyncSession, user_id: str, marketplace: str,
                              sku: Optional[str]) -> Optional[float]:
    """Margin % from imported finance for (user, marketplace, sku). None if no data."""
    if not (user_id and sku):
        return None
    aliases = _MP_ALIASES.get(_norm_mp(marketplace), (_norm_mp(marketplace),))
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0),
                func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0),
                func.count(),
            ).where(
                ImportedFinanceRow.user_id == user_id,
                ImportedFinanceRow.marketplace.in_(aliases),
                ImportedFinanceRow.sku == str(sku),
            )
        )
    ).one()
    net, rev, n = float(row[0]), float(row[1]), int(row[2])
    if not n or rev <= 0:
        return None
    return net / rev * 100.0


async def _resolve_context(db: AsyncSession, decision, marketplace: str) -> tuple[Optional[str], str, str]:
    """
    Best-effort (category, price_band, margin_band) from existing domain models.
    category/price via the listing's legacy Product; margin via imported finance.
    Anything unavailable → 'unknown' (never fabricated). Read-only.
    """
    category: Optional[str] = None
    price: Optional[float] = None
    listing_id = getattr(decision, "listing_id", None)
    if listing_id:
        listing = await db.get(ProductListing, listing_id)
        legacy_id = getattr(listing, "legacy_product_id", None) if listing else None
        if legacy_id:
            prod = await db.get(Product, legacy_id)
            if prod is not None:
                category = prod.category
                price = prod.price

    sku = _sku_from_key(getattr(decision, "insight_key", None))
    margin_pct = await _compute_margin_pct(db, getattr(decision, "user_id", None),
                                           marketplace, sku)
    return category, price_band(price), margin_band(margin_pct)


class _InsightContextShim:
    """
    Duck-typed stand-in so the write-side resolvers (_resolve_marketplace /
    _resolve_context) can run for a read-side insight that has no Decision row
    yet. Carries exactly the attributes those resolvers read via getattr.
    """
    __slots__ = ("user_id", "insight_key", "listing_id")

    def __init__(self, user_id, insight_key, listing_id):
        self.user_id = user_id
        self.insight_key = insight_key
        self.listing_id = listing_id


async def resolve_context_group_for_insight(
    db: AsyncSession,
    *,
    user_id: str,
    insight_key: Optional[str],
    marketplace: Optional[str] = None,
    sku: Optional[str] = None,
    listing_id: Optional[str] = None,
) -> str:
    """
    Read-side context_group for an insight, using the SAME enrichment logic as
    the Memory write path (record_decision_memory): it runs the very same
    _resolve_marketplace / _resolve_context / build_context_group helpers via a
    lightweight shim. Given the same listing/product/finance data, the result
    EQUALS the write-side context_group. Missing segments degrade to "unknown".
    Read-only — no writes, no execution.

    Resolution: marketplace from the listing (else the `marketplace` hint);
    category/price_band from the listing's legacy Product; margin_band from
    imported finance keyed by sku (from insight_key, else the `sku` hint, used
    only when the key carried none). Thresholds are never duplicated here.
    """
    shim = _InsightContextShim(user_id, insight_key, listing_id)
    mp = await _resolve_marketplace(db, shim)
    if mp == _UNKNOWN and marketplace:
        mp = _norm_mp(marketplace) or _UNKNOWN
    category, p_band, m_band = await _resolve_context(db, shim, mp)
    # sku hint only fills a gap when the insight_key carried no sku of its own.
    if m_band == _UNKNOWN and sku and not _sku_from_key(insight_key):
        m_band = margin_band(await _compute_margin_pct(db, user_id, mp, sku))
    return build_context_group(
        marketplace=mp, category=category, price_band=p_band, margin_band=m_band)


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
    # L3: enrich context from real domain data (category/price via listing's legacy
    # Product; margin from imported finance). Missing segments → "unknown".
    category, p_band, m_band = await _resolve_context(db, decision, marketplace)
    context_group = build_context_group(
        marketplace=marketplace,
        category=category,
        price_band=p_band,
        margin_band=m_band,
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
