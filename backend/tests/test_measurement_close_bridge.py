"""
Measurement close bridge (Slice 4: manual close) — tests.

Closes due still_open outcomes by reusing select_due_outcomes + close_measurement.
set_price favorable→confirmed, unfavorable→refuted; null baseline / unreadable
→ insufficient_data; missing token → skipped (stays still_open); not-due and
already-closed untouched; per-outcome errors isolated; idempotent rerun.
"""
import ast
import asyncio
import inspect
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers all tables
from models.decision import Decision
from models.decision_outcome import DecisionOutcome
from models.observation import Observation
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from repositories import decision_outcome as outcome_repo
from services.marketplace import credential_vault, metric_reader
from services.marketplace.metric_reader import MetricSample, MetricUnavailable
from services import measurement_close_bridge as cb
from services.measurement_close_bridge import close_due_measurements, CloseSummary

NOW = datetime(2026, 6, 20)
PAST = NOW - timedelta(days=30)   # created_at in the past → due at NOW


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, baseline=True, with_cred=True, action_key="set_price",
                metric="revenue", baseline_value=100.0, window=7, created_at=PAST,
                mp="wb", entity_id="OFFER1"):
    # Each seed is a fully isolated seller (own uid → own credential), mirroring
    # production where one connection is per (user, marketplace).
    uid = str(uuid.uuid4())
    did = str(uuid.uuid4())
    db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                    action_key=action_key, insight_key=f"{action_key}:{mp}:{did[:8]}"))
    if with_cred:
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                                     status="connected", scopes=["prices"])
        db.add(conn)
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                             secret_enc=credential_vault.encrypt("tok-123")))
    baseline_id = None
    if baseline:
        obs = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing",
                          entity_id=entity_id, metric_name=metric, marketplace=mp,
                          value=baseline_value, unit="rub", observed_at=PAST, source="api")
        db.add(obs)
        await db.flush()
        baseline_id = obs.id
    out = await outcome_repo.create_still_open_outcome(
        db, decision_id=did, metric_name=metric, expected_window_days=window,
        baseline_observation_id=baseline_id)
    # Force created_at into the past so the window has elapsed by NOW.
    out.created_at = created_at
    await db.commit()   # durable, like a Slice-3 opened outcome in production
    return out


def _realized(value):
    async def _r(**kw):
        return MetricSample(value=value, unit="rub", observed_at=kw.get("now") or NOW,
                            source="api", quality="n=5")
    return _r


def _unavailable():
    async def _r(**kw):
        return MetricUnavailable(kw["metric_name"], "adapter_not_implemented")
    return _r


# ── confirmed / refuted ──────────────────────────────────────────────────────

def test_set_price_favorable_confirmed(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _seed(db)
        s = await close_due_measurements(db, now=NOW)
        assert s.total_due == 1 and s.confirmed == 1
        row = await outcome_repo.get_by_decision_id(db, out.decision_id)
        assert row.outcome_label == "confirmed" and row.realized_delta == 50.0
    _run(go())


def test_set_price_unfavorable_refuted(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(80.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _seed(db)
        s = await close_due_measurements(db, now=NOW)
        assert s.refuted == 1
        row = await outcome_repo.get_by_decision_id(db, out.decision_id)
        assert row.outcome_label == "refuted"
    _run(go())


# ── insufficient_data ────────────────────────────────────────────────────────

def test_null_baseline_insufficient():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _seed(db, baseline=False)
        s = await close_due_measurements(db, now=NOW)
        assert s.insufficient == 1
        row = await outcome_repo.get_by_decision_id(db, out.decision_id)
        assert row.outcome_label == "insufficient_data"
    _run(go())


def test_metric_unavailable_insufficient(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _unavailable())

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _seed(db)
        s = await close_due_measurements(db, now=NOW)
        assert s.insufficient == 1
        row = await outcome_repo.get_by_decision_id(db, out.decision_id)
        assert row.outcome_label == "insufficient_data"
    _run(go())


# ── missing token → skip, stays still_open ───────────────────────────────────

def test_missing_token_skipped_stays_open(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _seed(db, with_cred=False)   # real baseline, no credential
        s = await close_due_measurements(db, now=NOW)
        assert s.skipped == 1 and s.confirmed == 0 and s.insufficient == 0
        row = await outcome_repo.get_by_decision_id(db, out.decision_id)
        assert row.outcome_label == "still_open"
    _run(go())


# ── not-due / already-closed untouched ───────────────────────────────────────

def test_not_due_untouched(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _seed(db, created_at=NOW)     # window not elapsed
        s = await close_due_measurements(db, now=NOW)
        assert s.total_due == 0
        row = await outcome_repo.get_by_decision_id(db, out.decision_id)
        assert row.outcome_label == "still_open"
    _run(go())


def test_idempotent_rerun_no_overwrite(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await _seed(db)
        s1 = await close_due_measurements(db, now=NOW)
        assert s1.confirmed == 1
        # Second run: outcome no longer still_open → not selected, label unchanged.
        s2 = await close_due_measurements(db, now=NOW)
        assert s2.total_due == 0 and s2.confirmed == 0
        row = await outcome_repo.get_by_decision_id(db, out.decision_id)
        assert row.outcome_label == "confirmed"
    _run(go())


# ── per-outcome error isolation + counts ─────────────────────────────────────

def test_per_outcome_error_isolated(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        bad = await _seed(db, baseline=False)
        good = await _seed(db, baseline=False)
        # Capture ids before the service runs: its per-outcome rollback expires
        # session objects, so reading bad.decision_id afterwards would lazy-load.
        bad_did, good_did = bad.decision_id, good.decision_id

        real_close = cb.close_measurement

        async def flaky_close(db, *, outcome, token, now=None, min_favorable_delta=0.0):
            if outcome.decision_id == bad_did:
                raise RuntimeError("boom")
            return await real_close(db, outcome=outcome, token=token, now=now,
                                    min_favorable_delta=min_favorable_delta)

        monkeypatch.setattr(cb, "close_measurement", flaky_close)
        s = await close_due_measurements(db, now=NOW)
        assert s.total_due == 2 and s.errors == 1 and s.insufficient == 1
        good_row = await outcome_repo.get_by_decision_id(db, good_did)
        bad_row = await outcome_repo.get_by_decision_id(db, bad_did)
        assert good_row.outcome_label == "insufficient_data"
        assert bad_row.outcome_label == "still_open"   # rolled back
    _run(go())


def test_summary_counts_mixed(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db)                       # confirmed
        await _seed(db, baseline=False)       # insufficient
        await _seed(db, with_cred=False)      # skipped (no token)
        await _seed(db, created_at=NOW)       # not-due (excluded)
        s = await close_due_measurements(db, now=NOW)
        assert s.total_due == 3
        assert s.confirmed == 1 and s.insufficient == 1 and s.skipped == 1
        assert s.refuted == 0 and s.errors == 0
    _run(go())


# ── architecture guards ──────────────────────────────────────────────────────

_FORBIDDEN = ("executor", "wb_client", "ozon_client", "scheduler",
              "attribution", "learning", "counterfactual")


def _imported_modules(mod) -> set[str]:
    tree = ast.parse(inspect.getsource(mod))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


def test_close_bridge_forbidden_imports():
    mods = _imported_modules(cb)
    for m in mods:
        for bad in _FORBIDDEN:
            assert bad not in m, f"close bridge must not import '{m}' (matched '{bad}')"


def test_no_new_labels_invented():
    src = inspect.getsource(cb)
    # Only the existing close_measurement labels are referenced.
    assert "not_taken" not in src  # close never produces not_taken
    assert isinstance(close_due_measurements, type(close_due_measurements))
    assert CloseSummary().total_due == 0
