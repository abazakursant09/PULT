"""
Metric Catalog foundation tests.

Covers: catalog honesty (every metric binds to a real capability key), spec
well-formedness, availability gating delegated to capability_registry, the
marketplace-code seam, WB adapter normalization, reader honest-absence paths,
and the append-only Observation model. No network: the adapter is driven by an
injected sales fetcher; the reader's adapter registry is monkeypatched.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.observation import Observation
from services import capability_registry
from services.marketplace import metric_catalog, metric_reader
from services.marketplace.metric_reader import (
    MetricSample, MetricUnavailable, read_metric, build_observation,
)
from services.marketplace.wb_metric_adapter import WBMetricAdapter

_UNITS = {"rub", "percent", "units", "days", "rating", "count", "rank"}
_DIRECTIONS = {"higher_better", "lower_better"}
_AGGS = {"sum", "weighted_avg", "min", "max"}


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


# ── Catalog honesty + shape ──────────────────────────────────────────────────

def test_every_metric_binds_to_real_capability_key():
    keys = set(capability_registry.all_keys())
    for name in metric_catalog.known_metrics():
        spec = metric_catalog.get(name)
        assert spec.capability_key in keys, f"{name} → dangling capability_key {spec.capability_key}"


def test_specs_wellformed():
    for name in metric_catalog.known_metrics():
        spec = metric_catalog.get(name)
        assert spec.metric_name == name
        assert spec.unit in _UNITS
        assert spec.direction in _DIRECTIONS
        assert spec.aggregation in _AGGS
        assert spec.grain == "listing"          # native read grain
        assert spec.semantic_def                 # non-empty canonical meaning


# ── Availability gating (delegated to capability_registry) ───────────────────

def test_available_metric_on_marketplace():
    out = metric_catalog.availability("revenue", "wildberries")   # seam: wildberries → wb
    assert out["available"] is True
    assert out["unit"] == "rub"
    assert out["grain"] == "listing"
    assert out["capability_key"] == "sales"


def test_unknown_metric_is_honest():
    out = metric_catalog.availability("does_not_exist", "wb")
    assert out["available"] is False
    assert out["status"] == "unknown_metric"


def test_unknown_marketplace_is_honest():
    out = metric_catalog.availability("revenue", "marsmarket")
    assert out["available"] is False
    assert out["status"] == "no_adapter"


def test_marketplace_code_seam():
    assert metric_catalog.normalize_marketplace("wildberries") == "wb"
    assert metric_catalog.normalize_marketplace("YM") == "yandex"
    assert metric_catalog.normalize_marketplace("nope") is None


# ── WB adapter normalization (injected fetcher, no network) ──────────────────

def test_wb_adapter_revenue_sums_forpay_filtered_by_entity():
    sales_rows = [
        {"nmId": 111, "forPay": 1000.0},
        {"nmId": 111, "forPay": 250.5},
        {"nmId": 999, "forPay": 7777.0},   # different listing — must be excluded
    ]

    async def fake_sales(*, token, date_from, flag=0):
        return sales_rows

    adapter = WBMetricAdapter(sales_fetcher=fake_sales)
    now = datetime(2026, 6, 14)

    rev = _run(adapter.fetch(token="t", metric_name="revenue", entity_id=111,
                             window_days=7, now=now))
    assert isinstance(rev, MetricSample)
    assert rev.value == 1250.5
    assert rev.unit == "rub"
    assert rev.source == "api"
    assert rev.observed_at == now

    units = _run(adapter.fetch(token="t", metric_name="units_sold", entity_id=111,
                               window_days=7, now=now))
    assert units.value == 2.0
    assert units.unit == "units"


def test_wb_adapter_supports_only_thin_set():
    a = WBMetricAdapter(sales_fetcher=lambda **k: [])
    assert a.supports("revenue")
    assert a.supports("units_sold")
    assert not a.supports("ctr")
    assert not a.supports("rating")


# ── Reader honest-absence + happy path ───────────────────────────────────────

def test_read_metric_unknown_metric():
    res = _run(read_metric(token="t", marketplace="wb", metric_name="bogus"))
    assert isinstance(res, MetricUnavailable)
    assert res.reason == "unknown_metric"


def test_read_metric_unknown_marketplace():
    res = _run(read_metric(token="t", marketplace="marsmarket", metric_name="revenue"))
    assert isinstance(res, MetricUnavailable)
    assert res.reason == "no_adapter"


def test_read_metric_adapter_not_implemented(monkeypatch):
    # ad_metrics is API-available on WB, but the adapter wires no ctr read yet.
    # Honest gap, not a fabricated value.
    res = _run(read_metric(token="t", marketplace="wb", metric_name="ctr"))
    assert isinstance(res, MetricUnavailable)
    assert res.reason == "adapter_not_implemented"


def test_read_metric_happy_path(monkeypatch):
    async def fake_sales(*, token, date_from, flag=0):
        return [{"nmId": 5, "forPay": 300.0}, {"nmId": 5, "forPay": 200.0}]

    fake_adapter = WBMetricAdapter(sales_fetcher=fake_sales)
    monkeypatch.setattr(metric_reader, "_adapters", lambda: {"wb": fake_adapter})

    res = _run(read_metric(token="t", marketplace="wildberries", metric_name="revenue",
                           entity_id=5, now=datetime(2026, 6, 14)))
    assert isinstance(res, MetricSample)
    assert res.value == 500.0
    assert res.unit == "rub"


# ── Observation model (append-only persistence) ──────────────────────────────

def test_observation_persists_and_reads_back():
    async def go():
        db = await _engine()
        uid = str(uuid.uuid4())
        obs = Observation(
            id=str(uuid.uuid4()), user_id=uid, entity_grain="listing",
            entity_id="111", metric_name="revenue", marketplace="wb",
            value=1250.5, unit="rub", window_days=7,
            observed_at=datetime(2026, 6, 14), source="api", quality="n=2",
        )
        db.add(obs)
        await db.commit()

        from sqlalchemy import select
        rows = (await db.execute(
            select(Observation).where(Observation.user_id == uid)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].value == 1250.5
        assert rows[0].metric_name == "revenue"

    _run(go())


def test_build_observation_from_sample():
    sample = MetricSample(value=42.0, unit="units", observed_at=datetime(2026, 6, 14),
                          source="api", quality="n=42")
    obs = build_observation(sample, user_id="u1", entity_grain="listing",
                            entity_id=7, metric_name="units_sold",
                            marketplace="wb", window_days=14)
    assert obs.value == 42.0
    assert obs.unit == "units"
    assert obs.entity_id == "7"
    assert obs.metric_name == "units_sold"
    assert obs.source == "api"
