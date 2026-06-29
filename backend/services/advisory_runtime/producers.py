"""
Advisory Runtime producer adapters (Phase-0).

An ADAPTER is the only place that knows a contour's internals (snapshot builder,
thresholds, audit_and_persist). It exposes the single Runtime contract
`run(ctx) -> ProducerResult`. The Runtime never sees what is inside.

Adapters are FLUSH-ONLY: they never commit — the Runtime owns the transaction
(ctx.db). `stats` is opaque producer-owned data; the Runtime stores it verbatim.

This slice ships the first adapter: growth (fully DB-headless — its snapshot builder
aggregates already-imported PULT data, no marketplace client, no HTTP).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from models.product import Product
from models.product_listing import ProductListing

from services.growth.internal_source import build_snapshot_from_internal
from services.growth.snapshot import GrowthSnapshot, GrowthDataUnavailable
from services.growth.audit_persist import audit_and_persist
from services.growth.rules import GrowthThresholds

from services.legal.persist import audit_and_persist as legal_audit_and_persist
from services.legal.snapshot import LegalDataUnavailable

from .runtime import RuntimeContext, ProducerResult


async def _candidate_pairs(db: AsyncSession, user_id: str):
    """Distinct (marketplace, sku) the seller actually has finance data for — the
    growth snapshot's anchor. Observed-only, DB-headless."""
    rows = (await db.execute(
        select(ImportedFinanceRow.marketplace, ImportedFinanceRow.sku)
        .where(ImportedFinanceRow.user_id == user_id, ImportedFinanceRow.sku.isnot(None))
        .distinct())).all()
    return [(mp, sku) for mp, sku in rows]


async def _resolve_listing_id(db: AsyncSession, user_id: str, marketplace: str, sku: str):
    """Best-effort listing_id (for cross-contour SEO scope). None when not resolvable
    — the snapshot honestly marks SEO signals unavailable."""
    return (await db.execute(
        select(ProductListing.id).where(
            ProductListing.user_id == user_id,
            ProductListing.marketplace == marketplace,
            ProductListing.external_id == str(sku)).limit(1))).scalar()


async def run_growth_producer(ctx: RuntimeContext) -> ProducerResult:
    """Growth advisory producer adapter. Builds a GrowthSnapshot per observed
    (marketplace, sku) from internal PULT data and runs the EXISTING growth
    audit_and_persist (rules → persist → reconcile). Flush-only; no commit.

    Thresholds are ADAPTER-owned (the Runtime never sees them). Every growth rule is
    threshold-gated, so the adapter uses a PERMISSIVE default (no minimum filter:
    min revenue/net_profit = 0, low_stock_units = 0). That surfaces every OBSERVED
    case — it configures a filter, it never fabricates an observed fact. Per-user
    threshold tuning is a later concern."""
    db, uid = ctx.db, ctx.user_id
    thresholds = GrowthThresholds(
        min_net_profit_for_growth_signal=0.0,
        min_revenue_for_growth_signal=0.0,
        low_stock_units=0,
    )

    seen = built = unavailable = audits = problems = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        snap = await build_snapshot_from_internal(
            db, user_id=uid, marketplace=marketplace, sku=sku,
            listing_id=listing_id, now=ctx.now)
        if isinstance(snap, GrowthDataUnavailable):
            unavailable += 1
            continue
        assert isinstance(snap, GrowthSnapshot)
        built += 1
        res = await audit_and_persist(
            db, user_id=uid, snapshot=snap, thresholds=thresholds,
            triggered_by=ctx.triggered_by, now=ctx.now)
        audits += 1
        problems += res.total_problems
        if res.reconciliation is not None:
            reconciled += res.reconciliation.created + res.reconciliation.updated

    # opaque, producer-owned — Runtime stores verbatim, never reads keys
    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "snapshots_built": built,
        "unavailable": unavailable,
        "audits_created": audits,
        "problems_detected": problems,
        "signals_reconciled": reconciled,
    })


async def _legal_subjects(db: AsyncSession, user_id: str):
    """Distinct (marketplace, sku) legal subjects from the seller's catalog. Observed,
    DB-headless. Legal is per-seller-subject and advisory-only (AUTO_FORBIDDEN)."""
    rows = (await db.execute(
        select(Product.marketplace, Product.sku).where(
            Product.user_id == user_id,
            Product.marketplace.isnot(None),
            Product.sku.isnot(None)).distinct())).all()
    return [(mp, sku) for mp, sku in rows]


async def run_legal_producer(ctx: RuntimeContext) -> ProducerResult:
    """Legal advisory producer adapter. Runs the EXISTING legal audit_and_persist per
    catalog subject (builds its own snapshot internally from Product / ImportedProductRow
    — no marketplace read, no thresholds, no content generation). Flush-only; no commit.

    Legal is advisory-only and AUTO_FORBIDDEN — it can never bind an executor action.
    Reconciliation keeps one live legal_signal per insight_key."""
    db, uid = ctx.db, ctx.user_id

    seen = audits = unavailable = reconciled = 0
    for marketplace, sku in await _legal_subjects(db, uid):
        seen += 1
        res = await legal_audit_and_persist(
            db, seller_id=uid, marketplace=marketplace, subject_type="product",
            sku=sku, triggered_by=ctx.triggered_by, now=ctx.now)
        if isinstance(res, LegalDataUnavailable):
            unavailable += 1
            continue
        audits += 1
        reconciled += getattr(res, "signals_created", 0) + getattr(res, "signals_updated", 0)

    return ProducerResult(ok=True, stats={
        "subjects_seen": seen,
        "audits_created": audits,
        "unavailable": unavailable,
        "signals_reconciled": reconciled,
    })
