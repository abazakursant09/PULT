"""
Supply / Replenishment Diagnosis schema foundation (Phase 4.0) — INERT schema only.

Proves the two new tables (supply_signal, supply_audit) create, insert, and query, that
both models are registered on Base.metadata, are field-for-field parity with the Growth
contour, and — critically — that this schema is wired to NOTHING: the Advisory Runtime
registry has no supply producer and the Decision Feed reads no supply table. Supply
Diagnosis cannot run yet.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.supply_signal import SupplySignal
from models.supply_audit import SupplyAudit


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def test_tables_registered_on_metadata():
    assert "supply_signal" in Base.metadata.tables
    assert "supply_audit" in Base.metadata.tables


def test_insert_and_query_roundtrip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(SupplyAudit(id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
                           status="completed", total_problems=1, created_at=datetime(2026, 7, 4)))
        db.add(SupplySignal(
            id=str(uuid.uuid4()), audit_id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
            signal_key="supply_stockout_risk",
            insight_key="supply_stockout_risk:wildberries:SKU1",
            problem_type="stockout_risk", status="active",
            what="w", why="y", meaning="m", what_to_do="d", expected_effect="e",
            created_at=datetime(2026, 7, 4)))
        await db.commit()

        sig = (await db.execute(select(SupplySignal).where(SupplySignal.user_id == uid))).scalars().one()
        assert sig.insight_key == "supply_stockout_risk:wildberries:SKU1"
        assert sig.status == "active" and sig.audit_id == aid
        aud = (await db.execute(select(SupplyAudit).where(SupplyAudit.user_id == uid))).scalars().one()
        assert aud.total_problems == 1 and aud.status == "completed"
    _run(go())


def test_schema_parity_with_growth():
    from models.growth_signal import GrowthSignal
    from models.growth_audit import GrowthAudit
    sig_growth = {c.name for c in GrowthSignal.__table__.columns}
    sig_sup = {c.name for c in SupplySignal.__table__.columns}
    assert sig_sup == sig_growth, f"signal columns diverge: {sig_sup ^ sig_growth}"
    aud_growth = {c.name for c in GrowthAudit.__table__.columns}
    aud_sup = {c.name for c in SupplyAudit.__table__.columns}
    assert aud_sup == aud_growth, f"audit columns diverge: {aud_sup ^ aud_growth}"


def test_no_advisory_runtime_producer():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    keys = {s.key for s in ADVISORY_PRODUCERS}
    assert "supply" not in keys
    assert "replenishment" not in keys


def test_decision_feed_does_not_reference_supply():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "supply_signal" not in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "supply" not in contours
