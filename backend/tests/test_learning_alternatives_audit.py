"""
L6 contract audit — GET /api/learning/alternatives.

Locks the response contract (exact field sets), proves frontend-safety (no
internal ids / tokens / raw memory rows leak), and covers the route-layer edge
cases the L6 suite didn't assert directly: non-margin insight, user isolation,
and missing listing_id. Read-only; handler called directly with a real db.
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

from routers.learning import (
    ranked_alternatives_endpoint, AlternativesResponse, Alternative,
)

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


async def _call(db, uid, insight_key, listing_id=None):
    return await ranked_alternatives_endpoint(
        insight_key=insight_key, listing_id=listing_id, current_user=_User(uid), db=db)


# ── 4. response contract: exact field sets, frozen ───────────────────────────

def test_response_contract_field_sets():
    assert set(AlternativesResponse.model_fields) == {
        "insight_key", "alternatives", "source", "degraded"}
    assert set(Alternative.model_fields) == {
        "action_key", "rank", "reason", "fallback", "confirmed", "refuted",
        "sample", "confirmed_rate", "weighted_rate"}


# ── 7. frontend-safety: no internal ids / tokens / raw rows leak ─────────────

def test_no_internal_fields_leak():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await db.commit()
        resp = await _call(db, uid, "margin_crisis:wb:SKU1")
        dumped = resp.model_dump()
        # walk every key in the serialized payload
        keys = set()

        def _walk(o):
            if isinstance(o, dict):
                keys.update(o.keys())
                for v in o.values():
                    _walk(v)
            elif isinstance(o, list):
                for v in o:
                    _walk(v)
        _walk(dumped)
        forbidden = {"decision_id", "decision_chain_id", "step_in_chain", "product_id",
                     "physical_product_id", "listing_id", "context_group", "marketplace",
                     "created_at", "effect_value", "estimate_value", "token", "credential",
                     "user_id", "id"}
        assert not (keys & forbidden), f"leaked: {keys & forbidden}"
    _run(go())


# ── 6. non-margin insight (route layer) ──────────────────────────────────────

def test_non_margin_insight_route():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _call(db, uid, "seo_opportunity:wb:SKU1")
        assert isinstance(resp, AlternativesResponse)
        assert [a.action_key for a in resp.alternatives] == ["update_card"]
        assert resp.alternatives[0].fallback is True
        assert resp.alternatives[0].weighted_rate is None
        assert resp.source == "decision_memory"
    _run(go())


# ── 6. user isolation (route layer) ──────────────────────────────────────────

def test_user_isolation_route():
    async def go():
        db = await _engine(); a = str(uuid.uuid4()); b = str(uuid.uuid4())
        # user b has strong history; user a must not see it
        await _mem(db, b, "stop_auto_promotion", "confirmed", 5)
        await db.commit()
        resp = await _call(db, a, "margin_crisis:wb:SKU1")
        assert [x.action_key for x in resp.alternatives] == ACTIONS  # fallback order
        assert all(x.fallback is True for x in resp.alternatives)
    _run(go())


# ── 6. missing listing_id explicit ───────────────────────────────────────────

def test_missing_listing_id_ok():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _call(db, uid, "margin_crisis:wb:SKU1", listing_id=None)
        assert [a.action_key for a in resp.alternatives] == ACTIONS
        assert resp.degraded is True  # no domain data → unknown segments
    _run(go())
