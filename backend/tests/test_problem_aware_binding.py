"""
Action Space A0.2 — problem-aware metric binding (margin_crisis → net_profit).

target_metric is problem-aware; open_measurement derives problem_type from the
insight_key prefix and stores net_profit for margin_crisis while pricing/set_price
keeps revenue; close measures net_profit via the compute reader (profit up while
revenue down → confirmed), and stays honest (insufficient) when finance is absent.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_outcome import DecisionOutcome
from models.observation import Observation
from models.imported_finance import ImportedFinanceRow
from repositories import decision_outcome as outcome_repo
from services import decision_measurement, decision_validation
from services.marketplace import metric_reader
from services.marketplace.action_metric_binding import target_metric
from services.marketplace.metric_reader import MetricSample, MetricUnavailable

NOW = datetime(2026, 6, 20)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _decision(db, uid, insight_key):
    d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                 action_key="set_price", pnl_impact=5000.0, insight_key=insight_key)
    db.add(d); await db.flush()
    return d


async def _fin(db, uid, sku, net_profit, date="2026-06-18", mp="wb"):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date=date, sku=sku, net_profit=net_profit))
    await db.flush()


# ── A. binding ───────────────────────────────────────────────────────────────

def test_binding_backward_compatible():
    assert target_metric("set_price") == "revenue"


def test_binding_margin_crisis_net_profit():
    assert target_metric("set_price", problem_type="margin_crisis") == "net_profit"


def test_binding_pricing_problem_revenue():
    assert target_metric("set_price", problem_type="pricing_problem") == "revenue"


def test_binding_unknown_problem_falls_back():
    assert target_metric("set_price", problem_type="whatever") == "revenue"
    assert target_metric("ad_set_bid", problem_type="x") == "ad_cost_ratio"


# ── B. open_measurement ──────────────────────────────────────────────────────

def test_open_margin_crisis_uses_net_profit(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, "SKU1", net_profit=300.0)
        d = await _decision(db, uid, "margin_crisis:wb:SKU1")
        out = await decision_measurement.open_measurement(
            db, decision=d, entity_id="SKU1", marketplace="wb", window_days=7,
            token="t", now=NOW)
        assert out is not None and out.metric_name == "net_profit"
        assert out.baseline_observation_id is not None       # finance baseline captured
        base = await db.get(Observation, out.baseline_observation_id)
        assert base.value == 300.0 and base.source == "compute"
    _run(go())


def test_open_pricing_problem_uses_revenue(monkeypatch):
    # revenue routes to the adapter; stub it so no network.
    class _Fake:
        marketplace = "wb"
        def supports(self, m): return True
        async def fetch(self, *, token, metric_name, entity_id, window_days, now):
            return MetricSample(value=10.0, unit="rub", observed_at=now, source="api")
    monkeypatch.setattr(metric_reader, "_adapters", lambda: {"wb": _Fake()})

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _decision(db, uid, "pricing_problem:wb:SKU1")
        out = await decision_measurement.open_measurement(
            db, decision=d, entity_id="SKU1", marketplace="wb", window_days=7,
            token="t", now=NOW)
        assert out.metric_name == "revenue"
    _run(go())


def test_open_malformed_key_falls_back_to_revenue(monkeypatch):
    class _Fake:
        marketplace = "wb"
        def supports(self, m): return True
        async def fetch(self, **kw):
            return MetricSample(value=1.0, unit="rub", observed_at=kw["now"], source="api")
    monkeypatch.setattr(metric_reader, "_adapters", lambda: {"wb": _Fake()})

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _decision(db, uid, "margin_crisis")  # no colon → malformed → None
        out = await decision_measurement.open_measurement(
            db, decision=d, entity_id="SKU1", marketplace="wb", window_days=7,
            token="t", now=NOW)
        assert out.metric_name == "revenue"
    _run(go())


# ── C. close_measurement ─────────────────────────────────────────────────────

async def _open_net_profit(db, uid, sku, baseline_profit):
    await _fin(db, uid, sku, net_profit=baseline_profit)
    d = await _decision(db, uid, f"margin_crisis:wb:{sku}")
    out = await decision_measurement.open_measurement(
        db, decision=d, entity_id=sku, marketplace="wb", window_days=7, token="t", now=NOW)
    return out


def test_close_net_profit_up_confirmed():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _open_net_profit(db, uid, "SKU1", baseline_profit=100.0)
        # realized window now has higher profit (revenue could be lower — irrelevant)
        await _fin(db, uid, "SKU1", net_profit=400.0, date="2026-06-19")
        r = await decision_validation.close_measurement(db, outcome=out, token="t", now=NOW)
        assert r.outcome_label == "confirmed"
        assert r.realized_delta > 0
    _run(go())


def test_close_net_profit_down_refuted():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _open_net_profit(db, uid, "SKU2", baseline_profit=500.0)
        # realized window (around close-now) has only a LOWER-profit row → refuted.
        close_now = datetime(2026, 6, 28)
        await _fin(db, uid, "SKU2", net_profit=200.0, date="2026-06-25")
        r = await decision_validation.close_measurement(db, outcome=out, token="t", now=close_now)
        assert r.outcome_label == "refuted"
        assert r.realized_delta == -300.0   # 200 - 500
    _run(go())
    _run(go())


def test_close_no_finance_insufficient():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _open_net_profit(db, uid, "SKU3", baseline_profit=100.0)
        # realized read far in the future → no finance rows in window → insufficient
        r = await decision_validation.close_measurement(db, outcome=out, token="t",
                                                        now=NOW + timedelta(days=400))
        assert r.outcome_label == "insufficient_data"
    _run(go())


def test_existing_revenue_outcome_closes_via_revenue(monkeypatch):
    # An outcome stored as revenue still measures via the revenue adapter path.
    class _Fake:
        marketplace = "wb"
        def supports(self, m): return True
        async def fetch(self, **kw):
            return MetricSample(value=150.0, unit="rub", observed_at=kw["now"], source="api")
    monkeypatch.setattr(metric_reader, "_adapters", lambda: {"wb": _Fake()})

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = str(uuid.uuid4())
        base = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing",
                           entity_id="SKU9", metric_name="revenue", marketplace="wb",
                           value=100.0, unit="rub", observed_at=NOW, source="api")
        db.add(base); await db.flush()
        out = await outcome_repo.create_still_open_outcome(
            db, decision_id=did, metric_name="revenue", expected_window_days=7,
            baseline_observation_id=base.id)
        r = await decision_validation.close_measurement(db, outcome=out, token="t", now=NOW)
        assert r.outcome_label == "confirmed" and r.realized_delta == 50.0  # 150 - 100
    _run(go())
