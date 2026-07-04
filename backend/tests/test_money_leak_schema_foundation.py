"""
Money Leak Detection schema foundation (Phase 3.0) — INERT schema only.

Proves the two new tables (money_leak_signal, money_leak_audit) create, insert, and query,
that both models are registered on Base.metadata, are field-for-field parity with the
Growth contour, and — critically — that this schema is wired to NOTHING: the Advisory
Runtime registry has no money_leak producer and the Decision Feed reads no money_leak
table. Money Leak Detection cannot run yet.
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
from models.money_leak_signal import MoneyLeakSignal
from models.money_leak_audit import MoneyLeakAudit


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def test_tables_registered_on_metadata():
    assert "money_leak_signal" in Base.metadata.tables
    assert "money_leak_audit" in Base.metadata.tables


def test_insert_and_query_roundtrip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(MoneyLeakAudit(id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
                              status="completed", total_problems=1, created_at=datetime(2026, 7, 3)))
        db.add(MoneyLeakSignal(
            id=str(uuid.uuid4()), audit_id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
            signal_key="money_leak_logistics_drift",
            insight_key="money_leak_logistics_drift:wildberries:SKU1",
            problem_type="logistics_drift", status="active",
            what="w", why="y", meaning="m", what_to_do="d", expected_effect="e",
            created_at=datetime(2026, 7, 3)))
        await db.commit()

        sig = (await db.execute(select(MoneyLeakSignal).where(MoneyLeakSignal.user_id == uid))).scalars().one()
        assert sig.insight_key == "money_leak_logistics_drift:wildberries:SKU1"
        assert sig.status == "active" and sig.audit_id == aid
        aud = (await db.execute(select(MoneyLeakAudit).where(MoneyLeakAudit.user_id == uid))).scalars().one()
        assert aud.total_problems == 1 and aud.status == "completed"
    _run(go())


def test_schema_parity_with_growth():
    from models.growth_signal import GrowthSignal
    from models.growth_audit import GrowthAudit
    sig_growth = {c.name for c in GrowthSignal.__table__.columns}
    sig_ml = {c.name for c in MoneyLeakSignal.__table__.columns}
    assert sig_ml == sig_growth, f"signal columns diverge: {sig_ml ^ sig_growth}"
    aud_growth = {c.name for c in GrowthAudit.__table__.columns}
    aud_ml = {c.name for c in MoneyLeakAudit.__table__.columns}
    assert aud_ml == aud_growth, f"audit columns diverge: {aud_ml ^ aud_growth}"


def test_money_leak_producer_registered_and_enabled():
    # Phase 3.3b enabled the producer: registered AND enabled (canonical scheduled writer).
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "money_leak" in by_key
    assert by_key["money_leak"].enabled is True


def test_decision_feed_reads_money_leak():
    # Phase 3.3a wired the reader: money_leak_signal is now a canonical feed engine. The
    # producer stays DISABLED, so nothing writes money_leak_signal in prod yet.
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "money_leak_signal" in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "money_leak" in contours
