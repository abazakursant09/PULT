"""
ExecutionLog Decision provenance (Apply bridge slice A).

This slice does NOT apply decisions. It only lets ExecutionLog carry an optional
decision_id. Tests: column exists + persists, helper threads it, execute()
accepts it, and existing call shape (no decision_id) still yields null.
"""
import asyncio
import inspect
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from database import Base
import models  # noqa: F401 — registers tables
from models.execution_log import ExecutionLog
from services.marketplace import executor


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


def test_column_exists():
    assert "decision_id" in {c.name for c in ExecutionLog.__table__.columns}


def test_persists_decision_id():
    async def go():
        db = await _engine()
        rec = ExecutionLog(id=str(uuid.uuid4()), user_id="u1", action_type="set_price",
                           mode="manual_l3", payload={}, status="success",
                           decision_id="dec-123")
        db.add(rec); await db.commit()
        got = (await db.execute(select(ExecutionLog).where(ExecutionLog.user_id == "u1"))).scalar_one()
        assert got.decision_id == "dec-123"
    _run(go())


def test_new_log_threads_decision_id():
    rec = executor._new_log("u1", "set_price", "wb", "manual_l3", {}, None, None,
                            status="pending", decision_id="dec-9")
    assert rec.decision_id == "dec-9"


def test_new_log_defaults_null():
    rec = executor._new_log("u1", "set_price", "wb", "manual_l3", {}, None, None, status="pending")
    assert rec.decision_id is None


def test_execute_accepts_decision_id():
    sig = inspect.signature(executor.execute)
    assert "decision_id" in sig.parameters
    assert sig.parameters["decision_id"].default is None  # optional, backward compatible
