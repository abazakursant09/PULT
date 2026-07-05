"""
Rating / Reputation Health Diagnosis schema foundation (Phase 5.0) — INERT schema only.

Proves the two new tables (rating_signal, rating_audit) create, insert, and query, that
both models are registered on Base.metadata, are field-for-field parity with the Growth
contour, and — critically — that this schema is wired to NOTHING: the Advisory Runtime
registry has no rating producer and the Decision Feed reads no rating table. Rating /
Reputation Health cannot run yet. Distinct from the Review contour (review_signal untouched).
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
from models.rating_signal import RatingSignal
from models.rating_audit import RatingAudit


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def test_tables_registered_on_metadata():
    assert "rating_signal" in Base.metadata.tables
    assert "rating_audit" in Base.metadata.tables


def test_insert_and_query_roundtrip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(RatingAudit(id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
                           status="completed", total_problems=1, created_at=datetime(2026, 7, 4)))
        db.add(RatingSignal(
            id=str(uuid.uuid4()), audit_id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
            signal_key="rating_decline",
            insight_key="rating_decline:wildberries:SKU1",
            problem_type="rating_decline", status="active",
            what="w", why="y", meaning="m", what_to_do="d", expected_effect="e",
            created_at=datetime(2026, 7, 4)))
        await db.commit()

        sig = (await db.execute(select(RatingSignal).where(RatingSignal.user_id == uid))).scalars().one()
        assert sig.insight_key == "rating_decline:wildberries:SKU1"
        assert sig.status == "active" and sig.audit_id == aid
        aud = (await db.execute(select(RatingAudit).where(RatingAudit.user_id == uid))).scalars().one()
        assert aud.total_problems == 1 and aud.status == "completed"
    _run(go())


def test_schema_parity_with_growth():
    from models.growth_signal import GrowthSignal
    from models.growth_audit import GrowthAudit
    sig_growth = {c.name for c in GrowthSignal.__table__.columns}
    sig_rat = {c.name for c in RatingSignal.__table__.columns}
    assert sig_rat == sig_growth, f"signal columns diverge: {sig_rat ^ sig_growth}"
    aud_growth = {c.name for c in GrowthAudit.__table__.columns}
    aud_rat = {c.name for c in RatingAudit.__table__.columns}
    assert aud_rat == aud_growth, f"audit columns diverge: {aud_rat ^ aud_growth}"


def test_rating_producer_registered_and_enabled():
    # Phase 5.3b enabled the producer: registered AND scheduled (writes rating_signal).
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "rating" in by_key
    assert by_key["rating"].enabled is True


def test_decision_feed_reads_rating():
    # Phase 5.3a wired the reader: rating_signal is now a canonical feed engine. The
    # producer stays DISABLED, so nothing writes rating_signal in prod yet.
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "rating_signal" in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "rating" in contours


def test_review_contour_untouched():
    # Rating is DISTINCT from the Review contour — review_signal stays its own engine.
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "review_signal" in tables      # Review contour still wired, independent
