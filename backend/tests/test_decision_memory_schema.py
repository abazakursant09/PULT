"""
Memory OS Phase 1, Slice 1 — schema/model tests (no logic).

Verifies the additive chain columns on Decision, the append-only decision_memory
table (no updated_at / no mutable lifecycle), default step_in_chain=0, and that
old decisions stay chain_id=NULL / step=0.
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── model import / column presence ───────────────────────────────────────────

def test_decision_has_chain_columns():
    cols = Decision.__table__.columns
    assert "decision_chain_id" in cols
    assert "step_in_chain" in cols
    assert cols["decision_chain_id"].nullable is True
    assert cols["step_in_chain"].nullable is False


def test_decision_chain_index_present():
    idx = {i.name for i in Decision.__table__.indexes}
    assert "ix_decision_chain" in idx


def test_decision_memory_table_exists():
    assert DecisionMemory.__tablename__ == "decision_memory"
    expected = {"id", "decision_id", "decision_chain_id", "step_in_chain",
                "product_id", "marketplace", "action_type", "context_group",
                "outcome", "effect_value", "estimate_value", "created_at"}
    assert set(DecisionMemory.__table__.columns.keys()) == expected


def test_decision_memory_is_append_only_shape():
    cols = set(DecisionMemory.__table__.columns.keys())
    # No mutable lifecycle columns on an append-only table.
    assert "updated_at" not in cols
    assert "modified_at" not in cols
    assert "deleted_at" not in cols


def test_decision_memory_indexes():
    idx = {i.name for i in DecisionMemory.__table__.indexes}
    assert {"ix_decision_memory_chain", "ix_decision_memory_product",
            "ix_decision_memory_decision", "ix_decision_memory_context"} <= idx


# ── runtime behavior of defaults ─────────────────────────────────────────────

def test_step_in_chain_defaults_to_zero_and_chain_null():
    async def go():
        db = await _engine()
        d = Decision(user_id=str(uuid.uuid4()), problem="p", status="open")
        db.add(d)
        await db.flush()
        row = (await db.execute(select(Decision).where(Decision.id == d.id))).scalar_one()
        assert row.decision_chain_id is None    # legacy/no-chain default
        assert row.step_in_chain == 0           # default 0
    _run(go())


def test_decision_memory_insert_minimal():
    async def go():
        db = await _engine()
        m = DecisionMemory(decision_id=str(uuid.uuid4()), outcome="refuted")
        db.add(m)
        await db.flush()
        row = (await db.execute(select(DecisionMemory).where(DecisionMemory.id == m.id))).scalar_one()
        assert row.outcome == "refuted"
        assert row.step_in_chain == 0
        assert row.created_at is not None
        assert row.effect_value is None and row.estimate_value is None
    _run(go())
