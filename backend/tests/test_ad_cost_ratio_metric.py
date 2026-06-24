"""
Action Coverage Expansion A2.1 — ad_cost_ratio (ДРР) observed reader.

ad_cost_ratio = ad_spend / revenue * 100 (%), computed over ImportedFinanceRow
(no marketplace API). Observed only: MetricUnavailable when db/user_id/entity
missing, no finance rows, or revenue <= 0 — never a fabricated 0, never inferred,
never backfilled. Registered in effect_measurement._READERS with direction -1
(lower ДРР is better). Unavailable observation closes measurement as not_evaluated.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from services.marketplace.finance_metric_reader import read_ad_cost_ratio
from services.marketplace.metric_reader import MetricSample, MetricUnavailable
from services.decision_outcome import effect_measurement
from services.decision_outcome.effect_measurement import _READERS, _read_observed

NOW = datetime(2026, 6, 20)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _fin(db, uid, *, mp="wb", sku="SKU1", date="2026-06-18",
               revenue=0.0, ad_spend=0.0):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date=date, sku=sku, revenue=revenue, ad_spend=ad_spend))
    await db.flush()


# ── (1) reader registration ──────────────────────────────────────────────────

def test_reader_registered_with_lower_better_direction():
    assert "ad_cost_ratio" in _READERS
    reader, direction = _READERS["ad_cost_ratio"]
    assert reader is read_ad_cost_ratio
    assert direction == -1   # lower ДРР is better


# ── (2) successful observed read ─────────────────────────────────────────────

def test_observed_read_computes_drr_percent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, revenue=800.0, ad_spend=100.0)
        await _fin(db, uid, revenue=200.0, ad_spend=50.0)   # totals: rev 1000, ad 150
        s = await read_ad_cost_ratio(db=db, user_id=uid, marketplace="wb",
                                     entity_id="SKU1", window_days=7, now=NOW)
        assert isinstance(s, MetricSample)
        assert s.value == 15.0           # 150 / 1000 * 100
        assert s.unit == "percent"
        assert s.source == "compute"
    _run(go())


# ── (3) unavailable data → not_evaluated (no observed value) ──────────────────

def test_no_rows_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await read_ad_cost_ratio(db=db, user_id=uid, marketplace="wb",
                                     entity_id="SKU1", window_days=7, now=NOW)
        assert isinstance(r, MetricUnavailable) and r.reason == "no_finance_rows"
    _run(go())


def test_missing_scope_unavailable():
    async def go():
        # no db, no user, no entity → honest absence, never a value
        assert isinstance(await read_ad_cost_ratio(db=None, user_id="u", marketplace="wb",
                          entity_id="S", now=NOW), MetricUnavailable)
        db = await _engine()
        r1 = await read_ad_cost_ratio(db=db, user_id=None, marketplace="wb",
                                      entity_id="S", now=NOW)
        r2 = await read_ad_cost_ratio(db=db, user_id="u", marketplace="wb",
                                      entity_id=None, now=NOW)
        assert r1.reason == "no_scope" and r2.reason == "no_entity"
    _run(go())


def test_unavailable_closes_as_no_observed_value():
    # _read_observed surfaces None + reason (lifecycle closes such links not_evaluated,
    # never improved/worsened) — the reader fabricates nothing.
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())

        class _Link:  # minimal link shim for the reader seam
            user_id = uid
            marketplace = "wb"
            sku = "SKU1"

        value, reason = await _read_observed(db, _Link(), "ad_cost_ratio",
                                             window_days=7, now=NOW)
        assert value is None and reason == "no_finance_rows"
    _run(go())


# ── (4) marketplace isolation (no cross-marketplace blending) ─────────────────

def test_marketplace_isolation_no_blending():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # ozon rows must NOT leak into a wb read
        await _fin(db, uid, mp="ozon", revenue=1000.0, ad_spend=900.0)
        await _fin(db, uid, mp="wb", revenue=1000.0, ad_spend=100.0)
        s = await read_ad_cost_ratio(db=db, user_id=uid, marketplace="wb",
                                     entity_id="SKU1", window_days=7, now=NOW)
        assert isinstance(s, MetricSample) and s.value == 10.0   # wb only, ozon excluded
    _run(go())


def test_megamarket_unsupported_returns_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # megamarket has no imported finance → no rows → honest None (never inferred)
        r = await read_ad_cost_ratio(db=db, user_id=uid, marketplace="megamarket",
                                     entity_id="SKU1", window_days=7, now=NOW)
        assert isinstance(r, MetricUnavailable) and r.reason == "no_finance_rows"
    _run(go())


# ── (5) no fake fallback values ──────────────────────────────────────────────

def test_zero_revenue_unavailable_not_zero():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # ad_spend present but revenue 0 → ratio undefined → unavailable, NOT 0.0
        await _fin(db, uid, revenue=0.0, ad_spend=300.0)
        r = await read_ad_cost_ratio(db=db, user_id=uid, marketplace="wb",
                                     entity_id="SKU1", window_days=7, now=NOW)
        assert isinstance(r, MetricUnavailable) and r.reason == "no_revenue"
    _run(go())
