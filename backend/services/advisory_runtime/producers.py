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
from services.growth.threshold_source import derive_growth_thresholds

from services.legal.persist import audit_and_persist as legal_audit_and_persist
from services.legal.snapshot import LegalDataUnavailable

from models.review_response import ReviewResponse
from services.review.internal_source import build_snapshot_from_reviews
from services.review.snapshot import ReviewSnapshot, ReviewDataUnavailable
from services.review.audit_persist import audit_and_persist as review_audit_and_persist

from models.imported_product import ImportedProductRow
from services.operations.low_stock_source import build_low_stock_signal, LOW_STOCK_UNITS

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

    Thresholds are ADAPTER-owned (the Runtime never sees them). They are derived
    read-only from the seller's OWN observed finance (services/growth/threshold_source),
    so only above-typical listings surface — no permissive-default overgeneration. No
    finance data → all-None thresholds → no signals (honest)."""
    db, uid = ctx.db, ctx.user_id
    thresholds = await derive_growth_thresholds(db, uid, now=ctx.now)

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


async def _review_subjects(db: AsyncSession, user_id: str):
    """The seller's reviews. ReviewResponse has no user_id — ownership is proven via
    the linked Product (Product.user_id). Observed, DB-headless."""
    rows = (await db.execute(
        select(ReviewResponse.id, ReviewResponse.marketplace)
        .join(Product, Product.id == ReviewResponse.product_id)
        .where(Product.user_id == user_id))).all()
    return [(rid, mp) for rid, mp in rows]


async def run_review_producer(ctx: RuntimeContext) -> ProducerResult:
    """Review advisory producer adapter. Runs the EXISTING review audit_and_persist per
    owned review (snapshot from ReviewResponse / Product — no marketplace read, no
    thresholds, no auto-reply). Flush-only; no commit.

    Advisory-only: review signals never bind an executor (publish_review_response is
    PAYLOAD_NOT_DERIVABLE — reply text is human-written; negative reviews are
    MANUAL_ONLY). Reconciliation keeps one live review_signal per insight_key."""
    db, uid = ctx.db, ctx.user_id

    seen = audits = unavailable = reconciled = 0
    for review_id, marketplace in await _review_subjects(db, uid):
        seen += 1
        snap = await build_snapshot_from_reviews(
            db, review_id=review_id, marketplace=marketplace, owner_user_id=uid, now=ctx.now)
        if isinstance(snap, ReviewDataUnavailable):
            unavailable += 1
            continue
        assert isinstance(snap, ReviewSnapshot)
        res = await review_audit_and_persist(
            db, user_id=uid, snapshot=snap, triggered_by=ctx.triggered_by, now=ctx.now)
        audits += 1
        if res.reconciliation is not None:
            reconciled += res.reconciliation.created + res.reconciliation.updated

    return ProducerResult(ok=True, stats={
        "reviews_seen": seen,
        "audits_created": audits,
        "unavailable": unavailable,
        "signals_reconciled": reconciled,
    })


async def run_operations_low_stock_producer(ctx: RuntimeContext) -> ProducerResult:
    """Low-stock advisory producer adapter (NARROW operations producer). Reads observed
    stock from already-imported PULT data (ImportedProductRow) and creates one
    operations_low_stock signal per critically-low listing via the EXISTING low-stock
    source (services/operations/low_stock_source). Flush-only; no commit.

    Advisory-only: operations_low_stock binds to no executor and is not in the
    Decision-Outcome canonical set, so it creates operations_signal rows only — no
    Decision, no Apply, no executor, no marketplace write. The source is idempotent
    per insight_key, so reconciliation keeps one live signal per (marketplace, sku).
    DB-headless. Runs ALONGSIDE the legacy _compute_insights low_stock logic, never
    replacing it."""
    db, uid = ctx.db, ctx.user_id

    # bounded: pre-filter to the critically-low rows only (the source re-guards)
    rows = (await db.execute(select(ImportedProductRow).where(
        ImportedProductRow.user_id == uid,
        ImportedProductRow.stock.isnot(None),
        ImportedProductRow.stock >= 0,
        ImportedProductRow.stock <= LOW_STOCK_UNITS,
    ))).scalars().all()

    seen = signals = 0
    for row in rows:
        seen += 1
        sig = await build_low_stock_signal(
            db, user_id=uid, marketplace=row.marketplace, sku=row.sku,
            stock=row.stock, listing_id=row.product_id, now=ctx.now)
        if sig is not None:
            signals += 1

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "low_stock_signals": signals,
    })
