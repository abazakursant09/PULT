"""
Sprint E2 — decision evidence read API.

GET /api/learning/evidence → evidence for one (insight_key, action_key), or
{evidence: null} when the action isn't part of the insight's alternatives.
Handler called directly with a real in-memory db (DI bypassed). Read-only.
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

from routers import learning
from routers.learning import decision_evidence_endpoint, EvidenceResponse, Evidence

IKEY = "margin_crisis:wb:SKU1"
# E4: evidence context now mirrors /alternatives (listing_id only). No listing →
# fully-unknown context, which is what the seeded memory uses.
CG = "unknown|unknown|unknown|unknown"


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


async def _mem(db, uid, action, outcome, n, cg=CG):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"margin_crisis:wb:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action,
                              context_group=cg, outcome=outcome))
    await db.flush()


async def _call(db, uid, action_key, insight_key=IKEY):
    return await decision_evidence_endpoint(
        insight_key=insight_key, action_key=action_key, current_user=_User(uid), db=db)


# ── evidence present ─────────────────────────────────────────────────────────

def test_evidence_present():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await db.commit()
        resp = await _call(db, uid, "reduce_discount")
        assert isinstance(resp, EvidenceResponse)
        assert resp.insight_key == IKEY
        ev = resp.evidence
        assert isinstance(ev, Evidence)
        assert ev.action_key == "reduce_discount"
        assert ev.context_group == CG
        assert ev.confirmed == 4 and ev.refuted == 1 and ev.sample == 5
        assert ev.confirmed_rate == 0.8 and ev.weighted_rate == 0.8
        assert ev.fallback is False and ev.source == "decision_memory"
        assert "recent outcomes weighted" in ev.reason
    _run(go())


# ── no history → fallback evidence (not null) ────────────────────────────────

def test_no_history_fallback_evidence():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _call(db, uid, "set_price")
        assert resp.evidence is not None
        assert resp.evidence.action_key == "set_price"
        assert resp.evidence.fallback is True
        assert resp.evidence.sample == 0
        assert resp.evidence.confirmed_rate is None and resp.evidence.weighted_rate is None
    _run(go())


# ── unknown action → null ────────────────────────────────────────────────────

def test_unknown_action_null():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _call(db, uid, "mystery_action")
        assert resp.insight_key == IKEY and resp.evidence is None
    _run(go())


# ── malformed insight → null ─────────────────────────────────────────────────

def test_malformed_insight_null():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _call(db, uid, "set_price", insight_key="")
        assert resp.evidence is None
    _run(go())


# ── user isolation ───────────────────────────────────────────────────────────

def test_user_isolation():
    async def go():
        db = await _engine(); a = str(uuid.uuid4()); b = str(uuid.uuid4())
        await _mem(db, b, "reduce_discount", "confirmed", 5)
        await db.commit()
        resp = await _call(db, a, "reduce_discount")
        assert resp.evidence.fallback is True and resp.evidence.sample == 0
    _run(go())


# ── route registered, GET-only ───────────────────────────────────────────────

def test_route_registered_get_only():
    route = next((r for r in learning.router.routes
                  if getattr(r, "path", None) == "/learning/evidence"), None)
    assert route is not None
    assert route.methods == {"GET"}


# ── read-only: no writes in handler module ───────────────────────────────────

def test_handler_no_writes():
    src = inspect.getsource(learning)
    for bad in ("db.add", "db.commit", "db.flush", ".delete("):
        assert bad not in src


# ── no executor / promotion imports ──────────────────────────────────────────

def test_no_executor_or_promotion_imports():
    tree = ast.parse(inspect.getsource(learning))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
    joined = " ".join(names)
    for forbidden in ("executor", "measurement_close_bridge", "decision_apply",
                      "promote_insight", "wb_client", "ozon_client", "refuted_loop"):
        assert forbidden not in joined
