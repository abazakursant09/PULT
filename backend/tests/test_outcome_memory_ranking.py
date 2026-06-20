"""
Sprint L2 — outcome memory ranking foundation.

Pure ranking over DecisionMemory terminal outcomes (confirmed/refuted only),
keyed by (context_group, action_key), min_sample=3, confirmed_rate desc with
deterministic fallback tie-break. No writes, no execution.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from services.outcome_memory_ranking import rank_actions

CG = "wb|unknown|unknown|unknown"          # degraded context_group (L2 accepts this)
ACTIONS = ["set_price", "reduce_discount", "stop_auto_promotion"]


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _mem(db, uid, action, outcome, n=1, context_group=CG):
    """Append n terminal-memory rows for (uid, context_group, action)."""
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"margin_crisis:wb:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action,
                              context_group=context_group, outcome=outcome))
    await db.flush()


def _order(ranked):
    return [r["action_key"] for r in ranked]


# ── fallback when no history ─────────────────────────────────────────────────

def test_no_history_fallback_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        assert _order(ranked) == ACTIONS
        assert all(r["reason"] == "no history, fallback order" for r in ranked)
        assert [r["rank"] for r in ranked] == [1, 2, 3]
    _run(go())


# ── confirmed_rate ordering ──────────────────────────────────────────────────

def test_confirmed_rate_ordering():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # set_price 1/4 (0.25), reduce_discount 3/4 (0.75), stop 4/4 (1.0)
        await _mem(db, uid, "set_price", "confirmed", 1)
        await _mem(db, uid, "set_price", "refuted", 3)
        await _mem(db, uid, "reduce_discount", "confirmed", 3)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await _mem(db, uid, "stop_auto_promotion", "confirmed", 4)
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        assert _order(ranked) == ["stop_auto_promotion", "reduce_discount", "set_price"]
        assert ranked[0]["reason"] == "4/4 confirmed in this context (recent outcomes weighted)"
        assert ranked[2]["confirmed_rate"] == 0.25
    _run(go())


def test_refuted_heavy_ranks_lower():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 3)        # 1.0
        await _mem(db, uid, "reduce_discount", "refuted", 3)    # 0.0
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        # eligible: set_price(1.0) then reduce_discount(0.0); stop has no history → fallback
        assert _order(ranked) == ["set_price", "reduce_discount", "stop_auto_promotion"]
        assert ranked[2]["reason"] == "no history, fallback order"
    _run(go())


# ── min_sample gate ──────────────────────────────────────────────────────────

def test_min_sample_gate():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # reduce_discount only 2 samples (< 3) → not ranked above fallback
        await _mem(db, uid, "reduce_discount", "confirmed", 2)
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        assert _order(ranked) == ACTIONS                       # unchanged fallback order
        rd = next(r for r in ranked if r["action_key"] == "reduce_discount")
        assert rd["eligible"] is False and rd["reason"] == "not enough history, fallback order"
    _run(go())


# ── insufficient & non-terminal ignored ──────────────────────────────────────

def test_insufficient_and_followup_ignored():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 3)
        await _mem(db, uid, "set_price", "insufficient", 5)     # ignored
        await _mem(db, uid, "set_price", "followup_created", 5) # ignored
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        sp = next(r for r in ranked if r["action_key"] == "set_price")
        assert sp["sample"] == 3 and sp["confirmed_rate"] == 1.0   # only terminals counted
    _run(go())


# ── tie uses fallback order ──────────────────────────────────────────────────

def test_tie_uses_fallback_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 3)        # 1.0
        await _mem(db, uid, "reduce_discount", "confirmed", 3)  # 1.0 (tie)
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        # tie at 1.0 → original order set_price before reduce_discount
        assert _order(ranked)[:2] == ["set_price", "reduce_discount"]
    _run(go())


# ── context isolation ────────────────────────────────────────────────────────

def test_context_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # history in a DIFFERENT context_group must not affect this one
        await _mem(db, uid, "reduce_discount", "confirmed", 3, context_group="ozon|unknown|unknown|unknown")
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        assert _order(ranked) == ACTIONS                       # no cross-context leak
    _run(go())


def test_user_isolation():
    async def go():
        db = await _engine(); a = str(uuid.uuid4()); b = str(uuid.uuid4())
        await _mem(db, b, "stop_auto_promotion", "confirmed", 3)  # other user
        await db.commit()
        ranked = await rank_actions(db, user_id=a, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        assert _order(ranked) == ACTIONS                       # user a has no history
    _run(go())


# ── unknown / determinism ────────────────────────────────────────────────────

def test_unknown_action_safe():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 3)
        await db.commit()
        avail = ["set_price", "mystery_action"]
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=avail)
        assert _order(ranked) == ["set_price", "mystery_action"]
        my = next(r for r in ranked if r["action_key"] == "mystery_action")
        assert my["eligible"] is False and my["sample"] == 0
    _run(go())


def test_deterministic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 3)
        await _mem(db, uid, "reduce_discount", "refuted", 3)
        await db.commit()
        a = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                               context_group=CG, available_actions=ACTIONS)
        b = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                               context_group=CG, available_actions=ACTIONS)
        assert _order(a) == _order(b)
    _run(go())


# ── purity guard ─────────────────────────────────────────────────────────────

def test_no_writes_no_ml():
    import inspect, ast
    from services import outcome_memory_ranking as r
    src = inspect.getsource(r)
    for bad in ("db.add", "db.commit", "db.flush", ".delete(", "random", "sklearn", "numpy"):
        assert bad not in src
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "measurement_close_bridge", "refuted_loop", "sklearn", "numpy", "torch"):
        assert all(bad not in m for m in mods)
