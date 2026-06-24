"""
Learning OS Foundation v1 — observed-only outcome aggregation + feed enrichment.

Descriptive, not predictive: counts of how a measured action's effect band came out,
per (marketplace, action_key, metric_key). MARKETPLACE ISOLATION is a hard rule —
wb / ozon / yandex / megamarket are never merged. No percentages/scores stored;
feed enrichment is gated by a minimum sample and shows observed counts only.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
import models  # registers tables
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation
from models.decision import Decision

from services.learning_os.registry import (
    aggregate_learning_observations, get_action_learning_summary,
    rank_action_keys_by_observed,
)
from services.insight_decision_bridge import InsightPromotionDTO
from services.learning_os.promotion_ranking import promote_alternatives_observed_ranked
from routers import decision_feed as feed_router

NOW = datetime(2026, 6, 24, 12, 0, 0)
PAST = NOW - timedelta(days=20)

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _measured(db, uid, *, mp, action, band, metric="ad_profit_impact", measured=True):
    """One closed observation for (mp, action, metric) with the given effect band.
    Unique SKU/insight per call (the link is unique per user+insight+action)."""
    did = str(uuid.uuid4())
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    ik = f"adv_ad_destroying_profit:{mp}:{sku}"
    link = EngineSignalDecisionLink(
        user_id=uid, contour="advertising", signal_table="advertising_signal",
        signal_id=str(uuid.uuid4()), insight_key=ik, action_key=action, decision_id=did,
        link_status="measured", marketplace=mp, sku=sku)
    db.add(link); await db.flush()
    db.add(EngineEffectObservation(
        link_id=link.id, user_id=uid, insight_key=ik, metric_key=metric, window_days=14,
        baseline_captured_at=PAST, measured_at=(NOW if measured else None),
        effect_band=band, evidence=json.dumps({"baseline": 100, "after": 200}), created_at=PAST))
    await db.commit()


def _feed_client(db, uid):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(feed_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def _agg_for(aggs, *, mp, action):
    return next((a for a in aggs if a.marketplace == mp and a.action_key == action), None)


# ── A3. marketplace isolation — wb and ozon NEVER merge ──────────────────────

def test_marketplace_isolation():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(3):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    for _ in range(2):
        _run(_measured(db, uid, mp="ozon", action="stop_auto_promotion", band="worsened"))
    aggs = _run(aggregate_learning_observations(db, user_id=uid))
    wb = _agg_for(aggs, mp="wb", action="stop_auto_promotion")
    oz = _agg_for(aggs, mp="ozon", action="stop_auto_promotion")
    assert wb.total_count == 3 and wb.improved_count == 3 and wb.worsened_count == 0
    assert oz.total_count == 2 and oz.worsened_count == 2 and oz.improved_count == 0
    # no combined bucket exists
    assert all(not (a.marketplace not in ("wb", "ozon")) for a in aggs)
    assert sum(a.total_count for a in aggs) == 5   # never a blended 5-in-one row


def test_marketplace_alias_does_not_split():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    _run(_measured(db, uid, mp="wb", action="stop_auto_promotion", band="improved"))
    aggs = _run(aggregate_learning_observations(db, user_id=uid))
    wb = _agg_for(aggs, mp="wb", action="stop_auto_promotion")
    assert wb is not None and wb.total_count == 2          # aliases fold to canonical wb
    assert len([a for a in aggs if a.action_key == "stop_auto_promotion"]) == 1


# ── A2. aggregation counts + improved/worsened separation ────────────────────

def test_aggregation_counts_and_separation():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for b in ["improved", "improved", "worsened", "unchanged"]:
        _run(_measured(db, uid, mp="wb", action="stop_auto_promotion", band=b))
    a = _agg_for(_run(aggregate_learning_observations(db, user_id=uid)),
                 mp="wb", action="stop_auto_promotion")
    assert a.total_count == 4
    assert a.improved_count == 2 and a.worsened_count == 1 and a.unchanged_count == 1
    assert a.not_evaluated_count == 0


# ── A2. not_evaluated handled honestly (not counted as improvement) ──────────

def test_not_evaluated_handling():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_measured(db, uid, mp="wb", action="stop_auto_promotion", band="not_evaluated"))
    _run(_measured(db, uid, mp="wb", action="stop_auto_promotion", band="improved"))
    a = _agg_for(_run(aggregate_learning_observations(db, user_id=uid)),
                 mp="wb", action="stop_auto_promotion")
    assert a.total_count == 2 and a.not_evaluated_count == 1 and a.improved_count == 1


# ── A2. open (un-measured) observations are NOT counted ──────────────────────

def test_only_measured_counted():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_measured(db, uid, mp="wb", action="stop_auto_promotion", band="improved", measured=True))
    _run(_measured(db, uid, mp="wb", action="stop_auto_promotion", band="improved", measured=False))
    a = _agg_for(_run(aggregate_learning_observations(db, user_id=uid)),
                 mp="wb", action="stop_auto_promotion")
    assert a.total_count == 1   # the still-open one is excluded


# ── A4. get_action_learning_summary — observed, marketplace-isolated ─────────

def test_action_summary_marketplace_isolated():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(4):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    for _ in range(7):
        _run(_measured(db, uid, mp="ozon", action="stop_auto_promotion", band="improved"))
    wb = _run(get_action_learning_summary(db, user_id=uid, marketplace="wildberries", action_key="stop_auto_promotion"))
    oz = _run(get_action_learning_summary(db, user_id=uid, marketplace="ozon", action_key="stop_auto_promotion"))
    none = _run(get_action_learning_summary(db, user_id=uid, marketplace="yandex", action_key="stop_auto_promotion"))
    assert wb.total_count == 4 and oz.total_count == 7
    assert none is None                                    # no yandex history → None, never borrowed


# ── A5. feed enrichment gate — only with >= 10 measured, counts only ─────────

def test_feed_enrichment_below_gate_shows_nothing():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(9):                                     # below the min sample (10)
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    outs = [x for x in items if x["contour"] == "decision_outcome"]
    assert outs                                            # measured items exist
    assert all(x.get("learning_context") in (None, "") for x in outs)   # gated → nothing


def test_feed_enrichment_at_gate_shows_counts():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(8):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    for _ in range(2):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="worsened"))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    enriched = [x for x in items if x.get("learning_context")]
    assert enriched, "expected learning_context once >= 10 measured"
    txt = enriched[0]["learning_context"]
    assert txt == "История PULT на Wildberries: это решение помогло в 8 из 10 случаев."
    assert "%" not in txt                                  # counts only, never a percentage


def test_feed_enrichment_is_marketplace_specific():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    for _ in range(10):
        _run(_measured(db, uid, mp="ozon", action="stop_auto_promotion", band="worsened"))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    texts = [x["learning_context"] for x in items if x.get("learning_context")]
    wb_line = next(t for t in texts if "Wildberries" in t)
    oz_line = next(t for t in texts if "Ozon" in t)
    assert wb_line == "История PULT на Wildberries: это решение помогло в 10 из 10 случаев."
    assert oz_line == "История PULT на Ozon: это решение помогло в 0 из 10 случаев."   # observed, not blended


# ── A2 v2. Observed sort-only ranking ────────────────────────────────────────

async def _history(db, uid, *, mp, action, improved=0, worsened=0, unchanged=0, not_evaluated=0):
    for _ in range(improved):
        await _measured(db, uid, mp=mp, action=action, band="improved")
    for _ in range(worsened):
        await _measured(db, uid, mp=mp, action=action, band="worsened")
    for _ in range(unchanged):
        await _measured(db, uid, mp=mp, action=action, band="unchanged")
    for _ in range(not_evaluated):
        await _measured(db, uid, mp=mp, action=action, band="not_evaluated")


def test_rank_enough_sample_orders_by_observed():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_history(db, uid, mp="wb", action="reduce_discount", improved=15, worsened=2))   # 17
    _run(_history(db, uid, mp="wb", action="set_price", improved=10, worsened=9))          # 19
    # original deterministic order: set_price first
    order = _run(rank_action_keys_by_observed(
        db, user_id=uid, marketplace="wb", action_keys=["set_price", "reduce_discount"]))
    assert order == ["reduce_discount", "set_price"]   # more improved + fewer worsened first


def test_rank_below_min_sample_keeps_original_order():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_history(db, uid, mp="wb", action="reduce_discount", improved=5))   # < 10
    _run(_history(db, uid, mp="wb", action="set_price", improved=3))         # < 10
    order = _run(rank_action_keys_by_observed(
        db, user_id=uid, marketplace="wb", action_keys=["set_price", "reduce_discount"]))
    assert order == ["set_price", "reduce_discount"]   # deterministic order preserved


def test_rank_tie_keeps_original_order():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_history(db, uid, mp="wb", action="set_price", improved=6, worsened=6))        # 12
    _run(_history(db, uid, mp="wb", action="reduce_discount", improved=6, worsened=6))  # 12
    order = _run(rank_action_keys_by_observed(
        db, user_id=uid, marketplace="wb", action_keys=["set_price", "reduce_discount"]))
    assert order == ["set_price", "reduce_discount"]   # identical stats → original order


def test_rank_marketplace_isolation():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    # strong WB history for reduce_discount; ZERO ozon history
    _run(_history(db, uid, mp="wb", action="reduce_discount", improved=20))
    _run(_history(db, uid, mp="wb", action="set_price", improved=1, worsened=15))
    # ozon ranking must ignore WB entirely → deterministic order
    order = _run(rank_action_keys_by_observed(
        db, user_id=uid, marketplace="ozon", action_keys=["set_price", "reduce_discount"]))
    assert order == ["set_price", "reduce_discount"]   # no ozon data → no reorder, no WB fallback


def test_rank_is_sort_only_all_present():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_history(db, uid, mp="wb", action="reduce_discount", improved=12))
    inp = ["set_price", "reduce_discount", "stop_auto_promotion"]
    order = _run(rank_action_keys_by_observed(db, user_id=uid, marketplace="wb", action_keys=inp))
    assert sorted(order) == sorted(inp) and len(order) == len(inp)   # nothing dropped/added


def test_rank_output_is_only_action_keys():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_history(db, uid, mp="wb", action="set_price", improved=11, worsened=1))
    order = _run(rank_action_keys_by_observed(
        db, user_id=uid, marketplace="wb", action_keys=["set_price", "reduce_discount"]))
    assert all(isinstance(a, str) for a in order)   # no scores / no forecast fields


# ── A3. promotion alternatives use the observed order when sample is enough ───

def _dto(itype="margin_crisis", mp="wb", sku="SKU1"):
    return InsightPromotionDTO(insight_key=f"{itype}:{mp}:{sku}", itype=itype,
                               marketplace=mp, sku=sku)


def _order_of(db, results):
    return [(_run(db.get(Decision, r.decision_id))).action_key for r in results]


def test_promotion_alternatives_use_observed_order():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    # reduce_discount has the stronger proven record on wb
    _run(_history(db, uid, mp="wb", action="reduce_discount", improved=18, worsened=2))
    _run(_history(db, uid, mp="wb", action="set_price", improved=5, worsened=14))
    results = _run(promote_alternatives_observed_ranked(db, user_id=uid, insight=_dto())); _run(db.commit())
    order = _order_of(db, results)
    assert order[0] == "reduce_discount"                      # observed-better promoted first
    assert set(order) == {"set_price", "reduce_discount", "stop_auto_promotion"}   # none dropped


def test_promotion_alternatives_default_order_without_history():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    results = _run(promote_alternatives_observed_ranked(db, user_id=uid, insight=_dto())); _run(db.commit())
    order = _order_of(db, results)
    assert order[0] == "set_price"                            # no history → deterministic order
    assert set(order) == {"set_price", "reduce_discount", "stop_auto_promotion"}


# ── v3. Observed history in the feed — framed as history, never forecast ─────

import pathlib

_CARD = (pathlib.Path(__file__).resolve().parents[2]
         / "frontend" / "components" / "decision-feed" / "DecisionFeedCard.tsx")


def test_feed_history_text_is_observed_not_forecast():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    line = next(x["learning_context"] for x in items if x.get("learning_context"))
    assert line == "История PULT на Wildberries: это решение помогло в 10 из 10 случаев."
    assert "%" not in line                                   # counts only, no percentage
    low = line.lower()
    for bad in ("прогноз", "вероятн", "предсказ", "score", "confidence", "roi", "рентаб", "прибыл"):
        assert bad not in low                                # no forecast / probability / score / ROI


def test_feed_history_marketplace_isolated():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    for _ in range(10):
        _run(_measured(db, uid, mp="ozon", action="stop_auto_promotion", band="worsened"))
    texts = [x["learning_context"] for x in
             _feed_client(db, uid).get("/api/decision-feed").json()["items"] if x.get("learning_context")]
    assert any(t == "История PULT на Wildberries: это решение помогло в 10 из 10 случаев." for t in texts)
    assert any(t == "История PULT на Ozon: это решение помогло в 0 из 10 случаев." for t in texts)
    assert all("Megamarket" not in t and "Yandex" not in t for t in texts)   # WB never blended into others


def test_feed_card_renders_history_and_disclaimer():
    src = _CARD.read_text(encoding="utf-8")
    assert "learning_context" in src                                   # history line is rendered
    assert "Это не прогноз, а только прошлые наблюдения." in src        # explicit "not a forecast"


def test_feed_card_has_no_auto_apply_for_measured():
    src = _CARD.read_text(encoding="utf-8")
    # the apply affordance is gated OFF for measured decision_outcome items
    assert "item.contour !== 'decision_outcome'" in src
    # history surface is descriptive — no apply call wired to learning_context
    assert "learning_context" in src
