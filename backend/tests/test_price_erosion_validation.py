"""
Price Erosion / Discount Creep Diagnosis — SHADOW VALIDATION DEEPENING (Phase 8.2), read-only.

Raises confidence in the DISABLED price_erosion producer to the Review Velocity 6.2 / Overstock
7.2 level. Validation tests ONLY — no production code touched, producer stays enabled=False, not
in the Decision Feed. Complements test_price_erosion_shadow (8.1 band matrix + basic absence +
lifecycle) with the harder cases:
  * multi-SKU isolation and two-marketplace independence
  * latest CONFIRMED price vs baseline across dated snapshots
  * dated ordering by created_at — NOT insert order
  * relative-drop float boundaries around 5% / 15% / 30%
  * a broader honest-absence matrix (thin/flat/rising/sub-band/non-positive-baseline/single-dip)
  * evidence_hash determinism (same evidence → same hash; different → different)
  * lifecycle reconcile (in-place update on evidence change; dismissed changed-evidence reopens)
  * Pricing INDEPENDENCE even when pricing data is present (no price-write)

Observed-only: relative_drop = (baseline − latest) / baseline. No forecast, no benchmark, no
competitor, no price-write.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.price_erosion_signal import PriceErosionSignal
from models.pricing_signal import PricingSignal

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.price_erosion.diagnosis_source import (
    classify_price_erosion, MIN_SNAPSHOTS, LOW_DROP, MED_DROP, SEVERE_DROP)
from services.price_erosion.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _price(db, uid, day, price, *, marketplace="wildberries", sku="SKU1"):
    db.add(ImportedProductRow(import_id=f"p{marketplace}{sku}{day}", user_id=uid,
                              marketplace=marketplace, sku=sku, price=price,
                              created_at=datetime(2026, 6, day)))


async def _candidacy(db, uid, *, marketplace="wildberries", sku="SKU1"):
    db.add(ImportedFinanceRow(import_id=f"f{marketplace}{sku}", user_id=uid, marketplace=marketplace,
                              sku=sku, date="2026-06-01", quantity=1, revenue=100.0, net_profit=0.0))


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="price_erosion", now=NOW)


async def _live(db, uid, *, marketplace=None, sku=None):
    q = select(PriceErosionSignal).where(
        PriceErosionSignal.user_id == uid,
        PriceErosionSignal.status.in_(("active", "reopened")))
    if marketplace is not None:
        q = q.where(PriceErosionSignal.marketplace == marketplace)
    if sku is not None:
        q = q.where(PriceErosionSignal.sku == sku)
    return (await db.execute(q)).scalars().all()


# ── multi-SKU isolation ──────────────────────────────────────────────────────

def test_multi_sku_isolation():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # SKU_A erodes (100→65, drop 0.35); SKU_B healthy (100→100 flat)
        await _candidacy(db, uid, sku="A"); await _candidacy(db, uid, sku="B")
        for day, p in [(1, 100), (10, 80), (20, 65)]:
            await _price(db, uid, day, p, sku="A")
        for day, p in [(1, 100), (10, 100), (20, 100)]:
            await _price(db, uid, day, p, sku="B")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid, sku="A")) == 1
        assert await _live(db, uid, sku="B") == []
    _run(go())


# ── two-marketplace independence ─────────────────────────────────────────────

def test_two_marketplaces_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        for mp in ("wildberries", "ozon"):
            await _candidacy(db, uid, marketplace=mp)
            for day, p in [(1, 100), (10, 80), (20, 65)]:
                await _price(db, uid, day, p, marketplace=mp)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        wb = await _live(db, uid, marketplace="wildberries")
        oz = await _live(db, uid, marketplace="ozon")
        assert len(wb) == 1 and len(oz) == 1
        assert wb[0].insight_key == "price_erosion_discount_creep:wildberries:SKU1"
        assert oz[0].insight_key == "price_erosion_discount_creep:ozon:SKU1"
    _run(go())


# ── latest confirmed price vs baseline (pure classify) ───────────────────────

def test_latest_confirmed_vs_baseline():
    diag = classify_price_erosion([200.0, 170.0, 140.0], marketplace="wb", sku="S")
    assert diag is not None
    assert diag.baseline_price == 200.0            # oldest
    assert diag.latest_price == 140.0              # newest
    assert diag.prev_price == 170.0                # second-newest (confirms)
    assert diag.relative_drop == (200.0 - 140.0) / 200.0


# ── dated ordering, not insert order (end-to-end) ────────────────────────────

def test_dated_ordering_not_insert_order():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _candidacy(db, uid)
        # insert scrambled; created_at defines chronology → [100(d1), 80(d10), 65(d20)]
        await _price(db, uid, 20, 65)
        await _price(db, uid, 1, 100)
        await _price(db, uid, 10, 80)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].priority_level == "high"   # drop 0.35 confirmed
    _run(go())


# ── relative-drop float boundaries (pure classify) ───────────────────────────

def test_relative_drop_float_boundaries():
    def drop(latest):  # baseline 100, prev below baseline so confirmation holds
        return classify_price_erosion([100.0, 95.0, float(latest)], marketplace="wb", sku="S")
    assert drop(70).priority_level == "high"       # drop 0.30 (>= 30%)
    assert drop(71).priority_level == "medium"     # drop 0.29 [15,30)
    assert drop(85).priority_level == "medium"     # drop 0.15 (>= 15%)
    assert drop(86).priority_level == "low"        # drop 0.14 [5,15)
    assert drop(95).priority_level == "low"        # drop 0.05 (>= 5%)
    assert drop(96) is None                        # drop 0.04 (< 5%) → absence
    assert (LOW_DROP, MED_DROP, SEVERE_DROP) == (0.05, 0.15, 0.30)


# ── honest-absence matrix (pure classify) ────────────────────────────────────

def test_absence_matrix():
    # too few snapshots
    assert classify_price_erosion([100.0, 80.0], marketplace="w", sku="s") is None
    # flat
    assert classify_price_erosion([100.0, 100.0, 100.0], marketplace="w", sku="s") is None
    # rising
    assert classify_price_erosion([90.0, 95.0, 100.0], marketplace="w", sku="s") is None
    # sub-band decline
    assert classify_price_erosion([100.0, 99.0, 97.0], marketplace="w", sku="s") is None
    # non-positive baseline
    assert classify_price_erosion([0.0, 50.0, 40.0], marketplace="w", sku="s") is None
    assert classify_price_erosion([-10.0, 50.0, 40.0], marketplace="w", sku="s") is None
    # single-dip noise: prev still at baseline → not confirmed
    assert classify_price_erosion([100.0, 100.0, 70.0], marketplace="w", sku="s") is None
    assert MIN_SNAPSHOTS == 3


# ── evidence_hash determinism ────────────────────────────────────────────────

def test_evidence_hash_same_and_different():
    d1 = classify_price_erosion([100.0, 80.0, 65.0], marketplace="wb", sku="S")
    d2 = classify_price_erosion([100.0, 80.0, 65.0], marketplace="wb", sku="S")
    assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)
    d3 = classify_price_erosion([100.0, 85.0, 75.0], marketplace="wb", sku="S")   # different drop
    assert evidence_hash(d1.evidence) != evidence_hash(d3.evidence)


# ── lifecycle reconcile ──────────────────────────────────────────────────────

def test_evidence_change_updates_in_place():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _candidacy(db, uid)
        for day, p in [(1, 100), (10, 80), (20, 65)]:
            await _price(db, uid, day, p)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        first = (await _live(db, uid))[0]
        first_hash = first.evidence_hash
        # a newer, lower price snapshot changes the evidence (same insight_key, still eroding)
        await _price(db, uid, 25, 55); await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1                                # no duplicate
        assert live[0].evidence_hash != first_hash           # updated in place
    _run(go())


def test_dismissed_changed_evidence_reopens():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _candidacy(db, uid)
        for day, p in [(1, 100), (10, 80), (20, 65)]:
            await _price(db, uid, day, p)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; sig.evidence_hash = "stale"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


# ── Pricing INDEPENDENCE even when pricing data is present (no price-write) ───

def test_pricing_independent_no_price_write():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _candidacy(db, uid)
        for day, p in [(1, 100), (10, 80), (20, 65)]:
            await _price(db, uid, day, p)
        await db.commit()
        await _diagnose(db, uid); await db.commit()          # run ONLY price_erosion
        assert len(await _live(db, uid)) == 1
        # never writes the executable Pricing contour's table (no price-write / no pricing signal)
        assert (await db.execute(select(PricingSignal))).scalars().all() == []
    _run(go())


# ── disabled / not in feed (reaffirm) ────────────────────────────────────────

def test_disabled_and_not_in_feed():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["price_erosion"].enabled is False
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "price_erosion_signal" not in tables
    assert "pricing_signal" in tables                        # executable Pricing independent
