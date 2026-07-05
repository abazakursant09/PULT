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

from services.advertising.internal_source import build_snapshot_from_finance as build_advertising_snapshot
from services.advertising.snapshot import AdvertisingSnapshot, AdvertisingDataUnavailable
from services.advertising.audit_persist import audit_and_persist as advertising_audit_and_persist
from services.advertising.threshold_source import derive_advertising_thresholds

from services.pricing.generator import generate_pricing_signals
from services.pricing.threshold_source import derive_pricing_thresholds

from services.legal.persist import audit_and_persist as legal_audit_and_persist
from services.legal.snapshot import LegalDataUnavailable

from models.review_response import ReviewResponse
from services.review.internal_source import build_snapshot_from_reviews
from services.review.snapshot import ReviewSnapshot, ReviewDataUnavailable
from services.review.audit_persist import audit_and_persist as review_audit_and_persist

from models.imported_product import ImportedProductRow
from services.operations.low_stock_source import build_low_stock_signal, LOW_STOCK_UNITS

from models.revenue_audit import RevenueAudit
from services.revenue.diagnosis_source import build_revenue_series, classify_revenue_trajectory
from services.revenue.persist import reconcile_revenue_signal

from models.money_leak_audit import MoneyLeakAudit
from services.money_leak.diagnosis_source import build_cost_series, classify_cost_drifts
from services.money_leak.persist import reconcile_money_leak_signals

from models.supply_audit import SupplyAudit
from services.supply.diagnosis_source import latest_stock, observed_velocity, classify_supply_risk
from services.supply.persist import reconcile_supply_signal

from models.rating_audit import RatingAudit
from services.rating.diagnosis_source import build_rating_series, classify_rating_decline
from services.rating.persist import reconcile_rating_signal

from models.review_velocity_audit import ReviewVelocityAudit
from services.review_velocity.diagnosis_source import (
    build_review_series, observed_sales_by_date, classify_review_velocity_stall)
from services.review_velocity.persist import reconcile_review_velocity_signal

from models.overstock_audit import OverstockAudit
from services.overstock.diagnosis_source import (
    latest_stock as overstock_latest_stock,
    observed_velocity as overstock_observed_velocity, classify_overstock)
from services.overstock.persist import reconcile_overstock_signal

from models.price_erosion_audit import PriceErosionAudit
from services.price_erosion.diagnosis_source import build_price_series, classify_price_erosion
from services.price_erosion.persist import reconcile_price_erosion_signal

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


async def run_revenue_diagnosis_producer(ctx: RuntimeContext) -> ProducerResult:
    """Revenue Diagnosis advisory producer adapter (Phase 2.1, shadow). Answers ONLY
    'WHERE is revenue disappearing?' per observed (marketplace, sku), from the seller's
    OWN ImportedFinanceRow daily revenue series (services/revenue). Flush-only; no commit.
    DB-headless — no marketplace client.

    PURE DIAGNOSIS: it emits an evidence-backed symptom (revenue_signal) only — no
    treatment, no recommended_action_key that binds an executor, no Decision, no
    EngineSignalDecisionLink, no Apply, no executor, no marketplace write. Treatment stays
    owned by the existing action contours. Confirmed trajectories only (spike-rejected,
    history-gated); honest absence emits nothing."""
    db, uid = ctx.db, ctx.user_id

    seen = emitted = absent = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        daily = await build_revenue_series(db, uid, marketplace, sku)
        diag = classify_revenue_trajectory(daily, marketplace=marketplace, sku=sku)

        audit = RevenueAudit(
            user_id=uid, listing_id=listing_id, marketplace=marketplace, sku=sku,
            source="finance", status="completed",
            total_problems=1 if diag is not None else 0,
            total_not_evaluated=0 if diag is not None else 1,
            top_severity=diag.priority_level if diag is not None else None,
            triggered_by=ctx.triggered_by, created_at=ctx.now, completed_at=ctx.now)
        db.add(audit)
        await db.flush()

        rec = await reconcile_revenue_signal(
            db, user_id=uid, listing_id=listing_id, audit_id=audit.id,
            marketplace=marketplace, sku=sku, diagnosis=diag, now=ctx.now)
        if diag is not None:
            emitted += 1
        else:
            absent += 1
        reconciled += rec.created + rec.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "trajectories_confirmed": emitted,
        "honest_absent": absent,
        "signals_reconciled": reconciled,
    })


async def run_money_leak_producer(ctx: RuntimeContext) -> ProducerResult:
    """Money Leak Detection advisory producer adapter (Phase 3.1, shadow). Answers ONLY
    'WHY does money leak?' per observed (marketplace, sku), from the seller's OWN
    ImportedFinanceRow — confirmed upward drift of the commission and/or logistics share of
    revenue (services/money_leak). Flush-only; no commit. DB-headless — no marketplace client.

    PURE DIAGNOSIS + INDEPENDENT: emits an evidence-backed money_leak_signal only — no
    treatment, no recommended_action_key that binds an executor, no Decision, no
    EngineSignalDecisionLink, no Apply, no executor, no marketplace write. It does NOT absorb
    Pricing (price/margin state), Advertising (ad spend), Operations (stock), or Revenue
    Diagnosis (revenue trajectory) — it owns only the silent cost-share drift. Confirmed
    drifts only (spike-rejected, history-gated); honest absence emits nothing."""
    db, uid = ctx.db, ctx.user_id

    seen = emitted = absent = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        daily = await build_cost_series(db, uid, marketplace, sku)
        diagnoses = classify_cost_drifts(daily, marketplace=marketplace, sku=sku)

        audit = MoneyLeakAudit(
            user_id=uid, listing_id=listing_id, marketplace=marketplace, sku=sku,
            source="finance", status="completed",
            total_problems=len(diagnoses),
            total_not_evaluated=0 if diagnoses else 1,
            top_severity=diagnoses[0].priority_level if diagnoses else None,
            triggered_by=ctx.triggered_by, created_at=ctx.now, completed_at=ctx.now)
        db.add(audit)
        await db.flush()

        rec = await reconcile_money_leak_signals(
            db, user_id=uid, listing_id=listing_id, audit_id=audit.id,
            marketplace=marketplace, sku=sku, diagnoses=diagnoses, now=ctx.now)
        if diagnoses:
            emitted += len(diagnoses)
        else:
            absent += 1
        reconciled += rec.created + rec.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "drifts_confirmed": emitted,
        "honest_absent": absent,
        "signals_reconciled": reconciled,
    })


async def run_supply_producer(ctx: RuntimeContext) -> ProducerResult:
    """Supply / Replenishment Diagnosis advisory producer adapter (Phase 4.1, shadow).
    Diagnoses stock-out RUNWAY per observed (marketplace, sku) — observed on-hand stock ÷
    observed daily sell-through velocity (services/supply) — from the seller's OWN
    ImportedProductRow.stock + ImportedFinanceRow.quantity. Flush-only; no commit.
    DB-headless — no marketplace client. Observed present runway, not a forecast.

    PURE DIAGNOSIS + INDEPENDENT: emits an evidence-backed supply_signal only — no
    treatment, no recommended_action_key that binds an executor, no Decision, no
    EngineSignalDecisionLink, no Apply, no executor, no marketplace write. It complements
    (does NOT replace) the operations low_stock signal and does NOT reuse operations_signal,
    Revenue, or Money Leak. Honest absence (no stock / no velocity / thin history / ample
    runway) emits nothing."""
    db, uid = ctx.db, ctx.user_id

    seen = emitted = absent = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        stock = await latest_stock(db, uid, marketplace, sku)
        distinct_days, total_units = await observed_velocity(db, uid, marketplace, sku)
        diag = classify_supply_risk(stock, distinct_days, total_units,
                                    marketplace=marketplace, sku=sku)

        audit = SupplyAudit(
            user_id=uid, listing_id=listing_id, marketplace=marketplace, sku=sku,
            source="finance", status="completed",
            total_problems=1 if diag is not None else 0,
            total_not_evaluated=0 if diag is not None else 1,
            top_severity=diag.priority_level if diag is not None else None,
            triggered_by=ctx.triggered_by, created_at=ctx.now, completed_at=ctx.now)
        db.add(audit)
        await db.flush()

        rec = await reconcile_supply_signal(
            db, user_id=uid, listing_id=listing_id, audit_id=audit.id,
            marketplace=marketplace, sku=sku, diagnosis=diag, now=ctx.now)
        if diag is not None:
            emitted += 1
        else:
            absent += 1
        reconciled += rec.created + rec.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "risks_confirmed": emitted,
        "honest_absent": absent,
        "signals_reconciled": reconciled,
    })


async def run_rating_producer(ctx: RuntimeContext) -> ProducerResult:
    """Rating / Reputation Health Diagnosis advisory producer adapter (Phase 5.1, shadow).
    Diagnoses aggregate rating DECLINE per observed (marketplace, sku) — from the seller's
    OWN ImportedProductRow.rating across dated import snapshots (services/rating). Flush-only;
    no commit. DB-headless — no marketplace client. Observed present decline, not a forecast.

    PURE DIAGNOSIS + INDEPENDENT: emits an evidence-backed rating_signal only — no treatment,
    no recommended_action_key that binds an executor, no Decision, no
    EngineSignalDecisionLink, no Apply, no executor, no marketplace write. DISTINCT from the
    Review contour (individual review handling) — does NOT touch Review or reuse review_signal;
    does not absorb Revenue/MoneyLeak/Supply. Confirmed decline only (history-gated, blip-
    rejected); honest absence emits nothing."""
    db, uid = ctx.db, ctx.user_id

    seen = emitted = absent = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        series = await build_rating_series(db, uid, marketplace, sku)
        diag = classify_rating_decline(series, marketplace=marketplace, sku=sku)

        audit = RatingAudit(
            user_id=uid, listing_id=listing_id, marketplace=marketplace, sku=sku,
            source="catalog", status="completed",
            total_problems=1 if diag is not None else 0,
            total_not_evaluated=0 if diag is not None else 1,
            top_severity=diag.priority_level if diag is not None else None,
            triggered_by=ctx.triggered_by, created_at=ctx.now, completed_at=ctx.now)
        db.add(audit)
        await db.flush()

        rec = await reconcile_rating_signal(
            db, user_id=uid, listing_id=listing_id, audit_id=audit.id,
            marketplace=marketplace, sku=sku, diagnosis=diag, now=ctx.now)
        if diag is not None:
            emitted += 1
        else:
            absent += 1
        reconciled += rec.created + rec.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "declines_confirmed": emitted,
        "honest_absent": absent,
        "signals_reconciled": reconciled,
    })


async def run_review_velocity_producer(ctx: RuntimeContext) -> ProducerResult:
    """Review Acquisition / Social-Proof Velocity Diagnosis advisory producer adapter
    (Phase 6.1, shadow). Diagnoses a self-referential STALL in the seller's OWN observed
    reviews-per-unit-sold rate per (marketplace, sku) — reviews_count dated snapshots
    (ImportedProductRow) vs sales quantity (ImportedFinanceRow) — comparing the recent window
    to this product's OWN earlier window (services/review_velocity). Flush-only; no commit.
    DB-headless — no marketplace client. Observed present slowdown, not a forecast.

    PURE DIAGNOSIS + INDEPENDENT: emits an evidence-backed review_velocity_signal only — no
    treatment, no recommended_action_key that binds an executor, no Decision, no
    EngineSignalDecisionLink, no Apply, no executor, no marketplace write. DISTINCT from BOTH
    the Rating contour (does NOT touch rating_signal) and the Review contour (does NOT touch
    review_signal); does not absorb Revenue/MoneyLeak/Supply. Confirmed self-referential
    slowdown only (history-gated, sales-gated); honest absence emits nothing."""
    db, uid = ctx.db, ctx.user_id

    seen = emitted = absent = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        series = await build_review_series(db, uid, marketplace, sku)
        sales = await observed_sales_by_date(db, uid, marketplace, sku)
        diag = classify_review_velocity_stall(series, sales, marketplace=marketplace, sku=sku)

        audit = ReviewVelocityAudit(
            user_id=uid, listing_id=listing_id, marketplace=marketplace, sku=sku,
            source="catalog", status="completed",
            total_problems=1 if diag is not None else 0,
            total_not_evaluated=0 if diag is not None else 1,
            top_severity=diag.priority_level if diag is not None else None,
            triggered_by=ctx.triggered_by, created_at=ctx.now, completed_at=ctx.now)
        db.add(audit)
        await db.flush()

        rec = await reconcile_review_velocity_signal(
            db, user_id=uid, listing_id=listing_id, audit_id=audit.id,
            marketplace=marketplace, sku=sku, diagnosis=diag, now=ctx.now)
        if diag is not None:
            emitted += 1
        else:
            absent += 1
        reconciled += rec.created + rec.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "stalls_confirmed": emitted,
        "honest_absent": absent,
        "signals_reconciled": reconciled,
    })


async def run_overstock_producer(ctx: RuntimeContext) -> ProducerResult:
    """Overstock / Dead Stock Diagnosis advisory producer adapter (Phase 7.1, shadow).
    Diagnoses OVERSTOCK per observed (marketplace, sku) — latest on-hand stock
    (ImportedProductRow) vs observed sell-through velocity (ImportedFinanceRow): dead stock
    (stock present, zero observed sales) or excess stock (days-of-cover too high). The MIRROR
    of Supply (services/overstock). Flush-only; no commit. DB-headless — no marketplace client.
    Observed present overstock, not a forecast.

    PURE DIAGNOSIS + INDEPENDENT: emits an evidence-backed overstock_signal only — no treatment,
    no recommended_action_key that binds an executor, no Decision, no EngineSignalDecisionLink,
    no Apply, no executor, no marketplace write, no discount/liquidation. DISTINCT from Supply —
    does NOT reuse supply_signal; does not absorb Revenue/MoneyLeak/Pricing. Confirmed overstock
    only (stock-gated, history-gated); honest absence emits nothing."""
    db, uid = ctx.db, ctx.user_id

    seen = emitted = absent = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        stock = await overstock_latest_stock(db, uid, marketplace, sku)
        distinct_days, total_units = await overstock_observed_velocity(db, uid, marketplace, sku)
        diag = classify_overstock(stock, distinct_days, total_units,
                                  marketplace=marketplace, sku=sku)

        audit = OverstockAudit(
            user_id=uid, listing_id=listing_id, marketplace=marketplace, sku=sku,
            source="catalog", status="completed",
            total_problems=1 if diag is not None else 0,
            total_not_evaluated=0 if diag is not None else 1,
            top_severity=diag.priority_level if diag is not None else None,
            triggered_by=ctx.triggered_by, created_at=ctx.now, completed_at=ctx.now)
        db.add(audit)
        await db.flush()

        rec = await reconcile_overstock_signal(
            db, user_id=uid, listing_id=listing_id, audit_id=audit.id,
            marketplace=marketplace, sku=sku, diagnosis=diag, now=ctx.now)
        if diag is not None:
            emitted += 1
        else:
            absent += 1
        reconciled += rec.created + rec.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "overstock_confirmed": emitted,
        "honest_absent": absent,
        "signals_reconciled": reconciled,
    })


async def run_price_erosion_producer(ctx: RuntimeContext) -> ProducerResult:
    """Price Erosion / Discount Creep Diagnosis advisory producer adapter (Phase 8.1, shadow).
    Diagnoses a self-referential downward drift in the seller's OWN observed price per
    (marketplace, sku) — ImportedProductRow.price across dated snapshots (services/price_erosion):
    baseline snapshot vs latest confirmed price. Flush-only; no commit. DB-headless — no
    marketplace client. Observed present erosion, not a forecast.

    PURE DIAGNOSIS + INDEPENDENT: emits an evidence-backed price_erosion_signal only — no
    treatment, no recommended_action_key that binds an executor, no Decision, no
    EngineSignalDecisionLink, no Apply, no executor, no marketplace write, NO PRICE-WRITE.
    DISTINCT from the executable Pricing contour — does NOT touch pricing_signal; does not absorb
    Money Leak / Revenue / Overstock. Confirmed self-referential drift only (history-gated,
    dip-rejected); honest absence emits nothing."""
    db, uid = ctx.db, ctx.user_id

    seen = emitted = absent = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        series = await build_price_series(db, uid, marketplace, sku)
        diag = classify_price_erosion(series, marketplace=marketplace, sku=sku)

        audit = PriceErosionAudit(
            user_id=uid, listing_id=listing_id, marketplace=marketplace, sku=sku,
            source="catalog", status="completed",
            total_problems=1 if diag is not None else 0,
            total_not_evaluated=0 if diag is not None else 1,
            top_severity=diag.priority_level if diag is not None else None,
            triggered_by=ctx.triggered_by, created_at=ctx.now, completed_at=ctx.now)
        db.add(audit)
        await db.flush()

        rec = await reconcile_price_erosion_signal(
            db, user_id=uid, listing_id=listing_id, audit_id=audit.id,
            marketplace=marketplace, sku=sku, diagnosis=diag, now=ctx.now)
        if diag is not None:
            emitted += 1
        else:
            absent += 1
        reconciled += rec.created + rec.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "erosions_confirmed": emitted,
        "honest_absent": absent,
        "signals_reconciled": reconciled,
    })


async def run_advertising_producer(ctx: RuntimeContext) -> ProducerResult:
    """Advertising advisory producer adapter (Phase 1.1). Builds an AdvertisingSnapshot
    per observed (marketplace, sku) from internal PULT finance data and runs the EXISTING
    advertising audit_and_persist (evaluate → persist → reconcile) — the SAME path the
    /advertising/audit router uses. Flush-only; no commit. DB-headless (build_snapshot_
    from_finance reads ImportedFinanceRow only — no marketplace client).

    Thresholds are ADAPTER-owned (the Runtime never sees them). Since a runtime run has
    no request body, they are derived read-only from the seller's OWN observed finance
    (services/advertising/threshold_source) — the same doctrine growth uses. No ad-spend
    finance → no thresholds → the producer exits clean with NO signals (honest absence).

    Advisory-only: this writes advertising_signal rows only — no Decision, no
    EngineSignalDecisionLink, no Apply, no executor, no marketplace write. (Executable
    binding lives downstream in the click-triggered Closed Loop, untouched.)"""
    db, uid = ctx.db, ctx.user_id

    thresholds = await derive_advertising_thresholds(db, uid, now=ctx.now)
    if thresholds is None:
        # honest absence — nothing to anchor to, emit nothing
        return ProducerResult(ok=True, stats={
            "candidates_seen": 0, "snapshots_built": 0, "unavailable": 0,
            "audits_created": 0, "signals_reconciled": 0, "thresholds": "unavailable"})

    seen = built = unavailable = audits = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        snap = await build_advertising_snapshot(
            db, user_id=uid, marketplace=marketplace, sku=sku,
            listing_id=listing_id, thresholds=thresholds)
        if isinstance(snap, AdvertisingDataUnavailable):
            unavailable += 1
            continue
        assert isinstance(snap, AdvertisingSnapshot)
        built += 1
        res = await advertising_audit_and_persist(
            db, user_id=uid, snapshot=snap, triggered_by=ctx.triggered_by, now=ctx.now)
        audits += 1
        if res.reconciliation is not None:
            reconciled += res.reconciliation.created + res.reconciliation.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "snapshots_built": built,
        "unavailable": unavailable,
        "audits_created": audits,
        "signals_reconciled": reconciled,
    })


async def run_pricing_producer(ctx: RuntimeContext) -> ProducerResult:
    """Pricing advisory producer adapter (Phase 1.3, shadow). Runs the EXISTING pricing
    generator (build_pricing_snapshot → rules → reconcile) per observed (marketplace, sku)
    from internal PULT finance data. Flush-only; no commit. DB-headless — no marketplace
    client.

    Thresholds are ADAPTER-owned: derived read-only from the seller's OWN observed finance
    (services/pricing/threshold_source). min_revenue is a median floor; target_margin is
    left None (no canonical source → margin_below_target stays NOT_EVALUATED). No finance →
    no thresholds → the producer exits clean with NO signals (honest absence).

    Advisory-only: writes pricing_signal rows only — no Decision, no
    EngineSignalDecisionLink, no Apply, no executor, no marketplace write."""
    db, uid = ctx.db, ctx.user_id

    thresholds = await derive_pricing_thresholds(db, uid, now=ctx.now)
    if thresholds is None:
        return ProducerResult(ok=True, stats={
            "candidates_seen": 0, "evaluated": 0, "signals_reconciled": 0,
            "thresholds": "unavailable"})

    seen = evaluated = reconciled = 0
    for marketplace, sku in await _candidate_pairs(db, uid):
        seen += 1
        listing_id = await _resolve_listing_id(db, uid, marketplace, sku)
        res = await generate_pricing_signals(
            db, user_id=uid, marketplace=marketplace, sku=sku,
            thresholds=thresholds, listing_id=listing_id, now=ctx.now)
        if res.evaluated:
            evaluated += 1
            if res.reconciliation is not None:
                reconciled += res.reconciliation.created + res.reconciliation.updated

    return ProducerResult(ok=True, stats={
        "candidates_seen": seen,
        "evaluated": evaluated,
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
