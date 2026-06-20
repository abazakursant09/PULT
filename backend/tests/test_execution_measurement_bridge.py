"""
Execution → Measurement bridge (Slice 3: open only) — tests.

Service: opens a still_open DecisionOutcome for honestly measurable actions
(set_price real baseline; update_card null baseline), blocks ad_set_bid, skips
on missing decision_id/offer_id/credential, idempotent. Caller: /execute opens
on real success only, dry_run skipped, exception non-blocking.
"""
import asyncio
import inspect
import types
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers all tables
from models.decision import Decision
from models.decision_outcome import DecisionOutcome
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from services.marketplace import credential_vault, metric_reader
from services.marketplace.metric_reader import MetricSample, MetricUnavailable
from services import execution_measurement_bridge as bridge
from services.execution_measurement_bridge import open_measurement_for_execution


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, action_key, *, scope, mp="wb", with_cred=True):
    dec = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                   action_key=action_key, insight_key=f"{action_key}:{mp}:SKU1")
    db.add(dec)
    if with_cred:
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                                     status="connected", scopes=[scope])
        db.add(conn)
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
                             secret_enc=credential_vault.encrypt("tok-123")))
    await db.flush()
    return dec


def _sample(value):
    async def _r(**kw):
        return MetricSample(value=value, unit="rub", observed_at=kw.get("now"),
                            source="api", quality="n=5")
    return _r


def _unavailable():
    async def _r(**kw):
        return MetricUnavailable(kw["metric_name"], "adapter_not_implemented")
    return _r


# ── set_price: real baseline ─────────────────────────────────────────────────

def test_set_price_opens_with_real_baseline(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _sample(100.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        dec = await _seed(db, uid, "set_price", scope="prices")
        out = await open_measurement_for_execution(
            db, user_id=uid, decision_id=dec.id, action_key="set_price",
            marketplace="wb", entity_id="OFFER1")
        assert out is not None
        assert out.metric_name == "revenue"
        assert out.outcome_label == "still_open"
        assert out.baseline_observation_id is not None
    _run(go())


# ── update_card: honest null baseline ────────────────────────────────────────

def test_update_card_opens_null_baseline(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _unavailable())

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        dec = await _seed(db, uid, "update_card", scope="content")
        out = await open_measurement_for_execution(
            db, user_id=uid, decision_id=dec.id, action_key="update_card",
            marketplace="wb", entity_id="OFFER1")
        assert out is not None
        assert out.metric_name == "ctr"
        assert out.outcome_label == "still_open"
        assert out.baseline_observation_id is None   # honest, not fabricated
    _run(go())


# ── blocked / skipped paths ──────────────────────────────────────────────────

def test_ad_set_bid_blocked():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        dec = await _seed(db, uid, "ad_set_bid", scope="advert")
        out = await open_measurement_for_execution(
            db, user_id=uid, decision_id=dec.id, action_key="ad_set_bid",
            marketplace="wb", entity_id="OFFER1")
        assert out is None
        assert (await db.execute(select(DecisionOutcome))).scalars().first() is None
    _run(go())


def test_missing_decision_id_skipped():
    async def go():
        db = await _engine()
        out = await open_measurement_for_execution(
            db, user_id="u1", decision_id=None, action_key="set_price",
            marketplace="wb", entity_id="OFFER1")
        assert out is None
    _run(go())


def test_missing_offer_id_skipped():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        dec = await _seed(db, uid, "set_price", scope="prices")
        out = await open_measurement_for_execution(
            db, user_id=uid, decision_id=dec.id, action_key="set_price",
            marketplace="wb", entity_id=None)
        assert out is None
    _run(go())


def test_no_credential_skipped(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _sample(100.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        dec = await _seed(db, uid, "set_price", scope="prices", with_cred=False)
        out = await open_measurement_for_execution(
            db, user_id=uid, decision_id=dec.id, action_key="set_price",
            marketplace="wb", entity_id="OFFER1")
        assert out is None
        assert (await db.execute(select(DecisionOutcome))).scalars().first() is None
    _run(go())


def test_decision_not_found_skipped():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await open_measurement_for_execution(
            db, user_id=uid, decision_id=str(uuid.uuid4()), action_key="set_price",
            marketplace="wb", entity_id="OFFER1")
        assert out is None
    _run(go())


def test_idempotent_no_duplicate_outcome(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _sample(100.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        dec = await _seed(db, uid, "set_price", scope="prices")
        a = await open_measurement_for_execution(
            db, user_id=uid, decision_id=dec.id, action_key="set_price",
            marketplace="wb", entity_id="OFFER1")
        b = await open_measurement_for_execution(
            db, user_id=uid, decision_id=dec.id, action_key="set_price",
            marketplace="wb", entity_id="OFFER1")
        assert a.id == b.id
        rows = (await db.execute(select(DecisionOutcome).where(
            DecisionOutcome.decision_id == dec.id))).scalars().all()
        assert len(rows) == 1
    _run(go())


# ── caller wiring (/execute) ─────────────────────────────────────────────────

class _Res:
    ok = True; status = "success"; log_id = "l1"; error = None; marketplace = "wb"


class _DB:
    async def commit(self): return None
    async def rollback(self): return None


def _wire(monkeypatch, *, open_impl, status="success"):
    from routers import action_engine as ae
    captured = {}

    plan = ae._imap.Plan(insight_key="margin_crisis:wb:SKU1", itype="margin_crisis",
                         action_type="set_price",
                         payload={"offer_id": "OFF1", "price": 1, "marketplace": "wb"})

    async def fake_resolve(db, uid, key, overrides): return plan

    async def fake_promote(db, *, user_id, insight):
        from services.insight_decision_bridge import PromoteResult
        return PromoteResult("dec-1", created=True, promotable=True, reason=None)

    class _R(_Res):
        pass
    _R.status = status

    async def fake_exec(**kw): return _R()

    monkeypatch.setattr(ae._imap, "resolve_plan", fake_resolve)
    monkeypatch.setattr(ae, "_promote_decision", fake_promote)
    monkeypatch.setattr(ae, "_executor", types.SimpleNamespace(execute=fake_exec))
    monkeypatch.setattr(ae, "_open_measurement", open_impl)
    return ae, captured


def test_execute_calls_open_on_success(monkeypatch):
    calls = []

    async def open_impl(db, **kw):
        calls.append(kw)
        return object()  # truthy → caller commits

    ae, _ = _wire(monkeypatch, open_impl=open_impl)
    body = types.SimpleNamespace(overrides={}, dry_run=False)
    user = types.SimpleNamespace(id="u1")
    resp = _run(ae.execute_insight("margin_crisis:wb:SKU1", body, current_user=user, db=_DB()))
    assert len(calls) == 1
    assert calls[0]["action_key"] == "set_price"
    assert calls[0]["entity_id"] == "OFF1"
    assert calls[0]["decision_id"] == "dec-1"
    assert resp.success is True


def test_execute_dry_run_skips_open(monkeypatch):
    calls = []

    async def open_impl(db, **kw):
        calls.append(kw); return object()

    ae, _ = _wire(monkeypatch, open_impl=open_impl, status="dry_run_ok")
    body = types.SimpleNamespace(overrides={}, dry_run=True)
    user = types.SimpleNamespace(id="u1")
    resp = _run(ae.execute_insight("margin_crisis:wb:SKU1", body, current_user=user, db=_DB()))
    assert calls == []
    assert resp.success is True


def test_execute_open_exception_non_blocking(monkeypatch):
    async def open_impl(db, **kw):
        raise RuntimeError("boom")

    ae, _ = _wire(monkeypatch, open_impl=open_impl)
    body = types.SimpleNamespace(overrides={}, dry_run=False)
    user = types.SimpleNamespace(id="u1")
    resp = _run(ae.execute_insight("margin_crisis:wb:SKU1", body, current_user=user, db=_DB()))
    assert resp.success is True  # execution unaffected


# ── guards ───────────────────────────────────────────────────────────────────

def test_execute_opens_measurement_single_site_no_close():
    from routers import action_engine as ae
    src = inspect.getsource(ae.execute_insight)
    assert src.count("_open_measurement(") == 1
    for forbidden in ("close_measurement", "decision_validation", "apply_decision"):
        assert forbidden not in src


def test_bridge_blocks_non_listing_actions():
    # ad_* (campaign-grain) stay excluded; listing-grain margin/content actions allowed.
    assert "ad_set_bid" not in bridge._MEASURABLE_ACTIONS
    assert "ad_set_state" not in bridge._MEASURABLE_ACTIONS
    assert bridge._MEASURABLE_ACTIONS == frozenset({"set_price", "update_card", "reduce_discount", "stop_auto_promotion"})
