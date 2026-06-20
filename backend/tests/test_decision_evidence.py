"""
Sprint E1 — decision evidence read model.

get_decision_evidence composes context resolver + ranked alternatives into one
DecisionEvidence for a single (insight_key, action_key). Read-only; reflects the
ranking under the resolved context_group. Verifies history / no-history /
fallback / malformed / user + context isolation / no writes.
"""
import ast
import asyncio
import inspect
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory

from services import decision_evidence
from services.decision_evidence import get_decision_evidence, DecisionEvidence
from services.ranked_alternatives import get_ranked_alternatives_for_insight

# E4: evidence resolves context the same way /alternatives does (listing_id only,
# NOT the insight_key marketplace). With no listing_id the context is fully
# unknown — matching the seeded memory context below.
IKEY = "margin_crisis:wb:SKU1"
CG = "unknown|unknown|unknown|unknown"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _mem(db, uid, action, outcome, n, cg=CG):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"margin_crisis:wb:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action,
                              context_group=cg, outcome=outcome))
    await db.flush()


async def _ev(db, uid, action_key, ikey=IKEY):
    return await get_decision_evidence(db, user_id=uid, insight_key=ikey, action_key=action_key)


# ── history present ──────────────────────────────────────────────────────────

def test_history_present():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await db.commit()
        ev = await _ev(db, uid, "reduce_discount")
        assert isinstance(ev, DecisionEvidence)
        assert ev.action_key == "reduce_discount"
        assert ev.context_group == CG
        assert ev.confirmed == 4 and ev.refuted == 1 and ev.sample == 5
        assert ev.confirmed_rate == 0.8 and ev.weighted_rate == 0.8
        assert ev.fallback is False
        assert ev.source == "decision_memory"
        assert "recent outcomes weighted" in ev.reason
    _run(go())


# ── no history → still returns the action, fallback path ─────────────────────

def test_no_history_fallback():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ev = await _ev(db, uid, "set_price")
        assert ev is not None
        assert ev.action_key == "set_price"
        assert ev.fallback is True
        assert ev.sample == 0 and ev.confirmed == 0 and ev.refuted == 0
        assert ev.confirmed_rate is None and ev.weighted_rate is None
        assert ev.context_group == CG
    _run(go())


def test_below_min_sample_is_fallback():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 2)  # < min_sample
        await db.commit()
        ev = await _ev(db, uid, "set_price")
        assert ev.fallback is True and ev.sample == 2
    _run(go())


# ── malformed insight / unknown action → None ────────────────────────────────

def test_malformed_insight_none():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await _ev(db, uid, "set_price", ikey="") is None
        assert await _ev(db, uid, "update_card", ikey="low_stock:wb:SKU1") is None
    _run(go())


def test_unknown_action_none():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await _ev(db, uid, "mystery_action") is None
    _run(go())


# ── isolation ────────────────────────────────────────────────────────────────

def test_user_isolation():
    async def go():
        db = await _engine(); a = str(uuid.uuid4()); b = str(uuid.uuid4())
        await _mem(db, b, "reduce_discount", "confirmed", 5)  # other user
        await db.commit()
        ev = await _ev(db, a, "reduce_discount")
        assert ev.fallback is True and ev.sample == 0  # user a sees nothing
    _run(go())


def test_context_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # history in a different context must not surface for CG
        await _mem(db, uid, "reduce_discount", "confirmed", 5,
                   cg="ozon|unknown|unknown|unknown")
        await db.commit()
        ev = await _ev(db, uid, "reduce_discount")
        assert ev.fallback is True and ev.sample == 0
    _run(go())


# ── E4 consistency: evidence == top ranked alternative ───────────────────────

def test_evidence_matches_top_alternative():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await db.commit()
        alts = await get_ranked_alternatives_for_insight(db, user_id=uid, insight_key=IKEY)
        top = alts[0]
        ev = await get_decision_evidence(db, user_id=uid, insight_key=IKEY,
                                         action_key=top["action_key"])
        # same context resolution + same ranking → every shared field matches
        assert ev.reason == top["reason"]
        assert ev.confirmed == top["confirmed"] and ev.refuted == top["refuted"]
        assert ev.sample == top["sample"]
        assert ev.confirmed_rate == top["confirmed_rate"]
        assert ev.weighted_rate == top["weighted_rate"]
        assert ev.fallback == top["fallback"]
    _run(go())


# ── purity: no writes / no execution / no promotion ──────────────────────────

def test_no_writes_no_execution():
    src = inspect.getsource(decision_evidence)
    for bad in ("db.add", "db.commit", "db.flush", ".delete(",
                "promote_insight", "record_decision_memory", "execute("):
        assert bad not in src
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "measurement_close_bridge", "refuted_loop",
                "insight_decision_bridge"):
        assert all(bad not in m for m in mods), f"evidence must not import {bad}"
