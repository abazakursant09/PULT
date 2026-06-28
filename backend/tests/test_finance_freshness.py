"""
Measure Quality — finance ingestion freshness (read-only observed facts).

Covers the helper finance_freshness and its read-side passthrough into the Feed's
source_context for UNMEASURED finance-backed effects. Observed facts only — no score,
no threshold, no verdict.
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
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.imported_finance import ImportedFinanceRow

from services.marketplace.finance_freshness import finance_freshness
from services.operations.signal_builder import build_operations_signal, SIGNAL_KEY
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement,
)
from services.decision_outcome.effect_summary import build_effect_summaries
from services.decision_feed.builder import build_feed

NOW = datetime(2026, 6, 21, 12, 0, 0)
SKU = "SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _fin(db, uid, *, mp="ozon", sku=SKU, date, net_profit=10.0, created_at=None):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date=date, sku=sku, revenue=100.0, net_profit=net_profit,
                              created_at=created_at or NOW))
    await db.flush()


# ── (1) helper returns observed facts ────────────────────────────────────────

def test_freshness_returns_observed_facts():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, date="2026-06-10", created_at=datetime(2026, 6, 11, 9, 0))
        await _fin(db, uid, date="2026-06-18", created_at=datetime(2026, 6, 19, 9, 0))  # in 14d window
        await db.commit()
        f = await finance_freshness(db, user_id=uid, marketplace="ozon", sku=SKU,
                                    window_days=14, now=NOW)
        assert f["last_finance_date"] == "2026-06-18"
        assert f["last_import_at"] == datetime(2026, 6, 19, 9, 0).isoformat()
        assert f["rows_in_window"] == 2            # both dates within [2026-06-07, 2026-06-21]
        assert f["window_start"] == "2026-06-07" and f["window_end"] == "2026-06-21"
    _run(go())


def test_freshness_counts_only_window_rows():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, date="2026-06-18")                 # in window
        await _fin(db, uid, date="2026-01-01")                 # outside 14d window
        await db.commit()
        f = await finance_freshness(db, user_id=uid, marketplace="ozon", sku=SKU,
                                    window_days=14, now=NOW)
        assert f["last_finance_date"] == "2026-06-18"          # MAX over ALL rows
        assert f["rows_in_window"] == 1                        # only the in-window row counted
    _run(go())


# ── (2) empty set → honest nulls + rows_in_window=0 ──────────────────────────

def test_freshness_empty_is_null():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        f = await finance_freshness(db, user_id=uid, marketplace="ozon", sku=SKU,
                                    window_days=14, now=NOW)
        assert f["last_finance_date"] is None and f["last_import_at"] is None
        assert f["rows_in_window"] == 0
        assert f["window_start"] == "2026-06-07" and f["window_end"] == "2026-06-21"
    _run(go())


def test_freshness_none_without_db_or_keys():
    async def go():
        db = await _engine()
        assert await finance_freshness(None, user_id="u", marketplace="ozon", sku=SKU) is None
        assert await finance_freshness(db, user_id=None, marketplace="ozon", sku=SKU) is None
        assert await finance_freshness(db, user_id="u", marketplace="ozon", sku=None) is None
    _run(go())


# ── (3) marketplace + sku isolation ──────────────────────────────────────────

def test_freshness_marketplace_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, mp="ozon", date="2026-06-18")
        await _fin(db, uid, mp="wb", date="2026-06-10")
        await db.commit()
        oz = await finance_freshness(db, user_id=uid, marketplace="ozon", sku=SKU, now=NOW)
        wb = await finance_freshness(db, user_id=uid, marketplace="wb", sku=SKU, now=NOW)
        assert oz["last_finance_date"] == "2026-06-18" and oz["rows_in_window"] == 1
        assert wb["last_finance_date"] == "2026-06-10"   # ozon row never leaks to wb
    _run(go())


def test_freshness_sku_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="A", date="2026-06-18")
        await _fin(db, uid, sku="B", date="2026-06-10")
        await db.commit()
        a = await finance_freshness(db, user_id=uid, marketplace="ozon", sku="A", now=NOW)
        assert a["last_finance_date"] == "2026-06-18" and a["rows_in_window"] == 1
    _run(go())


# ── (4) passthrough into Feed source_context for unmeasured effects ──────────

async def _seed_promoted(db, uid, *, with_baseline: bool):
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="товар", cogs=50.0, cogs_source="manual"))
    db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace="ozon", external_id=SKU))
    await build_operations_signal(db, user_id=uid, marketplace="ozon", sku=SKU,
                                  net_profit=-100.0, in_auto_promotion=True)
    if with_baseline:
        await _fin(db, uid, date="2026-06-01", net_profit=-200.0,
                   created_at=datetime(2026, 6, 2, 9, 0))
    await db.commit()
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()


def test_not_evaluated_feed_item_gets_freshness():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_promoted(db, uid, with_baseline=True)
        await open_effect_measurement(db, user_id=uid, window_days=14, now=datetime(2026, 6, 1)); await db.commit()
        await close_effect_measurement(db, user_id=uid, now=NOW); await db.commit()  # no after → not_evaluated

        items = await build_feed(db, user_id=uid, include_resolved=True, now=NOW)
        it = next(i for i in items if i.action_key == "stop_auto_promotion"
                  and (i.group_key or "").startswith(SIGNAL_KEY))
        assert it.effect_status == "not_evaluated"
        fr = it.source_context.get("freshness")
        assert fr is not None
        assert fr["last_finance_date"] == "2026-06-01"
        assert "rows_in_window" in fr and "window_start" in fr and "window_end" in fr
    _run(go())


def test_not_measured_yet_summary_gets_freshness():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_promoted(db, uid, with_baseline=False)
        await open_effect_measurement(db, user_id=uid, window_days=14, now=datetime(2026, 6, 1)); await db.commit()

        sums = await build_effect_summaries(db, user_id=uid, contour="operations", now=NOW)
        s = next(x for x in sums if x.action_key == "stop_auto_promotion")
        assert s.effect_status == "not_measured_yet"
        assert s.evidence.get("freshness") is not None
    _run(go())


# ── (5) measured effect does NOT get freshness (v1 scope) ────────────────────

def test_measured_effect_has_no_freshness():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_promoted(db, uid, with_baseline=True)
        await open_effect_measurement(db, user_id=uid, window_days=14, now=datetime(2026, 6, 1)); await db.commit()
        # after-finance higher → improved (measured)
        await _fin(db, uid, date="2026-06-18", net_profit=900.0); await db.commit()
        await close_effect_measurement(db, user_id=uid, now=NOW); await db.commit()

        sums = await build_effect_summaries(db, user_id=uid, contour="operations", now=NOW)
        s = next(x for x in sums if x.action_key == "stop_auto_promotion")
        assert s.effect_status == "proven_improved"
        assert "freshness" not in s.evidence            # v1: only unmeasured statuses
    _run(go())


# ── (6) non-finance metric does NOT get freshness ────────────────────────────

def test_non_finance_metric_has_no_freshness():
    async def go():
        from models.engine_signal_decision_link import EngineSignalDecisionLink
        from models.engine_effect_observation import EngineEffectObservation
        db = await _engine(); uid = str(uuid.uuid4())
        ik = f"seo_title_too_short:ozon:{SKU}"
        link = EngineSignalDecisionLink(
            user_id=uid, contour="seo", signal_table="seo_signal",
            signal_id=str(uuid.uuid4()), insight_key=ik, action_key=None,
            decision_id=str(uuid.uuid4()), link_status="promoted", marketplace="ozon", sku=SKU)
        db.add(link); await db.flush()
        db.add(EngineEffectObservation(
            link_id=link.id, user_id=uid, insight_key=ik, metric_key="search_visibility",
            window_days=14, baseline_captured_at=datetime(2026, 6, 1), measured_at=None,
            effect_band="not_evaluated"))
        await _fin(db, uid, date="2026-06-18"); await db.commit()   # finance exists, but metric isn't finance

        sums = await build_effect_summaries(db, user_id=uid, contour="seo", now=NOW)
        s = next(x for x in sums if x.insight_key == ik)
        assert s.metric_key == "search_visibility"
        assert "freshness" not in s.evidence            # non-finance metric → no freshness
    _run(go())
