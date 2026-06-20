"""
Action Space A0.1 — net_profit metric + finance-backed compute reader.

net_profit is in the catalog (rub, higher_better, listing, sum), read by computing
over ImportedFinanceRow (no marketplace API). MetricUnavailable when db/user_id
missing or no finance rows — never a fabricated 0. The revenue API path and
read_metric backward-compat are unchanged.
"""
import ast
import asyncio
import inspect
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from services.marketplace import metric_catalog, metric_reader
from services.marketplace.metric_reader import read_metric, MetricSample, MetricUnavailable
from services.marketplace import finance_metric_reader

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
               net_profit=0.0, revenue=0.0, commission=0.0, logistics=0.0, ad_spend=0.0):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date=date, sku=sku, net_profit=net_profit, revenue=revenue,
                              commission=commission, logistics=logistics, ad_spend=ad_spend))
    await db.flush()


# ── catalog spec ─────────────────────────────────────────────────────────────

def test_net_profit_in_catalog():
    spec = metric_catalog.get("net_profit")
    assert spec is not None
    assert spec.capability_key == "net_profit"
    assert spec.unit == "rub"
    assert spec.direction == "higher_better"
    assert spec.grain == "listing"
    assert spec.aggregation == "sum"


# ── compute reader: happy path ───────────────────────────────────────────────

def test_read_net_profit_with_rows_returns_sample():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, net_profit=300.0)
        await _fin(db, uid, net_profit=200.0)
        s = await read_metric(token="t", marketplace="wb", metric_name="net_profit",
                              entity_id="SKU1", window_days=7, now=NOW, db=db, user_id=uid)
        assert isinstance(s, MetricSample)
        assert s.value == 500.0 and s.unit == "rub" and s.source == "compute"
    _run(go())


def test_effective_profit_fallback_when_net_profit_zero():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # net_profit 0 but costs present → fallback revenue - commission - logistics - ad_spend
        await _fin(db, uid, net_profit=0.0, revenue=1000.0, commission=100.0,
                   logistics=50.0, ad_spend=150.0)
        s = await read_metric(token="t", marketplace="wb", metric_name="net_profit",
                              entity_id="SKU1", window_days=7, now=NOW, db=db, user_id=uid)
        assert isinstance(s, MetricSample)
        assert s.value == 700.0   # 1000 - 100 - 50 - 150
    _run(go())


def test_marketplace_alias_matched():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, mp="wildberries", net_profit=400.0)  # raw label
        s = await read_metric(token="t", marketplace="wb", metric_name="net_profit",
                              entity_id="SKU1", window_days=7, now=NOW, db=db, user_id=uid)
        assert isinstance(s, MetricSample) and s.value == 400.0
    _run(go())


def test_window_excludes_old_rows():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, net_profit=100.0, date="2026-06-18")   # in window
        await _fin(db, uid, net_profit=999.0, date="2026-01-01")   # outside 7d window
        s = await read_metric(token="t", marketplace="wb", metric_name="net_profit",
                              entity_id="SKU1", window_days=7, now=NOW, db=db, user_id=uid)
        assert isinstance(s, MetricSample) and s.value == 100.0
    _run(go())


# ── unavailable (honest absence) ─────────────────────────────────────────────

def test_no_rows_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await read_metric(token="t", marketplace="wb", metric_name="net_profit",
                              entity_id="SKU1", window_days=7, now=NOW, db=db, user_id=uid)
        assert isinstance(r, MetricUnavailable) and r.reason == "no_finance_rows"
    _run(go())


def test_no_db_unavailable():
    async def go():
        r = await read_metric(token="t", marketplace="wb", metric_name="net_profit",
                              entity_id="SKU1", window_days=7, now=NOW, db=None, user_id="u1")
        assert isinstance(r, MetricUnavailable) and r.reason == "no_db"
    _run(go())


def test_no_user_unavailable():
    async def go():
        db = await _engine()
        r = await read_metric(token="t", marketplace="wb", metric_name="net_profit",
                              entity_id="SKU1", window_days=7, now=NOW, db=db, user_id=None)
        assert isinstance(r, MetricUnavailable) and r.reason == "no_scope"
    _run(go())


# ── revenue path unchanged + backward compat ─────────────────────────────────

def test_revenue_path_unchanged(monkeypatch):
    # revenue routes to the WB adapter (not the compute branch) and works with no
    # db/user_id — proving backward compatibility and no compute leakage.
    class _FakeAdapter:
        marketplace = "wb"
        def supports(self, m): return m == "revenue"
        async def fetch(self, *, token, metric_name, entity_id, window_days, now):
            return MetricSample(value=42.0, unit="rub", observed_at=now, source="api")

    monkeypatch.setattr(metric_reader, "_adapters", lambda: {"wb": _FakeAdapter()})

    async def go():
        r = await read_metric(token="t", marketplace="wb", metric_name="revenue",
                              entity_id="SKU1", window_days=7, now=NOW)  # no db/user_id
        assert isinstance(r, MetricSample) and r.value == 42.0 and r.source == "api"
    _run(go())


def test_read_metric_backward_compatible_signature():
    sig = inspect.signature(read_metric)
    # token/marketplace/metric_name still present; db/user_id are optional additions
    assert sig.parameters["db"].default is None
    assert sig.parameters["user_id"].default is None
    assert "marketplace" in sig.parameters and "metric_name" in sig.parameters


# ── guard: compute reader imports no marketplace clients ─────────────────────

def test_compute_reader_no_marketplace_client_imports():
    tree = ast.parse(inspect.getsource(finance_metric_reader))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("wb_client", "ozon_client", "yandex", "executor"):
        assert all(bad not in m for m in mods), f"compute reader must not import {bad}"
