"""
Revenue Diagnosis schema foundation (Phase 2.0) — INERT schema only.

Proves the two new tables (revenue_signal, revenue_audit) create, insert, and query,
that both models are registered on Base.metadata, and — critically — that this schema is
wired to NOTHING: the Advisory Runtime registry has no `revenue_diagnosis` producer and
the Decision Feed reads no revenue table. Revenue Diagnosis cannot run yet.
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
from models.revenue_signal import RevenueSignal
from models.revenue_audit import RevenueAudit


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── tables register on the shared metadata ───────────────────────────────────

def test_tables_registered_on_metadata():
    assert "revenue_signal" in Base.metadata.tables
    assert "revenue_audit" in Base.metadata.tables


# ── create + insert + query round-trip ───────────────────────────────────────

def test_insert_and_query_roundtrip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(RevenueAudit(id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
                            status="completed", total_problems=1, created_at=datetime(2026, 7, 3)))
        db.add(RevenueSignal(
            id=str(uuid.uuid4()), audit_id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
            signal_key="revenue_sustained_decline",
            insight_key="revenue_sustained_decline:wildberries:SKU1",
            problem_type="sustained_decline", status="active",
            what="w", why="y", meaning="m", what_to_do="d", expected_effect="e",
            created_at=datetime(2026, 7, 3)))
        await db.commit()

        sig = (await db.execute(select(RevenueSignal).where(RevenueSignal.user_id == uid))).scalars().one()
        assert sig.insight_key == "revenue_sustained_decline:wildberries:SKU1"
        assert sig.status == "active" and sig.audit_id == aid
        aud = (await db.execute(select(RevenueAudit).where(RevenueAudit.user_id == uid))).scalars().one()
        assert aud.total_problems == 1 and aud.status == "completed"
    _run(go())


# ── schema parity with GrowthSignal / GrowthAudit (field-for-field) ──────────

def test_schema_parity_with_growth():
    from models.growth_signal import GrowthSignal
    from models.growth_audit import GrowthAudit
    sig_growth = {c.name for c in GrowthSignal.__table__.columns}
    sig_rev = {c.name for c in RevenueSignal.__table__.columns}
    assert sig_rev == sig_growth, f"signal columns diverge: {sig_rev ^ sig_growth}"
    aud_growth = {c.name for c in GrowthAudit.__table__.columns}
    aud_rev = {c.name for c in RevenueAudit.__table__.columns}
    assert aud_rev == aud_growth, f"audit columns diverge: {aud_rev ^ aud_growth}"


# ── wired to NOTHING: no producer, not in the feed ───────────────────────────

def test_revenue_producer_registered_but_disabled():
    # Phase 2.1 wired the shadow producer: registered but DISABLED (never scheduled).
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "revenue_diagnosis" in by_key
    assert by_key["revenue_diagnosis"].enabled is False


def test_decision_feed_does_not_reference_revenue():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "revenue_signal" not in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "revenue" not in contours
