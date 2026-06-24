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
from models.product import Product
from models.product_listing import ProductListing

from services.learning_os.registry import (
    aggregate_learning_observations, get_action_learning_summary,
    get_action_learning_summary_for_context,
    rank_action_keys_by_observed, get_decision_identity_for_learning,
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


# ── v4. Similar-context experience (context_group: marketplace|cat|price|margin) ─

from models.imported_finance import ImportedFinanceRow as _Fin


async def _measured_ctx(db, uid, *, mp, action, band, margin_pct, metric="ad_profit_impact"):
    """One measured observation whose context margin_band is driven by seeded
    finance (rev=1000, net=rev*margin%). No listing → category/price stay unknown."""
    did = str(uuid.uuid4()); sku = f"SKU-{uuid.uuid4().hex[:8]}"
    ik = f"adv_ad_destroying_profit:{mp}:{sku}"
    link = EngineSignalDecisionLink(
        user_id=uid, contour="advertising", signal_table="advertising_signal",
        signal_id=str(uuid.uuid4()), insight_key=ik, action_key=action, decision_id=did,
        link_status="measured", marketplace=mp, sku=sku)
    db.add(link); await db.flush()
    db.add(EngineEffectObservation(
        link_id=link.id, user_id=uid, insight_key=ik, metric_key=metric, window_days=14,
        baseline_captured_at=PAST, measured_at=NOW, effect_band=band,
        evidence=json.dumps({"baseline": 100, "after": 200}), created_at=PAST))
    db.add(_Fin(import_id="imp", user_id=uid, marketplace=mp, sku=sku,
                revenue=1000.0, net_profit=1000.0 * margin_pct / 100.0, date=NOW.date().isoformat()))
    await db.commit()
    return sku


def _ctx_of(mp, margin_band):
    # no listing → category/price unknown; marketplace component is the lowercased hint
    return f"{mp.lower()}|unknown|unknown|{margin_band}"


def test_context_isolation_by_margin_band():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))   # mid_margin
    for _ in range(8):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="worsened", margin_pct=30))   # high_margin
    mid = _run(get_action_learning_summary_for_context(
        db, user_id=uid, marketplace="wildberries", action_key="stop_auto_promotion",
        context_group=_ctx_of("wildberries", "mid_margin")))
    high = _run(get_action_learning_summary_for_context(
        db, user_id=uid, marketplace="wildberries", action_key="stop_auto_promotion",
        context_group=_ctx_of("wildberries", "high_margin")))
    assert mid.total_count == 10 and mid.improved_count == 10            # only mid_margin counted
    assert high.total_count == 8 and high.worsened_count == 8            # separate context
    assert mid.total_count + high.total_count == 18                      # never blended


def test_context_marketplace_isolation():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="ozon", action="stop_auto_promotion", band="worsened", margin_pct=15))
    wb = _run(get_action_learning_summary_for_context(
        db, user_id=uid, marketplace="wildberries", action_key="stop_auto_promotion",
        context_group=_ctx_of("wildberries", "mid_margin")))
    assert wb.total_count == 10 and wb.improved_count == 10              # ozon never mixed in


def test_feed_shows_context_history_when_enough():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    line = next(x["learning_context"] for x in items if x.get("learning_context"))
    assert line == ("История PULT на Wildberries для товаров с маржой 10–25%: "
                    "это решение помогло в 10 из 10 случаев.")
    assert "%" not in line.replace("10–25%", "")    # no percentage in the count phrasing


def test_feed_falls_back_to_marketplace_when_context_thin():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    # only 5 in the mid_margin context, but 10 marketplace-wide (rest unknown context)
    for _ in range(5):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))
    for _ in range(5):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))  # no finance → unknown ctx
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    lines = [x["learning_context"] for x in items if x.get("learning_context")]
    # context tier (<10) is skipped → marketplace-wide line (10 total) shown
    assert any(l == "История PULT на Wildberries: это решение помогло в 10 из 10 случаев." for l in lines)
    assert all("для товаров" not in l for l in lines)   # no context line below the gate


def test_feed_no_history_below_all_gates():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(5):
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    assert all(x.get("learning_context") in (None, "") for x in items)   # < 10 anywhere → nothing


def test_context_history_no_forbidden_words():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    line = next(x["learning_context"] for x in items if x.get("learning_context")).lower()
    for bad in ("вероятн", "probability", "confidence", "увер", "score", "балл",
                "прогноз", "forecast", "предсказ", "prediction", "roi", "рентаб",
                "прибыл", "profit", "рекоменд"):
        assert bad not in line


# ── v5. Explain WHY the learning_context was shown ───────────────────────────

def test_explain_similar_context():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    ex = next(x["learning_explain"] for x in items if x.get("learning_explain"))
    assert ex["source_level"] == "similar_context"
    assert ex["matched_dimensions"]["marketplace"] == "wb"
    assert ex["matched_dimensions"]["margin_band"] == "mid_margin"
    assert ex["sample_size"] == 10 and ex["improved_count"] == 10 and ex["worsened_count"] == 0
    t = ex["explanation_text"].lower()
    assert "по похожему контексту" in t and "wildberries" in t and "маржа 10–25%" in t
    assert "не прогноз" in t


def test_explain_marketplace_fallback():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(5):    # context tier thin (< 10)
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))
    for _ in range(5):    # unknown-context measured → only marketplace tier reaches 10
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    ex = next(x["learning_explain"] for x in items if x.get("learning_explain"))
    assert ex["source_level"] == "marketplace"
    assert ex["matched_dimensions"] == {"marketplace": "wb"}
    assert ex["sample_size"] == 10
    t = ex["explanation_text"].lower()
    assert "недостаточно" in t and "по маркетплейсу" in t and "не прогноз" in t


def test_explain_marketplace_isolation():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="ozon", action="stop_auto_promotion", band="worsened", margin_pct=15))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    exps = [x["learning_explain"] for x in items if x.get("learning_explain")]
    wb = next(e for e in exps if e["matched_dimensions"]["marketplace"] == "wb")
    oz = next(e for e in exps if e["matched_dimensions"]["marketplace"] == "ozon")
    assert wb["improved_count"] == 10 and wb["worsened_count"] == 0      # WB observed
    assert oz["improved_count"] == 0 and oz["worsened_count"] == 10      # Ozon observed, never blended


def test_explain_no_forbidden_words():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_ctx(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved", margin_pct=15))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    t = next(x["learning_explain"]["explanation_text"] for x in items if x.get("learning_explain")).lower()
    # "прогноз"/"forecast" are allowed ONLY in the negated disclaimer ("не прогноз")
    for bad in ("вероятн", "probability", "confidence", "уверенн", "score", "балл",
                "предсказ", "prediction", "roi", "рентаб", "прибыл", "profit", "рекоменд"):
        assert bad not in t


# ── Listing Identity v1 — context resolved through Decision.listing_id ────────

async def _measured_with_listing(db, uid, *, mp, action, band, category, price, margin_pct,
                                 metric="ad_profit_impact"):
    """Measured observation whose Decision carries a real listing_id → Product, so
    the resolver reaches category/price (not 'unknown'). margin from seeded finance."""
    sku = f"SKU-{uuid.uuid4().hex[:8]}"; ik = f"adv_ad_destroying_profit:{mp}:{sku}"
    pid = str(uuid.uuid4()); ppid = str(uuid.uuid4()); lid = str(uuid.uuid4()); did = str(uuid.uuid4())
    _pmp = "wb" if mp in ("wb", "wildberries") else mp
    db.add(Product(id=pid, user_id=uid, name="товар", marketplace=_pmp, category=category, price=price))
    db.add(ProductListing(id=lid, physical_product_id=ppid, user_id=uid, marketplace="wb" if mp in ("wb", "wildberries") else mp,
                          external_id=sku, legacy_product_id=pid))
    db.add(Decision(id=did, user_id=uid, problem="adv", listing_id=lid, physical_product_id=ppid,
                    action_key=action, insight_key=ik, status="open"))
    link = EngineSignalDecisionLink(
        user_id=uid, contour="advertising", signal_table="advertising_signal",
        signal_id=str(uuid.uuid4()), insight_key=ik, action_key=action, decision_id=did,
        link_status="measured", marketplace=mp, sku=sku)
    db.add(link); await db.flush()
    db.add(EngineEffectObservation(
        link_id=link.id, user_id=uid, insight_key=ik, metric_key=metric, window_days=14,
        baseline_captured_at=PAST, measured_at=NOW, effect_band=band,
        evidence=json.dumps({"baseline": 100, "after": 200}), created_at=PAST))
    db.add(_Fin(import_id="imp", user_id=uid, marketplace="wb" if mp in ("wb", "wildberries") else mp,
                sku=sku, revenue=1000.0, net_profit=1000.0 * margin_pct / 100.0, date=NOW.date().isoformat()))
    await db.commit()
    return did, lid


def test_decision_identity_helper():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    did, lid = _run(_measured_with_listing(db, uid, mp="wildberries", action="stop_auto_promotion",
                                           band="improved", category="одежда", price=1200.0, margin_pct=15))
    ident = _run(get_decision_identity_for_learning(db, did))
    assert ident["listing_id"] == lid and ident["physical_product_id"] is not None
    # missing / not found → None values, no crash
    assert _run(get_decision_identity_for_learning(db, None)) == {"listing_id": None, "physical_product_id": None}
    assert _run(get_decision_identity_for_learning(db, "ghost"))["listing_id"] is None


def test_listing_identity_enriches_feed_context():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_with_listing(db, uid, mp="wildberries", action="stop_auto_promotion",
                                    band="improved", category="одежда", price=1200.0, margin_pct=15))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    line = next(x["learning_context"] for x in items if x.get("learning_context"))
    ex = next(x["learning_explain"] for x in items if x.get("learning_explain"))
    # category + price no longer 'unknown' — they appear in the context line
    assert "категории Одежда" in line and "ценой 500–2000 ₽" in line and "маржой 10–25%" in line
    assert ex["source_level"] == "similar_context"
    assert ex["matched_dimensions"]["category"] == "одежда"
    assert ex["matched_dimensions"]["price_band"] == "mid"
    assert ex["matched_dimensions"]["margin_band"] == "mid_margin"
    assert ex["matched_dimensions"]["marketplace"] == "wb"


def test_historical_fallback_when_no_listing_id():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):   # no Decision row / no listing_id → identity None → fallback
        _run(_measured(db, uid, mp="wildberries", action="stop_auto_promotion", band="improved"))
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    lines = [x["learning_context"] for x in items if x.get("learning_context")]
    assert any(l == "История PULT на Wildberries: это решение помогло в 10 из 10 случаев." for l in lines)
    assert all("для товаров" not in l for l in lines)   # marketplace-wide fallback, no crash


def test_listing_context_marketplace_isolation():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(10):
        _run(_measured_with_listing(db, uid, mp="wildberries", action="stop_auto_promotion",
                                    band="improved", category="одежда", price=1200.0, margin_pct=15))
    for _ in range(10):
        _run(_measured_with_listing(db, uid, mp="ozon", action="stop_auto_promotion",
                                    band="worsened", category="одежда", price=1200.0, margin_pct=15))
    wb = _run(get_action_learning_summary_for_context(
        db, user_id=uid, marketplace="wildberries", action_key="stop_auto_promotion",
        context_group="wb|одежда|mid|mid_margin"))
    oz = _run(get_action_learning_summary_for_context(
        db, user_id=uid, marketplace="ozon", action_key="stop_auto_promotion",
        context_group="ozon|одежда|mid|mid_margin"))
    assert wb.total_count == 10 and wb.improved_count == 10     # WB observed
    assert oz.total_count == 10 and oz.worsened_count == 10     # Ozon observed, never enriches WB


# ── v6. Explain action ranking (why A is preferred over B) ───────────────────

async def _measured_margin(db, uid, *, mp, action, band, with_listing=False,
                           category="одежда", price=1200.0, margin_pct=15):
    """Measured margin_crisis observation for `action` (so the action space has
    alternatives). with_listing → Decision carries a real listing → context tier."""
    sku = f"SKU-{uuid.uuid4().hex[:8]}"; ik = f"margin_crisis:{mp}:{sku}"
    did = str(uuid.uuid4()); lid = None; ppid = str(uuid.uuid4())
    pmp = "wb" if mp in ("wb", "wildberries") else mp
    if with_listing:
        pid = str(uuid.uuid4()); lid = str(uuid.uuid4())
        db.add(Product(id=pid, user_id=uid, name="товар", marketplace=pmp, category=category, price=price))
        db.add(ProductListing(id=lid, physical_product_id=ppid, user_id=uid, marketplace=pmp,
                              external_id=sku, legacy_product_id=pid))
        db.add(Decision(id=did, user_id=uid, problem="margin", listing_id=lid, physical_product_id=ppid,
                        action_key=action, insight_key=ik, status="open"))
        db.add(_Fin(import_id="imp", user_id=uid, marketplace=pmp, sku=sku,
                    revenue=1000.0, net_profit=1000.0 * margin_pct / 100.0, date=NOW.date().isoformat()))
    link = EngineSignalDecisionLink(
        user_id=uid, contour="advertising", signal_table="advertising_signal",
        signal_id=str(uuid.uuid4()), insight_key=ik, action_key=action, decision_id=did,
        link_status="measured", marketplace=mp, sku=sku)
    db.add(link); await db.flush()
    db.add(EngineEffectObservation(
        link_id=link.id, user_id=uid, insight_key=ik, metric_key="ad_profit_impact", window_days=14,
        baseline_captured_at=PAST, measured_at=NOW, effect_band=band,
        evidence=json.dumps({"baseline": 100, "after": 200}), created_at=PAST))
    await db.commit()


def _items(db, uid):
    return _feed_client(db, uid).get("/api/decision-feed").json()["items"]


def _rank_for(items, action_substr):
    # decision_outcome items don't expose action_key; pick by ranking presence + sample
    return [x for x in items if x.get("ranking_explain")]


def test_ranking_explain_present_marketplace():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(14):
        _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="improved"))
    for _ in range(3):
        _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="worsened"))
    for _ in range(5):
        _run(_measured_margin(db, uid, mp="wildberries", action="set_price", band="improved"))
    for _ in range(9):
        _run(_measured_margin(db, uid, mp="wildberries", action="set_price", band="worsened"))
    ranked = [x["ranking_explain"] for x in _items(db, uid) if x.get("ranking_explain")]
    assert ranked, "expected a ranking_explain on the observed-preferred action"
    r = ranked[0]
    assert r["source_level"] == "marketplace"
    assert set(r["compared_action_keys"]) == {"set_price", "stop_auto_promotion"}
    assert r["sample_size"] == 17
    t = r["explanation_text"]
    assert "чаще приводил к улучшению результата" in t and "не прогноз" in t


def test_ranking_absent_for_inferior_action():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    # reduce_discount strong (17), set_price weak (14) — only reduce_discount outranks
    for _ in range(14): _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="improved"))
    for _ in range(3):  _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="worsened"))
    for _ in range(5):  _run(_measured_margin(db, uid, mp="wildberries", action="set_price", band="improved"))
    for _ in range(9):  _run(_measured_margin(db, uid, mp="wildberries", action="set_price", band="worsened"))
    # both actions have learning_context (>=10 each); exactly ONE carries ranking_explain
    items = _items(db, uid)
    with_ctx = [x for x in items if x.get("learning_context")]
    with_rank = [x for x in items if x.get("ranking_explain")]
    assert len(with_ctx) >= 2 and len(with_rank) >= 1
    # the set_price items (improved 5 < 14) must NOT claim "shown above"
    sp_items = [x for x in with_ctx if "5 из 14" in (x.get("learning_context") or "")]
    assert sp_items and all(x.get("ranking_explain") is None for x in sp_items)


def test_ranking_marketplace_isolation():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(14): _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="improved"))
    for _ in range(3):  _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="worsened"))
    for _ in range(12): _run(_measured_margin(db, uid, mp="wildberries", action="set_price", band="worsened"))
    # Ozon has its OWN strong set_price — must not change WB ranking
    for _ in range(15): _run(_measured_margin(db, uid, mp="ozon", action="set_price", band="improved"))
    ranked = [x["ranking_explain"] for x in _items(db, uid) if x.get("ranking_explain")]
    wb = next(r for r in ranked if r["sample_size"] == 17)        # WB reduce_discount (14+3)
    assert "set_price" in wb["compared_action_keys"]              # compared within WB only
    # WB reduce_discount still preferred — Ozon set_price never blended in


def test_ranking_context_isolation():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    # context tier (listing-backed): reduce_discount strong in-context, set_price weak in-context
    for _ in range(10): _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="improved", with_listing=True))
    for _ in range(10): _run(_measured_margin(db, uid, mp="wildberries", action="set_price", band="worsened", with_listing=True))
    ranked = [x["ranking_explain"] for x in _items(db, uid) if x.get("ranking_explain")]
    assert ranked and ranked[0]["source_level"] == "similar_context"
    assert "в похожем контексте" in ranked[0]["explanation_text"]


def test_ranking_no_forbidden_words_and_no_auto_apply():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    for _ in range(14): _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="improved"))
    for _ in range(3):  _run(_measured_margin(db, uid, mp="wildberries", action="reduce_discount", band="worsened"))
    for _ in range(5):  _run(_measured_margin(db, uid, mp="wildberries", action="set_price", band="improved"))
    t = next(x["ranking_explain"]["explanation_text"] for x in _items(db, uid) if x.get("ranking_explain")).lower()
    for bad in ("вероятн", "probability", "confidence", "уверенн", "score", "балл",
                "предсказ", "prediction", "roi", "рентаб", "прибыл", "profit"):
        assert bad not in t
    for apply_word in ("примен", "нажмите", "сделайте", "auto", "автоприм"):   # no auto-apply implication
        assert apply_word not in t
