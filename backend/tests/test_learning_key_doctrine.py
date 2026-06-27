"""
Learning Key Doctrine guard (normative — see docs/learning-key-doctrine.md).

Locks the invariant that the Learning aggregation unit is
`(marketplace, action_key, metric_key)` and that the axis which keeps two different
problem detectors apart is `metric_key`, NOT contour.

Canonical case: advertising and operations BOTH pull `stop_auto_promotion` on `ozon`,
yet must NOT pool, because their observed metric differs:

    advertising (indirect)  -> (ozon, stop_auto_promotion, ad_profit_impact)
    operations (drain)      -> (ozon, stop_auto_promotion, net_profit)

If a future change ever adds a `contour` axis to the key, or makes the two contours
share a metric_key, these tests fail — by design, that needs a doctrine review.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.action_binding.registry import BY_SIGNAL_TYPE
from services.learning_os.registry import (
    aggregate_learning_observations, get_action_learning_summary,
)

NOW = datetime(2026, 6, 27, 12, 0, 0)
PAST = NOW - timedelta(days=20)

ADV_SIGNAL = "adv_ad_on_low_stock"                 # advertising indirect -> stop_auto_promotion
OPS_SIGNAL = "operations_auto_promo_margin_drain"  # operations          -> stop_auto_promotion
ACTION = "stop_auto_promotion"
ADV_METRIC = "ad_profit_impact"
OPS_METRIC = "net_profit"

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _measured(db, uid, *, contour, signal_table, action, metric, band):
    """One closed observation for (ozon, action, metric). Unique sku/insight per call."""
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    ik = f"{contour}:{sku}"
    link = EngineSignalDecisionLink(
        user_id=uid, contour=contour, signal_table=signal_table,
        signal_id=str(uuid.uuid4()), insight_key=ik, action_key=action,
        decision_id=str(uuid.uuid4()), link_status="measured", marketplace="ozon", sku=sku)
    db.add(link); await db.flush()
    db.add(EngineEffectObservation(
        link_id=link.id, user_id=uid, insight_key=ik, metric_key=metric, window_days=14,
        baseline_captured_at=PAST, measured_at=NOW, effect_band=band,
        evidence=json.dumps({"baseline": 100, "after": 200}), created_at=PAST))
    await db.commit()


# ── 1. registry: same action_key, different default metric_key ────────────────

def test_registry_same_action_different_metric():
    adv, ops = BY_SIGNAL_KEY[ADV_SIGNAL], BY_SIGNAL_KEY[OPS_SIGNAL]
    # both bind the SAME executable lever
    assert ACTION in adv.action_keys and ACTION in ops.action_keys
    assert BY_SIGNAL_TYPE[ADV_SIGNAL].action_key == ACTION
    assert BY_SIGNAL_TYPE[OPS_SIGNAL].action_key == ACTION
    # but the observed metric differs — this is what keeps them in separate buckets
    assert adv.default_metric_key == ADV_METRIC
    assert ops.default_metric_key == OPS_METRIC
    assert adv.default_metric_key != ops.default_metric_key


# ── 2. aggregation: the two land in DISTINCT buckets (metric_key is the axis) ──

def test_distinct_learning_buckets_by_metric_key():
    async def go():
        db = await _new_db(); uid = str(uuid.uuid4())
        await _measured(db, uid, contour="advertising", signal_table="advertising_signal",
                        action=ACTION, metric=ADV_METRIC, band="improved")
        await _measured(db, uid, contour="operations", signal_table="operations_signal",
                        action=ACTION, metric=OPS_METRIC, band="worsened")

        buckets = await aggregate_learning_observations(db, user_id=uid)
        keys = {(b.marketplace, b.action_key, b.metric_key) for b in buckets}
        # same (marketplace, action_key) but TWO buckets — split on metric_key
        assert ("ozon", ACTION, ADV_METRIC) in keys
        assert ("ozon", ACTION, OPS_METRIC) in keys
        assert len([b for b in buckets if b.action_key == ACTION]) == 2
    _run(go())


# ── 3. per-metric summaries do not equal each other (no pooling) ──────────────

def test_summaries_are_not_pooled():
    async def go():
        db = await _new_db(); uid = str(uuid.uuid4())
        await _measured(db, uid, contour="advertising", signal_table="advertising_signal",
                        action=ACTION, metric=ADV_METRIC, band="improved")
        await _measured(db, uid, contour="operations", signal_table="operations_signal",
                        action=ACTION, metric=OPS_METRIC, band="worsened")

        adv = await get_action_learning_summary(
            db, user_id=uid, marketplace="ozon", action_key=ACTION, metric_key=ADV_METRIC)
        ops = await get_action_learning_summary(
            db, user_id=uid, marketplace="ozon", action_key=ACTION, metric_key=OPS_METRIC)
        assert adv is not None and ops is not None
        # each summary sees ONLY its own metric's single observation
        assert (adv.total_count, adv.improved_count, adv.worsened_count) == (1, 1, 0)
        assert (ops.total_count, ops.improved_count, ops.worsened_count) == (1, 0, 1)
        assert adv.metric_key == ADV_METRIC and ops.metric_key == OPS_METRIC
    _run(go())


# ── 4. marketplace isolation still holds for this lever ───────────────────────

def test_marketplace_isolation_for_shared_action():
    async def go():
        db = await _new_db(); uid = str(uuid.uuid4())
        await _measured(db, uid, contour="operations", signal_table="operations_signal",
                        action=ACTION, metric=OPS_METRIC, band="improved")
        # an ozon observation must NOT appear under wb
        wb = await get_action_learning_summary(
            db, user_id=uid, marketplace="wb", action_key=ACTION, metric_key=OPS_METRIC)
        assert wb is None
    _run(go())
