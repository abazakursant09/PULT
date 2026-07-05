"""
Returns Diagnosis schema foundation (Phase R1a) — INERT schema only.

Proves the two new diagnosis tables (returns_signal, returns_audit) create, insert, and query,
that both models are registered on Base.metadata, are field-for-field parity with the Growth
contour, and — critically — that this schema is wired to NOTHING: the Advisory Runtime registry
has no returns producer and the Decision Feed reads no returns table. Returns Diagnosis cannot run
yet. Distinct from the returns INGESTION table (imported_return_rows, R0). All 7 live contours stay
wired and independent.
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
from models.returns_signal import ReturnsSignal
from models.returns_audit import ReturnsAudit


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def test_tables_registered_on_metadata():
    assert "returns_signal" in Base.metadata.tables
    assert "returns_audit" in Base.metadata.tables


def test_insert_and_query_roundtrip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(ReturnsAudit(id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
                            status="completed", total_problems=1, created_at=datetime(2026, 7, 5)))
        db.add(ReturnsSignal(
            id=str(uuid.uuid4()), audit_id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
            signal_key="returns_return_rate_rise",
            insight_key="returns_return_rate_rise:wildberries:SKU1",
            problem_type="return_rate_rise", status="active",
            what="w", why="y", meaning="m", what_to_do="d", expected_effect="e",
            created_at=datetime(2026, 7, 5)))
        await db.commit()

        sig = (await db.execute(select(ReturnsSignal).where(
            ReturnsSignal.user_id == uid))).scalars().one()
        assert sig.insight_key == "returns_return_rate_rise:wildberries:SKU1"
        assert sig.status == "active" and sig.audit_id == aid
        aud = (await db.execute(select(ReturnsAudit).where(
            ReturnsAudit.user_id == uid))).scalars().one()
        assert aud.total_problems == 1 and aud.status == "completed"
    _run(go())


def test_schema_parity_with_growth():
    from models.growth_signal import GrowthSignal
    from models.growth_audit import GrowthAudit
    sig_growth = {c.name for c in GrowthSignal.__table__.columns}
    sig_ret = {c.name for c in ReturnsSignal.__table__.columns}
    assert sig_ret == sig_growth, f"signal columns diverge: {sig_ret ^ sig_growth}"
    aud_growth = {c.name for c in GrowthAudit.__table__.columns}
    aud_ret = {c.name for c in ReturnsAudit.__table__.columns}
    assert aud_ret == aud_growth, f"audit columns diverge: {aud_ret ^ aud_growth}"


def test_indexes_match_pattern():
    ix = {i.name for i in ReturnsSignal.__table__.indexes}
    assert ix == {
        "ix_returns_signal_user_listing", "ix_returns_signal_insight",
        "ix_returns_signal_audit", "ix_returns_signal_status", "ix_returns_signal_category",
    }
    aix = {i.name for i in ReturnsAudit.__table__.indexes}
    assert aix == {"ix_returns_audit_user_listing", "ix_returns_audit_status"}


def test_returns_producer_registered_but_disabled():
    # Phase R1b wired the shadow producer: registered but DISABLED (never scheduled).
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "returns" in by_key
    assert by_key["returns"].enabled is False


def test_returns_in_decision_feed():
    # Phase R3a wired the reader: returns_signal is now a canonical feed engine. The producer
    # stays DISABLED, so nothing writes returns_signal in prod yet (INERT until R3b enables it).
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "returns_signal" in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "returns" in contours


def test_all_seven_live_contours_untouched():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal"):
        assert wired in tables, f"missing {wired}"
