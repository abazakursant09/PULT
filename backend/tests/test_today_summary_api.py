"""
Business Today Summary (A-summary) — endpoint tests.

GET /api/today/summary is a read-only ASSEMBLY of existing aggregates: money verbatim
from get_daily_summary, loss count from get_loss_products(days=1), and three feed
counts (critical priority / growth contour / operations contour) as plain len() over
build_feed. No new analytics, no DB write.
"""
import asyncio
import uuid
from datetime import date, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal
from models.operations_signal import OperationsSignal

import services.finance_aggregator as fin_agg
from routers.today import get_today_summary, TodaySummaryView

YEST = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")


def _run(c):
    return asyncio.run(c)


async def _factory():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)


class _User:
    def __init__(self, uid):
        self.id = uid


async def _fin(db, uid, *, sku, revenue, net_profit, mp="ozon"):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date=YEST, sku=sku, title=sku, revenue=revenue, net_profit=net_profit,
                              ad_spend=0.0, quantity=1))


async def _signals(db, uid):
    db.add(OperationsSignal(user_id=uid, signal_key="operations_low_stock",
           insight_key="operations_low_stock:ozon:LOW", problem_type="low_stock",
           category="operations", marketplace="ozon", sku="LOW", recommended_action_key=None,
           what="низкий остаток", why="x", meaning="x", what_to_do="пополнить",
           expected_effect="x", priority_level="critical", status="active"))
    db.add(GrowthSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="growth_margin_expansion_candidate", problem_type="margin_expansion_candidate",
           insight_key="growth_margin_expansion_candidate:ozon:G1", marketplace="ozon", sku="G1",
           status="active", what="рост", why="x", meaning="x", what_to_do="x", expected_effect="x"))
    db.add(LegalSignal(audit_id=str(uuid.uuid4()), user_id=uid, signal_key="legal_content_claim_risk",
           requirement_type="content_claim_risk", insight_key="legal_content_claim_risk:ozon:L1",
           marketplace="ozon", sku="L1", status="active", what="юр", why="x", meaning="x",
           what_to_do="x", expected_effect="x"))


# ── (1) money fields come verbatim from get_daily_summary ────────────────────

def test_money_fields_from_daily_summary(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4())
        monkeypatch.setattr(fin_agg, "AsyncSessionLocal", factory)
        async with factory() as db:
            await _fin(db, uid, sku="A", revenue=1000.0, net_profit=300.0)
            await db.commit()
        async with factory() as db:
            resp = await get_today_summary(current_user=_User(uid), db=db)
        assert isinstance(resp, TodaySummaryView)
        assert resp.revenue_today == 1000.0
        assert resp.profit_today == 300.0
        assert resp.margin_pct == 30.0
        assert resp.is_demo is False and resp.has_data is True
    _run(go())


# ── (2) feed counts: critical / growth / operations ──────────────────────────

def test_feed_counts(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4())
        monkeypatch.setattr(fin_agg, "AsyncSessionLocal", factory)
        async with factory() as db:
            await _fin(db, uid, sku="A", revenue=1000.0, net_profit=300.0)
            await _signals(db, uid)
            await db.commit()
        async with factory() as db:
            resp = await get_today_summary(current_user=_User(uid), db=db)
        assert resp.critical_count == 1                 # only the operations low_stock is critical
        assert resp.growth_opportunities_count == 1
        assert resp.low_stock_count == 1
    _run(go())


# ── (3) loss_products_count from get_loss_products ───────────────────────────

def test_loss_products_count(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4())
        monkeypatch.setattr(fin_agg, "AsyncSessionLocal", factory)
        async with factory() as db:
            await _fin(db, uid, sku="WIN", revenue=1000.0, net_profit=300.0)
            await _fin(db, uid, sku="LOSS", revenue=200.0, net_profit=-150.0)
            await db.commit()
        async with factory() as db:
            resp = await get_today_summary(current_user=_User(uid), db=db)
        assert resp.loss_products_count == 1            # only LOSS has net_profit < 0
    _run(go())


# ── (4) read-only — no DB writes ─────────────────────────────────────────────

def test_no_db_writes(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4())
        monkeypatch.setattr(fin_agg, "AsyncSessionLocal", factory)
        async with factory() as db:
            await _fin(db, uid, sku="A", revenue=1000.0, net_profit=300.0)
            await _signals(db, uid)
            await db.commit()

        async def _counts(db):
            f = (await db.execute(select(func.count()).select_from(ImportedFinanceRow))).scalar()
            o = (await db.execute(select(func.count()).select_from(OperationsSignal))).scalar()
            g = (await db.execute(select(func.count()).select_from(GrowthSignal))).scalar()
            return (f, o, g)

        async with factory() as db:
            before = await _counts(db)
        async with factory() as db:
            await get_today_summary(current_user=_User(uid), db=db)
        async with factory() as db:
            after = await _counts(db)
        assert before == after
    _run(go())


# ── (5) empty → honest is_demo / no data ─────────────────────────────────────

def test_empty_no_data(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4())
        monkeypatch.setattr(fin_agg, "AsyncSessionLocal", factory)
        async with factory() as db:
            resp = await get_today_summary(current_user=_User(uid), db=db)
        assert resp.is_demo is True and resp.has_data is False
        assert resp.revenue_today == 0.0 and resp.loss_products_count == 0
    _run(go())
