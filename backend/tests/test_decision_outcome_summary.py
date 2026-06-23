"""
Decision Outcome A8 — effect summary / learning feedback tests.

Read-only summaries per promoted decision: proven_improved / proven_unchanged /
proven_worsened / not_evaluated / not_measured_yet, with cautious observed-only
text. No score / forecast / ROI / money. API envelope {items,total}.
"""
import asyncio
import json
import uuid
from datetime import datetime
from dataclasses import fields as dc_fields

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.decision_outcome.effect_summary import (
    build_effect_summaries, aggregate_counts, DecisionEffectSummary,
    PROVEN_IMPROVED, PROVEN_UNCHANGED, PROVEN_WORSENED, NOT_EVALUATED, NOT_MEASURED_YET,
)
from routers import decision_outcome as do_router
from routers.decision_outcome import (
    decision_outcome_effects, decision_outcome_summary, EffectsResponse, SummaryResponse,
)

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _User:
    def __init__(self, uid):
        self.id = uid


async def _seed(db, uid, *, contour="advertising", band=None, measured=True, with_obs=True, sku="SKU1"):
    did = str(uuid.uuid4())
    link = EngineSignalDecisionLink(
        user_id=uid, contour=contour, signal_table=f"{contour}_signal", signal_id=str(uuid.uuid4()),
        insight_key=f"adv_ad_destroying_profit:wildberries:{sku}", action_key="stop_auto_promotion",
        decision_id=did, link_status=("measured" if measured else "promoted"),
        marketplace="wildberries", sku=sku)
    db.add(link); await db.flush()
    if with_obs:
        db.add(EngineEffectObservation(
            link_id=link.id, user_id=uid, insight_key=link.insight_key, metric_key="ad_profit_impact",
            window_days=14, baseline_captured_at=T0, measured_at=(T0 if measured else None),
            effect_band=(band or "not_evaluated"),
            evidence=json.dumps({"baseline": -500.0, "after": 1000.0, "observed_delta": 1500.0})))
    await db.commit()
    return did


# ── per-status summaries ─────────────────────────────────────────────────────

def test_improved_summary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, band="improved")
        s = (await build_effect_summaries(db, user_id=uid))[0]
        assert s.effect_status == PROVEN_IMPROVED
        assert "улучшение" in s.what_happened and s.next_action
        assert s.evidence.get("observed_delta") == 1500.0
    _run(go())


def test_unchanged_summary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, band="unchanged")
        s = (await build_effect_summaries(db, user_id=uid))[0]
        assert s.effect_status == PROVEN_UNCHANGED and "изменения" in s.what_happened.lower()
    _run(go())


def test_worsened_summary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, band="worsened")
        s = (await build_effect_summaries(db, user_id=uid))[0]
        assert s.effect_status == PROVEN_WORSENED and "пересмотреть" in s.next_action.lower()
    _run(go())


def test_not_evaluated_summary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, band="not_evaluated")
        s = (await build_effect_summaries(db, user_id=uid))[0]
        assert s.effect_status == NOT_EVALUATED and "недостаточно данных" in s.what_happened.lower()
    _run(go())


def test_not_measured_yet_summary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # open observation (measured_at None) → not measured yet
        await _seed(db, uid, measured=False, with_obs=True)
        s = (await build_effect_summaries(db, user_id=uid))[0]
        assert s.effect_status == NOT_MEASURED_YET and "не закрыт" in s.what_happened.lower()
        # also: a promoted decision with no observation at all → not_measured_yet
        uid2 = str(uuid.uuid4())
        await _seed(db, uid2, measured=False, with_obs=False)
        s2 = (await build_effect_summaries(db, user_id=uid2))[0]
        assert s2.effect_status == NOT_MEASURED_YET
    _run(go())


# ── no score / forecast / roi / money fields ─────────────────────────────────

def test_no_score_forecast_roi_money_fields():
    names = {f.name for f in dc_fields(DecisionEffectSummary)}
    for bad in ("score", "forecast", "roi", "pnl", "money", "expected_revenue", "predicted_effect"):
        assert bad not in names
    # API views too
    for model in (SummaryResponse, do_router.EffectView):
        for bad in ("score", "forecast", "roi", "money"):
            assert bad not in model.model_fields


# ── aggregate counts honest (not a score) ────────────────────────────────────

def test_aggregate_counts():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, band="improved", sku="A")
        await _seed(db, uid, band="worsened", sku="B")
        await _seed(db, uid, measured=False, with_obs=False, sku="C")
        c = aggregate_counts(await build_effect_summaries(db, user_id=uid))
        assert c["proven_improved"] == 1 and c["proven_worsened"] == 1
        assert c["not_measured_yet"] == 1 and c["total"] == 3
    _run(go())


# ── API envelope + handlers ──────────────────────────────────────────────────

def test_api_effects_and_summary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, band="improved")
        eff = await decision_outcome_effects(current_user=_User(uid), db=db)
        assert isinstance(eff, EffectsResponse)
        assert set(eff.model_dump().keys()) == {"items", "total"} and eff.total == 1
        assert eff.items[0].effect_status == PROVEN_IMPROVED
        summ = await decision_outcome_summary(current_user=_User(uid), db=db)
        assert isinstance(summ, SummaryResponse) and summ.proven_improved == 1 and summ.total == 1
        # filter by effect_status
        none = await decision_outcome_effects(effect_status="proven_worsened",
                                              current_user=_User(uid), db=db)
        assert none.total == 0
    _run(go())


# ── routes mounted ───────────────────────────────────────────────────────────

def test_routes_mounted():
    paths = {getattr(r, "path", None) for r in do_router.router.routes}
    assert {"/decision-outcome/effects", "/decision-outcome/summary"} <= paths
    import main
    app_paths = set(main.app.openapi()["paths"])  # OpenAPI paths: robust on FastAPI 0.136 (flat) and 0.137+ (nested mounts)
    assert "/api/decision-outcome/effects" in app_paths
