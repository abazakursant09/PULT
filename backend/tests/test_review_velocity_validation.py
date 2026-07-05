"""
Review Acquisition / Social-Proof Velocity Diagnosis — SHADOW VALIDATION DEEPENING
(Phase 6.2), read-only.

Raises confidence in the DISABLED review_velocity producer to the Rating 5.2 / Supply 4.2
level. Validation tests ONLY — no production code touched, producer stays enabled=False, not
in the Decision Feed. Complements test_review_velocity_shadow (6.1 band matrix + basic
absence + lifecycle) with the harder cases:
  * multi-SKU isolation and two-marketplace independence
  * window-alignment edge cases (boundary dates, out-of-window sales)
  * DATED ordering (by created_at) — NOT insert order
  * boundary float behavior around the 0.30 band
  * a broader honest-absence matrix
  * evidence_hash determinism (same evidence → same hash; different → different)
  * lifecycle reconcile (in-place update on evidence change; dismissed changed-evidence reopens)
  * Rating and Review INDEPENDENCE even when their data is present

The metric stays self-referential: relative_drop = (earlier_rate − recent_rate) / earlier_rate,
each window's rate = reviews_gained / units_sold. No absolute floor / benchmark / competitor.
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
from models.review_velocity_signal import ReviewVelocitySignal
from models.rating_signal import RatingSignal
from models.review_signal import ReviewSignal

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.review_velocity.diagnosis_source import (
    classify_review_velocity_stall, MIN_SNAPSHOTS, MIN_WINDOW_UNITS)
from services.review_velocity.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _snap(db, uid, day, reviews, *, marketplace="wildberries", sku="SKU1"):
    db.add(ImportedProductRow(import_id=f"p{marketplace}{sku}{day}", user_id=uid,
                              marketplace=marketplace, sku=sku, reviews_count=reviews,
                              created_at=datetime(2026, 6, day)))


async def _sale(db, uid, day, qty, *, marketplace="wildberries", sku="SKU1"):
    db.add(ImportedFinanceRow(import_id=f"f{marketplace}{sku}{day}", user_id=uid,
                              marketplace=marketplace, sku=sku, date=f"2026-06-{day:02d}",
                              quantity=qty, revenue=100.0, net_profit=0.0))


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="review_velocity", now=NOW)


async def _live(db, uid, *, marketplace=None, sku=None):
    q = select(ReviewVelocitySignal).where(
        ReviewVelocitySignal.user_id == uid,
        ReviewVelocitySignal.status.in_(("active", "reopened")))
    if marketplace is not None:
        q = q.where(ReviewVelocitySignal.marketplace == marketplace)
    if sku is not None:
        q = q.where(ReviewVelocitySignal.sku == sku)
    return (await db.execute(q)).scalars().all()


# reusable series/sales: earlier_rate 1.0, recent_rate 0.2 → drop 0.8 → high
def _stall_series():
    return [("2026-06-01", 100), ("2026-06-10", 110), ("2026-06-20", 112)]


def _stall_sales():
    return {"2026-06-05": 10, "2026-06-15": 10}


# ── multi-SKU isolation ──────────────────────────────────────────────────────

def test_multi_sku_only_stalling_emits():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # SKU_A stalls (earlier 1.0 → recent 0.2); SKU_B healthy (earlier 0.5 → recent 1.0)
        for day, rv in [(1, 100), (10, 110), (20, 112)]:
            await _snap(db, uid, day, rv, sku="A")
        for day, q in [(5, 10), (15, 10)]:
            await _sale(db, uid, day, q, sku="A")
        for day, rv in [(1, 100), (10, 105), (20, 115)]:
            await _snap(db, uid, day, rv, sku="B")
        for day, q in [(5, 10), (15, 10)]:
            await _sale(db, uid, day, q, sku="B")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid, sku="A")) == 1        # stalling SKU emits
        assert await _live(db, uid, sku="B") == []            # healthy SKU silent
    _run(go())


# ── two-marketplace independence ─────────────────────────────────────────────

def test_two_marketplaces_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        for mp in ("wildberries", "ozon"):
            for day, rv in [(1, 100), (10, 110), (20, 112)]:
                await _snap(db, uid, day, rv, marketplace=mp)
            for day, q in [(5, 10), (15, 10)]:
                await _sale(db, uid, day, q, marketplace=mp)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        wb = await _live(db, uid, marketplace="wildberries")
        oz = await _live(db, uid, marketplace="ozon")
        assert len(wb) == 1 and len(oz) == 1
        assert wb[0].insight_key == "review_velocity_stall:wildberries:SKU1"
        assert oz[0].insight_key == "review_velocity_stall:ozon:SKU1"
    _run(go())


# ── DATED ordering, not insert order ─────────────────────────────────────────

def test_dated_ordering_not_insert_order():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # insert scrambled; created_at defines chronology (day20 first, day1 last inserted)
        await _snap(db, uid, 20, 112)
        await _snap(db, uid, 1, 100)
        await _snap(db, uid, 10, 110)
        for day, q in [(15, 10), (5, 10)]:
            await _sale(db, uid, day, q)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1
        # baseline=100(day1), mid=110(day10), latest=112(day20) → earlier 1.0, recent 0.2 → high
        assert live[0].priority_level == "high"
    _run(go())


# ── window-alignment edge cases (pure classify) ──────────────────────────────

def test_sale_on_mid_date_counts_to_recent_not_earlier():
    # H1 = [first, mid); H2 = [mid, last]. A sale exactly on mid_date belongs to H2.
    series = [("2026-06-01", 100), ("2026-06-10", 110), ("2026-06-20", 112)]
    # earlier window has only the day-05 sale; the day-10 (mid) sale is recent
    sales = {"2026-06-05": 10, "2026-06-10": 5, "2026-06-15": 5}
    diag = classify_review_velocity_stall(series, sales, marketplace="wb", sku="S")
    assert diag is not None
    assert diag.earlier_units_sold == 10                      # only day-05
    assert diag.recent_units_sold == 10                       # day-10 (mid) + day-15


def test_sale_on_last_date_included_in_recent():
    series = [("2026-06-01", 100), ("2026-06-10", 110), ("2026-06-20", 112)]
    sales = {"2026-06-05": 10, "2026-06-20": 10}               # recent sale on the last date
    diag = classify_review_velocity_stall(series, sales, marketplace="wb", sku="S")
    assert diag is not None and diag.recent_units_sold == 10   # include_end on H2


def test_sale_before_first_snapshot_excluded():
    series = [("2026-06-05", 100), ("2026-06-10", 110), ("2026-06-20", 112)]
    sales = {"2026-06-01": 999, "2026-06-07": 10, "2026-06-15": 10}   # day-01 is before first
    diag = classify_review_velocity_stall(series, sales, marketplace="wb", sku="S")
    assert diag is not None and diag.earlier_units_sold == 10  # 999 on day-01 excluded


# ── boundary float behavior around the 0.30 band ─────────────────────────────

def test_float_at_030_boundary_lands_low():
    # earlier_rate 1.0 (100/100); recent_rate 0.70 (70/100) → drop exactly 0.30 → low (>=),
    # confirming IEEE-754 1.0−0.70 does not fall under the band.
    series = [("2026-06-01", 100), ("2026-06-10", 200), ("2026-06-20", 270)]  # recent gained 70
    sales = {"2026-06-05": 100, "2026-06-15": 100}
    diag = classify_review_velocity_stall(series, sales, marketplace="wb", sku="S")
    assert diag is not None and diag.priority_level == "low"


def test_float_just_below_030_is_absence():
    # recent_rate 0.71 (71/100) → drop 0.29 (< 0.30) → nothing
    series = [("2026-06-01", 100), ("2026-06-10", 200), ("2026-06-20", 271)]  # recent gained 71
    sales = {"2026-06-05": 100, "2026-06-15": 100}
    assert classify_review_velocity_stall(series, sales, marketplace="wb", sku="S") is None


# ── honest-absence matrix (pure) ─────────────────────────────────────────────

def test_absence_matrix():
    S = _stall_series
    # (series, sales, reason)
    assert classify_review_velocity_stall(S()[:2], _stall_sales(), marketplace="w", sku="s") is None  # thin
    assert classify_review_velocity_stall(  # earlier window under MIN_WINDOW_UNITS
        S(), {"2026-06-05": MIN_WINDOW_UNITS - 1, "2026-06-15": 10}, marketplace="w", sku="s") is None
    assert classify_review_velocity_stall(  # recent window under MIN_WINDOW_UNITS
        S(), {"2026-06-05": 10, "2026-06-15": MIN_WINDOW_UNITS - 1}, marketplace="w", sku="s") is None
    assert classify_review_velocity_stall(  # no sales in earlier window at all
        S(), {"2026-06-15": 10}, marketplace="w", sku="s") is None
    assert classify_review_velocity_stall(  # earlier_rate 0 (no reviews gained earlier)
        [("2026-06-01", 100), ("2026-06-10", 100), ("2026-06-20", 112)], _stall_sales(),
        marketplace="w", sku="s") is None
    assert classify_review_velocity_stall(  # accelerating (recent_rate > earlier_rate)
        [("2026-06-01", 100), ("2026-06-10", 105), ("2026-06-20", 130)], _stall_sales(),
        marketplace="w", sku="s") is None
    assert classify_review_velocity_stall(  # reviews decrease (bad data)
        [("2026-06-01", 100), ("2026-06-10", 110), ("2026-06-20", 105)], _stall_sales(),
        marketplace="w", sku="s") is None
    assert MIN_SNAPSHOTS == 3               # documents the thin-history gate


# ── evidence_hash determinism ────────────────────────────────────────────────

def test_evidence_hash_same_and_different():
    d1 = classify_review_velocity_stall(_stall_series(), _stall_sales(), marketplace="wb", sku="S")
    d2 = classify_review_velocity_stall(_stall_series(), _stall_sales(), marketplace="wb", sku="S")
    assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)     # deterministic
    d3 = classify_review_velocity_stall(  # different recent_rate → different evidence
        [("2026-06-01", 100), ("2026-06-10", 110), ("2026-06-20", 114)], _stall_sales(),
        marketplace="wb", sku="S")
    assert evidence_hash(d1.evidence) != evidence_hash(d3.evidence)


# ── lifecycle reconcile: in-place update on evidence change ───────────────────

def test_evidence_change_updates_in_place():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        for day, rv in [(1, 100), (10, 110), (20, 112)]:
            await _snap(db, uid, day, rv)
        for day, q in [(5, 10), (15, 10)]:
            await _sale(db, uid, day, q)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        first = (await _live(db, uid))[0]
        first_hash = first.evidence_hash
        # add a later snapshot that changes the recent window → new evidence, same insight_key
        await _snap(db, uid, 25, 113); await _sale(db, uid, 22, 10)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1                                 # still one live signal (no dup)
        assert live[0].evidence_hash != first_hash            # updated in place
    _run(go())


def test_dismissed_changed_evidence_reopens():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        for day, rv in [(1, 100), (10, 110), (20, 112)]:
            await _snap(db, uid, day, rv)
        for day, q in [(5, 10), (15, 10)]:
            await _sale(db, uid, day, q)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; sig.evidence_hash = "stale"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"    # changed evidence reopens
    _run(go())


# ── Rating and Review INDEPENDENCE even when their data is present ────────────

def test_independent_when_rating_and_reviews_present():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # snapshots carry BOTH a declining rating and monotonic reviews_count
        db.add(ImportedProductRow(import_id="r1", user_id=uid, marketplace="wildberries",
                                  sku="SKU1", rating=4.8, reviews_count=100,
                                  created_at=datetime(2026, 6, 1)))
        db.add(ImportedProductRow(import_id="r2", user_id=uid, marketplace="wildberries",
                                  sku="SKU1", rating=4.5, reviews_count=110,
                                  created_at=datetime(2026, 6, 10)))
        db.add(ImportedProductRow(import_id="r3", user_id=uid, marketplace="wildberries",
                                  sku="SKU1", rating=4.3, reviews_count=112,
                                  created_at=datetime(2026, 6, 20)))
        for day, q in [(5, 10), (15, 10)]:
            await _sale(db, uid, day, q)
        await db.commit()
        await _diagnose(db, uid); await db.commit()            # run ONLY review_velocity
        assert len(await _live(db, uid)) == 1                  # our signal emitted
        # sibling contours never written by this producer
        assert (await db.execute(select(RatingSignal))).scalars().all() == []
        assert (await db.execute(select(ReviewSignal))).scalars().all() == []
    _run(go())


# ── disabled but feed reads it (reader wired 6.3a; producer still disabled) ───

def test_disabled_but_feed_reads_it():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["review_velocity"].enabled is False               # producer still disabled
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "review_velocity_signal" in tables                        # reader wired (6.3a), INERT
    assert "rating_signal" in tables and "review_signal" in tables    # siblings independent
