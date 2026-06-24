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

from services.learning_os.registry import (
    aggregate_learning_observations, get_action_learning_summary,
)
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
    assert txt == "По Wildberries это решение ранее помогло в 8 случаях из 10."
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
    assert wb_line == "По Wildberries это решение ранее помогло в 10 случаях из 10."
    assert oz_line == "По Ozon это решение ранее помогло в 0 случаях из 10."   # observed, not blended
