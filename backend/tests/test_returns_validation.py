"""
Returns Diagnosis — SHADOW VALIDATION DEEPENING (Phase R2), read-only.

Raises confidence in the DISABLED returns producer to the Review Velocity 6.2 / Overstock 7.2
level. Validation tests ONLY — no production code touched, producer stays enabled=False, not in
the Decision Feed. Complements test_returns_shadow (R1b band matrix + basic absence + lifecycle)
with the harder cases:
  * multi-SKU isolation and two-marketplace independence
  * window alignment — a boundary (mid-date) return/sale belongs to the RECENT window
  * dated ordering by observed sale/return dates, NOT insert order
  * relative-rise float boundaries around 0.25 / 0.5 / 1.0
  * a broader honest-absence matrix (no returns, no sales, insufficient earlier/recent volume,
    earlier_rate 0, recent_rate 0, too few observed days)
  * evidence_hash determinism (same evidence → same hash; different → different)
  * lifecycle reconcile (in-place update on evidence change; one live per insight_key)
  * DOUBLE-COUNT GUARD reaffirmed — evidence is return-frequency only, no net_profit /
    return_amount / money-loss

Observed-only: return_rate = returns_qty / units_sold per window; relative_rise = (recent −
earlier) / earlier. No forecast, no benchmark, no competitor, no marketplace API.
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
from models.imported_return import ImportedReturnRow
from models.returns_signal import ReturnsSignal

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.returns.diagnosis_source import (
    classify_returns_rise, MIN_OBSERVED_DAYS, MIN_WINDOW_UNITS, LOW_RISE, MED_RISE, SEVERE_RISE)
from services.returns.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _sale(db, uid, day, units, *, marketplace="wildberries", sku="SKU1"):
    db.add(ImportedFinanceRow(import_id=f"f{marketplace}{sku}{day}", user_id=uid,
                              marketplace=marketplace, sku=sku, date=f"2026-06-{day:02d}",
                              quantity=units, revenue=100.0, net_profit=50.0))


async def _ret(db, uid, day, qty, *, marketplace="wildberries", sku="SKU1"):
    db.add(ImportedReturnRow(import_id=f"r{marketplace}{sku}{day}", user_id=uid,
                             marketplace=marketplace, sku=sku, date=f"2026-06-{day:02d}",
                             returns_qty=qty, return_amount=999.0, reason="x"))


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="returns", now=NOW)


async def _live(db, uid, *, marketplace=None, sku=None):
    q = select(ReturnsSignal).where(
        ReturnsSignal.user_id == uid,
        ReturnsSignal.status.in_(("active", "reopened")))
    if marketplace is not None:
        q = q.where(ReturnsSignal.marketplace == marketplace)
    if sku is not None:
        q = q.where(ReturnsSignal.sku == sku)
    return (await db.execute(q)).scalars().all()


# symmetric 20/20 window fixture (0.05 grid): sales days 1,2 (earlier=20) + 10,20 (recent=20)
def _sales_dict():
    return {"2026-06-01": 10, "2026-06-02": 10, "2026-06-10": 10, "2026-06-20": 10}


async def _seed_wb_high(db, uid, *, marketplace="wildberries", sku="SKU1"):
    # earlier 0.10 (2/20), recent 0.30 (6/20) → rise 2.0 → high
    for day, u in [(1, 10), (2, 10), (10, 10), (20, 10)]:
        await _sale(db, uid, day, u, marketplace=marketplace, sku=sku)
    await _ret(db, uid, 5, 2, marketplace=marketplace, sku=sku)
    await _ret(db, uid, 15, 6, marketplace=marketplace, sku=sku)


# ── multi-SKU isolation ──────────────────────────────────────────────────────

def test_multi_sku_isolation():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_wb_high(db, uid, sku="A")                       # rises
        for day, u in [(1, 10), (2, 10), (10, 10), (20, 10)]:       # SKU_B: flat return rate
            await _sale(db, uid, day, u, sku="B")
        await _ret(db, uid, 5, 4, sku="B"); await _ret(db, uid, 15, 4, sku="B")  # 0.2 → 0.2 flat
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid, sku="A")) == 1
        assert await _live(db, uid, sku="B") == []
    _run(go())


# ── two-marketplace independence ─────────────────────────────────────────────

def test_two_marketplaces_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_wb_high(db, uid, marketplace="wildberries")
        await _seed_wb_high(db, uid, marketplace="ozon")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        wb = await _live(db, uid, marketplace="wildberries")
        oz = await _live(db, uid, marketplace="ozon")
        assert len(wb) == 1 and len(oz) == 1
        assert wb[0].insight_key == "returns_return_rate_rise:wildberries:SKU1"
        assert oz[0].insight_key == "returns_return_rate_rise:ozon:SKU1"
    _run(go())


# ── window alignment: a return/sale on the mid-date belongs to the RECENT window ─

def test_mid_date_belongs_to_recent_window():
    # sale dates [01, 10, 20] → mid_date = 10. A return on 06-10 must count in RECENT, not earlier.
    sales = {"2026-06-01": 10, "2026-06-10": 10, "2026-06-20": 10}
    # earlier window [01,10): units = day01 = 10 ; recent [10,20]: units = day10+day20 = 20
    returns = {"2026-06-05": 1, "2026-06-10": 6}   # day05 earlier(1), day10 (mid) recent(6) → rise 2.0
    d = classify_returns_rise(sales, returns, marketplace="wb", sku="S")
    assert d is not None
    assert d.earlier_returns == 1 and d.recent_returns == 6   # mid-date return went to recent
    assert d.earlier_window_end == "2026-06-10" and d.recent_window_start == "2026-06-10"


def test_return_after_last_sale_date_excluded():
    sales = {"2026-06-01": 10, "2026-06-10": 10, "2026-06-20": 10}
    returns = {"2026-06-05": 1, "2026-06-15": 6, "2026-06-25": 99}   # 25 is after last sale date
    d = classify_returns_rise(sales, returns, marketplace="wb", sku="S")
    assert d is not None and d.recent_returns == 6   # day-25 return excluded (out of window)


# ── dated ordering, not insert order (end-to-end) ────────────────────────────

def test_dated_ordering_not_insert_order():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # insert scrambled; observed dates define windows → high
        await _sale(db, uid, 20, 10); await _sale(db, uid, 1, 10)
        await _sale(db, uid, 2, 10); await _sale(db, uid, 10, 10)
        await _ret(db, uid, 15, 6); await _ret(db, uid, 5, 2)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].priority_level == "high"
    _run(go())


# ── relative-rise float boundaries (pure classify, symmetric 20/20 windows) ──

def test_relative_rise_float_boundaries():
    sales = {"2026-06-01": 10, "2026-06-02": 10, "2026-06-10": 10, "2026-06-20": 10}  # 20/20

    def band(er, rr):  # earlier returns, recent returns → priority or None
        d = classify_returns_rise(sales, {"2026-06-05": er, "2026-06-15": rr},
                                  marketplace="wb", sku="S")
        return d.priority_level if d else None

    assert band(2, 6) == "high"      # 0.10 → 0.30, rise 2.0
    assert band(4, 8) == "high"      # 0.20 → 0.40, rise 1.0 (>= 1.0)
    assert band(4, 7) == "medium"    # 0.20 → 0.35, rise 0.75
    assert band(8, 13) == "medium"   # 0.40 → 0.65, rise 0.625
    assert band(8, 11) == "low"      # 0.40 → 0.55, rise 0.375
    assert band(8, 9) is None        # 0.40 → 0.45, rise 0.125 (< 0.25)
    assert (LOW_RISE, MED_RISE, SEVERE_RISE) == (0.25, 0.50, 1.00)


# ── honest-absence matrix (pure classify) ────────────────────────────────────

def test_absence_matrix():
    sales = {"2026-06-01": 10, "2026-06-02": 10, "2026-06-10": 10, "2026-06-20": 10}
    # no returns data
    assert classify_returns_rise(sales, {}, marketplace="w", sku="s") is None
    # no sales data
    assert classify_returns_rise({}, {"2026-06-05": 2, "2026-06-15": 6}, marketplace="w", sku="s") is None
    # too few observed sale days
    assert classify_returns_rise({"2026-06-01": 10, "2026-06-10": 10},
                                 {"2026-06-05": 2, "2026-06-15": 6}, marketplace="w", sku="s") is None
    # insufficient earlier volume (earlier window units < MIN_WINDOW_UNITS)
    assert classify_returns_rise({"2026-06-01": 3, "2026-06-10": 10, "2026-06-20": 10},
                                 {"2026-06-05": 1, "2026-06-15": 6}, marketplace="w", sku="s") is None
    # insufficient recent volume
    assert classify_returns_rise({"2026-06-01": 10, "2026-06-02": 10, "2026-06-10": 2},
                                 {"2026-06-05": 2, "2026-06-15": 6}, marketplace="w", sku="s") is None
    # earlier_rate zero (no returns in earlier window)
    assert classify_returns_rise(sales, {"2026-06-15": 6}, marketplace="w", sku="s") is None
    # recent_rate zero (returns only earlier) → recent <= earlier → nothing
    assert classify_returns_rise(sales, {"2026-06-05": 6}, marketplace="w", sku="s") is None
    assert MIN_OBSERVED_DAYS == 3 and MIN_WINDOW_UNITS == 5


# ── evidence_hash determinism ────────────────────────────────────────────────

def test_evidence_hash_same_and_different():
    sales = _sales_dict()
    d1 = classify_returns_rise(sales, {"2026-06-05": 2, "2026-06-15": 6}, marketplace="wb", sku="S")
    d2 = classify_returns_rise(sales, {"2026-06-05": 2, "2026-06-15": 6}, marketplace="wb", sku="S")
    assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)
    d3 = classify_returns_rise(sales, {"2026-06-05": 2, "2026-06-15": 8}, marketplace="wb", sku="S")
    assert evidence_hash(d1.evidence) != evidence_hash(d3.evidence)


# ── lifecycle: in-place update, one live per insight_key ──────────────────────

def test_evidence_change_updates_in_place():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_wb_high(db, uid)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        first = (await _live(db, uid))[0]
        first_hash = first.evidence_hash
        # a heavier recent return changes the evidence (same insight_key, still rising)
        await _ret(db, uid, 16, 3); await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1                                # no duplicate
        assert live[0].evidence_hash != first_hash           # updated in place
    _run(go())


# ── double-count guard reaffirmed ────────────────────────────────────────────

def test_double_count_guard_evidence_frequency_only():
    d = classify_returns_rise(_sales_dict(), {"2026-06-05": 2, "2026-06-15": 6},
                              marketplace="wb", sku="S")
    ev = d.evidence
    for forbidden in ("net_profit", "return_amount", "money_loss", "amount", "loss", "revenue", "profit"):
        assert forbidden not in ev
    assert set(ev) >= {"earlier_returns", "recent_returns", "earlier_units", "recent_units"}


# ── disabled / not in feed (reaffirm) ────────────────────────────────────────

def test_disabled_and_not_in_feed():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["returns"].enabled is False
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "returns_signal" not in tables
