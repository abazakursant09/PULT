"""
E2 contract audit — GET /api/learning/evidence.

Locks the response contract (exact field sets), proves frontend-safety (no
internal ids / user_id / tokens / raw DecisionMemory rows leak — context_group
IS an intentional field), and adds the route-layer context-isolation case the
E2 suite asserted only at the service layer. Read-only; handler called directly.
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
    decision_evidence_endpoint, EvidenceResponse, Evidence,
)

IKEY = "margin_crisis:wb:SKU1"
CG = "wb|unknown|unknown|unknown"


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


# ── 4. response contract: exact field sets, frozen ───────────────────────────

def test_response_contract_field_sets():
    assert set(EvidenceResponse.model_fields) == {"insight_key", "evidence"}
    assert set(Evidence.model_fields) == {
        "action_key", "reason", "context_group", "confirmed", "refuted",
        "sample", "confirmed_rate", "weighted_rate", "fallback", "source"}


# ── 6. frontend-safety: no internal ids / user_id / tokens leak ──────────────

def test_no_internal_fields_leak():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await db.commit()
        resp = await _call(db, uid, "reduce_discount")
        dumped = resp.model_dump()
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
        # context_group is an intentional field; everything else internal is not
        forbidden = {"decision_id", "decision_chain_id", "step_in_chain", "product_id",
                     "physical_product_id", "listing_id", "marketplace", "created_at",
                     "effect_value", "estimate_value", "token", "credential",
                     "user_id", "id"}
        assert not (keys & forbidden), f"leaked: {keys & forbidden}"
        assert "context_group" in keys  # intentional, present
    _run(go())


# ── 5. null behavior shape ───────────────────────────────────────────────────

def test_null_shape_unknown_action():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _call(db, uid, "mystery_action")
        assert resp.model_dump() == {"insight_key": IKEY, "evidence": None}
    _run(go())


# ── 7. context isolation (route layer) ───────────────────────────────────────

def test_context_isolation_route():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # strong history in a DIFFERENT context must not surface for CG
        await _mem(db, uid, "reduce_discount", "confirmed", 5,
                   cg="ozon|unknown|unknown|unknown")
        await db.commit()
        resp = await _call(db, uid, "reduce_discount")
        ev = resp.evidence
        assert ev is not None
        assert ev.context_group == CG          # resolved to wb context, not ozon
        assert ev.fallback is True and ev.sample == 0
    _run(go())
