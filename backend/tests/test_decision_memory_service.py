"""
Decision Memory service (Memory OS Phase 1, Slice 3) — append-only recording.

INSERT-only API: record_decision_memory writes one immutable row, maps decision
fields, tolerates missing optionals, derives marketplace (or "unknown"), builds a
deterministic context_group that excludes action_type, keeps measured effect and
estimate separate, and never mutates Decision. Not wired to any close path.
"""
import ast
import asyncio
import inspect
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from models.product_listing import ProductListing
from services import decision_memory as dm
from services.decision_memory import record_decision_memory, build_context_group


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _decision(db, *, listing_id=None, chain="c1", step=0, action_key="set_price",
                    pnl=12000.0):
    d = Decision(user_id=str(uuid.uuid4()), problem="p", status="open",
                 action_key=action_key, pnl_impact=pnl, physical_product_id="phys-1",
                 listing_id=listing_id, decision_chain_id=chain, step_in_chain=step)
    db.add(d)
    await db.flush()
    return d


async def _listing(db, uid, mp="wb"):
    li = ProductListing(id=str(uuid.uuid4()), physical_product_id="phys-1", user_id=uid,
                        marketplace=mp, external_id="SKU1")
    db.add(li)
    await db.flush()
    return li


# ── insert / append-only ─────────────────────────────────────────────────────

def test_records_one_row():
    async def go():
        db = await _engine()
        d = await _decision(db)
        row = await record_decision_memory(db, decision=d, outcome="refuted")
        assert isinstance(row, DecisionMemory)
        n = (await db.execute(select(func.count()).select_from(DecisionMemory))).scalar()
        assert n == 1
        assert row.outcome == "refuted"
    _run(go())


def test_second_call_appends_not_updates():
    async def go():
        db = await _engine()
        d = await _decision(db)
        r1 = await record_decision_memory(db, decision=d, outcome="refuted")
        r2 = await record_decision_memory(db, decision=d, outcome="confirmed")
        assert r1.id != r2.id
        n = (await db.execute(select(func.count()).select_from(DecisionMemory))).scalar()
        assert n == 2
        # first row untouched
        first = (await db.execute(select(DecisionMemory).where(DecisionMemory.id == r1.id))).scalar_one()
        assert first.outcome == "refuted"
    _run(go())


# ── field mapping ────────────────────────────────────────────────────────────

def test_field_mapping():
    async def go():
        db = await _engine()
        d = await _decision(db, chain="chainX", step=2, action_key="reduce_discount")
        row = await record_decision_memory(db, decision=d, outcome="confirmed",
                                           effect_value=500.0)
        assert row.decision_id == d.id
        assert row.decision_chain_id == "chainX"
        assert row.step_in_chain == 2
        assert row.product_id == "phys-1"
        assert row.action_type == "reduce_discount"
        assert row.effect_value == 500.0
    _run(go())


def test_missing_chain_tolerated():
    async def go():
        db = await _engine()
        d = await _decision(db, chain=None, step=0)
        row = await record_decision_memory(db, decision=d, outcome="insufficient")
        assert row.decision_chain_id is None
        assert row.step_in_chain == 0
    _run(go())


def test_marketplace_unknown_when_no_listing():
    async def go():
        db = await _engine()
        d = await _decision(db, listing_id=None)
        row = await record_decision_memory(db, decision=d, outcome="refuted")
        assert row.marketplace == "unknown"
        assert row.context_group.startswith("unknown|")
    _run(go())


def test_marketplace_derived_from_listing():
    async def go():
        db = await _engine()
        li = await _listing(db, str(uuid.uuid4()), mp="ozon")
        d = await _decision(db, listing_id=li.id)
        row = await record_decision_memory(db, decision=d, outcome="confirmed")
        assert row.marketplace == "ozon"
        assert row.context_group.startswith("ozon|")
    _run(go())


# ── context_group ────────────────────────────────────────────────────────────

def test_context_group_excludes_action_type():
    cg = build_context_group("wb", "shoes", "mid", "low")
    assert "set_price" not in cg and "action" not in cg
    assert cg == "wb|shoes|mid|low"


def test_context_group_deterministic_and_null_to_unknown():
    a = build_context_group("WB", None, "  ", None)
    b = build_context_group("wb", None, None, None)
    assert a == b == "wb|unknown|unknown|unknown"


def test_effect_and_estimate_separate():
    async def go():
        db = await _engine()
        d = await _decision(db, pnl=9000.0)
        # measured effect provided; estimate falls back to decision.pnl_impact
        row = await record_decision_memory(db, decision=d, outcome="confirmed",
                                           effect_value=123.0)
        assert row.effect_value == 123.0       # measured
        assert row.estimate_value == 9000.0    # estimate, separate field
    _run(go())


# ── no mutation of Decision ──────────────────────────────────────────────────

def test_does_not_mutate_decision():
    async def go():
        db = await _engine()
        d = await _decision(db, chain="keep", step=1)
        before = (d.decision_chain_id, d.step_in_chain, d.status, d.action_key, d.pnl_impact)
        await record_decision_memory(db, decision=d, outcome="refuted")
        after = (d.decision_chain_id, d.step_in_chain, d.status, d.action_key, d.pnl_impact)
        assert before == after
    _run(go())


# ── source / import guards ───────────────────────────────────────────────────

def test_source_has_no_mutation_or_commit():
    src = inspect.getsource(dm)
    for forbidden in ("db.commit", ".delete(", ".merge(", "bulk_update", "update("):
        assert forbidden not in src, f"memory service must be append-only ({forbidden})"


def test_no_forbidden_imports():
    tree = ast.parse(inspect.getsource(dm))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("measurement_close_bridge", "decision_validation", "decision_candidate_engine",
                "decision_policy_engine", "autonomy_scoring_engine", "executor",
                "sklearn", "numpy", "torch"):
        assert all(bad not in m for m in mods), f"memory service must not import {bad}"
