"""
Overstock / Dead Stock Diagnosis — SHADOW VALIDATION DEEPENING (Phase 7.2), read-only.

Raises confidence in the DISABLED overstock producer to the Supply 4.2 / Review Velocity 6.2
level. Validation tests ONLY — no production code touched, producer stays enabled=False, not in
the Decision Feed. Complements test_overstock_shadow (7.1 band matrix + basic absence + lifecycle)
with the harder cases:
  * multi-SKU isolation and two-marketplace independence
  * dead_stock vs excess_stock disambiguation
  * latest stock wins across dated snapshots — ordered by created_at, NOT insert order
  * days-of-cover float boundaries around 90/120/180
  * a broader honest-absence matrix (incl. healthy velocity, missing listing identity)
  * evidence_hash determinism (same evidence → same hash; different → different)
  * lifecycle reconcile (in-place update on evidence change; dismissed changed-evidence reopens)
  * Supply INDEPENDENCE even when supply data is present

Observed-only: days_of_cover = stock / (total_units / distinct_days). No forecast, no benchmark,
no competitor, no discount/liquidation.
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
from models.overstock_signal import OverstockSignal
from models.supply_signal import SupplySignal

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.overstock.diagnosis_source import (
    classify_overstock, MIN_OBSERVED_DAYS, LOW_COVER, MED_COVER, HIGH_COVER)
from services.overstock.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _stock(db, uid, day, stock, *, marketplace="wildberries", sku="SKU1"):
    db.add(ImportedProductRow(import_id=f"p{marketplace}{sku}{day}", user_id=uid,
                              marketplace=marketplace, sku=sku, stock=stock,
                              created_at=datetime(2026, 6, day)))


async def _sale(db, uid, day, qty, *, marketplace="wildberries", sku="SKU1"):
    db.add(ImportedFinanceRow(import_id=f"f{marketplace}{sku}{day}", user_id=uid,
                              marketplace=marketplace, sku=sku, date=f"2026-06-{day:02d}",
                              quantity=qty, revenue=100.0, net_profit=0.0))


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="overstock", now=NOW)


async def _live(db, uid, *, marketplace=None, sku=None):
    q = select(OverstockSignal).where(
        OverstockSignal.user_id == uid,
        OverstockSignal.status.in_(("active", "reopened")))
    if marketplace is not None:
        q = q.where(OverstockSignal.marketplace == marketplace)
    if sku is not None:
        q = q.where(OverstockSignal.sku == sku)
    return (await db.execute(q)).scalars().all()


# 3 distinct observed days, all sales on day 5 → distinct_days=3, velocity=total/3
def _sales(total):
    return [(5, total), (6, 0), (7, 0)]


# ── multi-SKU isolation ──────────────────────────────────────────────────────

def test_multi_sku_isolation():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _stock(db, uid, 20, 200, sku="A")          # cover 200 → excess high
        for d, q in _sales(3):
            await _sale(db, uid, d, q, sku="A")
        await _stock(db, uid, 20, 30, sku="B")           # cover 30 → healthy, no signal
        for d, q in _sales(3):
            await _sale(db, uid, d, q, sku="B")
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
            await _stock(db, uid, 20, 200, marketplace=mp)
            for d, q in _sales(3):
                await _sale(db, uid, d, q, marketplace=mp)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        wb = await _live(db, uid, marketplace="wildberries")
        oz = await _live(db, uid, marketplace="ozon")
        assert len(wb) == 1 and len(oz) == 1
        assert wb[0].insight_key == "overstock_excess_stock:wildberries:SKU1"
        assert oz[0].insight_key == "overstock_excess_stock:ozon:SKU1"
    _run(go())


# ── dead_stock vs excess_stock disambiguation ────────────────────────────────

def test_dead_vs_excess_disambiguation():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _stock(db, uid, 20, 100, sku="DEAD")       # zero sales → dead_stock
        for d, q in [(5, 0), (6, 0), (7, 0)]:
            await _sale(db, uid, d, q, sku="DEAD")
        await _stock(db, uid, 20, 200, sku="EXC")        # cover 200 → excess_stock
        for d, q in _sales(3):
            await _sale(db, uid, d, q, sku="EXC")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        dead = await _live(db, uid, sku="DEAD")
        exc = await _live(db, uid, sku="EXC")
        assert dead[0].problem_type == "dead_stock"
        assert dead[0].insight_key == "overstock_dead_stock:wildberries:DEAD"
        assert exc[0].problem_type == "excess_stock"
        assert exc[0].insight_key == "overstock_excess_stock:wildberries:EXC"
    _run(go())


# ── latest stock wins across dated snapshots (created_at, not insert order) ───

def test_latest_stock_wins_not_insert_order():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # insert scrambled; latest by created_at (day 25) is stock=200 → excess high.
        # an earlier snapshot (day 10) has a healthy stock=30 that must NOT be used.
        await _stock(db, uid, 10, 30)
        await _stock(db, uid, 25, 200)
        await _stock(db, uid, 18, 50)
        for d, q in _sales(3):
            await _sale(db, uid, d, q)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].priority_level == "high"   # used stock=200 (latest)
    _run(go())


# ── days-of-cover float boundaries (pure classify, velocity 1.0/day) ─────────

def test_cover_band_float_boundaries():
    def cover(stock):
        return classify_overstock(stock, 3, 3, marketplace="wb", sku="S")
    assert cover(180).priority_level == "high"      # >= 180
    assert cover(179).priority_level == "medium"    # [120,180)
    assert cover(120).priority_level == "medium"    # >= 120
    assert cover(119).priority_level == "low"       # [90,120)
    assert cover(90).priority_level == "low"        # >= 90
    assert cover(89) is None                        # < 90 → healthy, absence
    # pin the band constants themselves
    assert (LOW_COVER, MED_COVER, HIGH_COVER) == (90.0, 120.0, 180.0)


# ── honest-absence matrix ─────────────────────────────────────────────────────

def test_absence_matrix():
    # no stock snapshot
    assert classify_overstock(None, 3, 3, marketplace="w", sku="s") is None
    # zero / negative stock
    assert classify_overstock(0, 3, 3, marketplace="w", sku="s") is None
    assert classify_overstock(-5, 3, 3, marketplace="w", sku="s") is None
    # no observed sales window (0 distinct days) — cannot judge a rate
    assert classify_overstock(500, 0, 0, marketplace="w", sku="s") is None
    # insufficient observed days (< MIN_OBSERVED_DAYS)
    assert classify_overstock(500, MIN_OBSERVED_DAYS - 1, 0, marketplace="w", sku="s") is None
    # sales velocity too healthy → cover below band
    assert classify_overstock(50, 3, 3, marketplace="w", sku="s") is None   # cover 50
    assert MIN_OBSERVED_DAYS == 3


def test_missing_listing_identity_still_diagnoses():
    # listing_id is a best-effort soft ref; overstock keys on (marketplace, sku). With no
    # ProductListing to resolve, listing_id stays None but the signal still emits.
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _stock(db, uid, 20, 200)
        for d, q in _sales(3):
            await _sale(db, uid, d, q)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].listing_id is None
    _run(go())


# ── evidence_hash determinism ────────────────────────────────────────────────

def test_evidence_hash_same_and_different():
    d1 = classify_overstock(200, 3, 3, marketplace="wb", sku="S")
    d2 = classify_overstock(200, 3, 3, marketplace="wb", sku="S")
    assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)
    d3 = classify_overstock(150, 3, 3, marketplace="wb", sku="S")   # different cover
    assert evidence_hash(d1.evidence) != evidence_hash(d3.evidence)


# ── lifecycle reconcile ──────────────────────────────────────────────────────

def test_evidence_change_updates_in_place():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _stock(db, uid, 20, 200)
        for d, q in _sales(3):
            await _sale(db, uid, d, q)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        first = (await _live(db, uid))[0]
        first_hash = first.evidence_hash
        # a newer stock snapshot changes the evidence (same insight_key, still excess)
        await _stock(db, uid, 25, 500); await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1                                # no duplicate
        assert live[0].evidence_hash != first_hash           # updated in place
    _run(go())


def test_dismissed_changed_evidence_reopens():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _stock(db, uid, 20, 200)
        for d, q in _sales(3):
            await _sale(db, uid, d, q)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; sig.evidence_hash = "stale"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


# ── Supply independence even when supply data is present ──────────────────────

def test_supply_independent_with_supply_data_present():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # same snapshot could drive Supply (low stock) AND Overstock (dead) for DIFFERENT skus;
        # running ONLY overstock must never write supply_signal.
        await _stock(db, uid, 20, 100, sku="DEAD")
        for d, q in [(5, 0), (6, 0), (7, 0)]:
            await _sale(db, uid, d, q, sku="DEAD")
        await db.commit()
        await _diagnose(db, uid); await db.commit()          # run ONLY overstock
        assert len(await _live(db, uid)) == 1
        assert (await db.execute(select(SupplySignal))).scalars().all() == []
    _run(go())


# ── disabled / not in feed (reaffirm) ────────────────────────────────────────

def test_disabled_and_not_in_feed():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["overstock"].enabled is False
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "overstock_signal" not in tables
    assert "supply_signal" in tables                          # Supply independent
