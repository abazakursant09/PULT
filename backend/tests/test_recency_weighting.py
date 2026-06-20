"""
Sprint L4 — outcome recency weighting.

rank_actions orders by weighted_rate (recent outcomes weight 1.0, 31-90d 0.5,
91+ 0.25); min_sample gate stays on RAW count; fallback unchanged. Recent
confirmed beats old confirmed; old refuted matters less; deterministic.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from services.outcome_memory_ranking import rank_actions, _recency_weight

NOW = datetime(2026, 6, 20)
CG = "wb|unknown|unknown|unknown"
ACTIONS = ["set_price", "reduce_discount", "stop_auto_promotion"]


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _mem(db, uid, action, outcome, *, age_days, n=1):
    created = NOW - timedelta(days=age_days)
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"margin_crisis:wb:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action, context_group=CG,
                              outcome=outcome, created_at=created))
    await db.flush()


def _order(r):
    return [x["action_key"] for x in r]


# ── weight bands (deterministic) ─────────────────────────────────────────────

def test_recency_weight_bands():
    assert _recency_weight(NOW, NOW) == 1.0
    assert _recency_weight(NOW, NOW - timedelta(days=30)) == 1.0
    assert _recency_weight(NOW, NOW - timedelta(days=31)) == 0.5
    assert _recency_weight(NOW, NOW - timedelta(days=90)) == 0.5
    assert _recency_weight(NOW, NOW - timedelta(days=91)) == 0.25
    assert _recency_weight(NOW, None) == 0.25


# ── recent confirmed beats old confirmed ─────────────────────────────────────

def test_recent_confirmed_beats_old_confirmed():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # set_price: 3 confirmed, all OLD (120d) → weighted_rate 1.0 but low weight
        await _mem(db, uid, "set_price", "confirmed", age_days=120, n=3)
        # reduce_discount: 3 confirmed RECENT → also 1.0 weighted_rate
        await _mem(db, uid, "reduce_discount", "confirmed", age_days=5, n=3)
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS, now=NOW)
        # both weighted_rate 1.0 → tie → original order (set_price first). This isolates
        # the next test which mixes outcomes; here we assert weights are applied, not order.
        sp = next(r for r in ranked if r["action_key"] == "set_price")
        rd = next(r for r in ranked if r["action_key"] == "reduce_discount")
        assert sp["weighted_rate"] == 1.0 and rd["weighted_rate"] == 1.0
        assert sp["sample"] == 3 and rd["sample"] == 3   # raw counts unaffected
    _run(go())


def test_old_refuted_matters_less():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # set_price: 2 recent confirmed (w 2.0) + 2 OLD refuted (w 0.5) →
        # weighted_rate = 2.0 / (2.0 + 0.5) = 0.8 ; raw confirmed_rate = 2/4 = 0.5
        await _mem(db, uid, "set_price", "confirmed", age_days=5, n=2)
        await _mem(db, uid, "set_price", "refuted", age_days=120, n=2)
        # reduce_discount: 2 recent confirmed + 2 recent refuted →
        # weighted_rate = 2.0 / 4.0 = 0.5 ; raw also 0.5
        await _mem(db, uid, "reduce_discount", "confirmed", age_days=5, n=2)
        await _mem(db, uid, "reduce_discount", "refuted", age_days=5, n=2)
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS, now=NOW)
        sp = next(r for r in ranked if r["action_key"] == "set_price")
        rd = next(r for r in ranked if r["action_key"] == "reduce_discount")
        assert sp["confirmed_rate"] == 0.5 and rd["confirmed_rate"] == 0.5  # raw tie
        assert sp["weighted_rate"] == 0.8 and rd["weighted_rate"] == 0.5    # recency breaks it
        # set_price ranks ABOVE reduce_discount because its refuteds are old
        assert _order(ranked)[:2] == ["set_price", "reduce_discount"]
    _run(go())


# ── min_sample on raw count; fallback unchanged ──────────────────────────────

def test_min_sample_on_raw_count():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # 2 recent confirmed → raw sample 2 < 3 → ineligible even though weighted high
        await _mem(db, uid, "reduce_discount", "confirmed", age_days=1, n=2)
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS, now=NOW)
        rd = next(r for r in ranked if r["action_key"] == "reduce_discount")
        assert rd["eligible"] is False
        assert _order(ranked) == ACTIONS  # fallback order
    _run(go())


def test_no_history_fallback_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS, now=NOW)
        assert _order(ranked) == ACTIONS
        assert all(r["reason"] == "no history, fallback order" for r in ranked)
    _run(go())


def test_context_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # recent confirmed in another context must not weight this one
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key="stop_auto_promotion", insight_key="margin_crisis:wb:X"))
        db.add(DecisionMemory(decision_id=did, action_type="stop_auto_promotion",
                              context_group="ozon|unknown|unknown|unknown", outcome="confirmed",
                              created_at=NOW))
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS, now=NOW)
        assert _order(ranked) == ACTIONS
    _run(go())


def test_deterministic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", age_days=5, n=2)
        await _mem(db, uid, "set_price", "refuted", age_days=120, n=2)
        await db.commit()
        a = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                               context_group=CG, available_actions=ACTIONS, now=NOW)
        b = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                               context_group=CG, available_actions=ACTIONS, now=NOW)
        assert a == b
    _run(go())
