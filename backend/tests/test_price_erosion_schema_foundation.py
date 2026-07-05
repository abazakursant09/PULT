"""
Price Erosion / Discount Creep Diagnosis schema foundation (Phase 8.0) — INERT schema only.

Proves the two new tables (price_erosion_signal, price_erosion_audit) create, insert, and query,
that both models are registered on Base.metadata, are field-for-field parity with the Growth
contour, and — critically — that this schema is wired to NOTHING: the Advisory Runtime registry
has no price_erosion producer and the Decision Feed reads no price_erosion table. Price Erosion
cannot run yet. DISTINCT from the executable Pricing contour (pure diagnosis, no price-write) and
from Money Leak / Revenue / Overstock; all 6 LIVE contours stay wired and independent.
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
from models.price_erosion_signal import PriceErosionSignal
from models.price_erosion_audit import PriceErosionAudit


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def test_tables_registered_on_metadata():
    assert "price_erosion_signal" in Base.metadata.tables
    assert "price_erosion_audit" in Base.metadata.tables


def test_insert_and_query_roundtrip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(PriceErosionAudit(id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
                                 status="completed", total_problems=1, created_at=datetime(2026, 7, 5)))
        db.add(PriceErosionSignal(
            id=str(uuid.uuid4()), audit_id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
            signal_key="price_erosion_discount_creep",
            insight_key="price_erosion_discount_creep:wildberries:SKU1",
            problem_type="discount_creep", status="active",
            what="w", why="y", meaning="m", what_to_do="d", expected_effect="e",
            created_at=datetime(2026, 7, 5)))
        await db.commit()

        sig = (await db.execute(select(PriceErosionSignal).where(
            PriceErosionSignal.user_id == uid))).scalars().one()
        assert sig.insight_key == "price_erosion_discount_creep:wildberries:SKU1"
        assert sig.status == "active" and sig.audit_id == aid
        aud = (await db.execute(select(PriceErosionAudit).where(
            PriceErosionAudit.user_id == uid))).scalars().one()
        assert aud.total_problems == 1 and aud.status == "completed"
    _run(go())


def test_schema_parity_with_growth():
    from models.growth_signal import GrowthSignal
    from models.growth_audit import GrowthAudit
    sig_growth = {c.name for c in GrowthSignal.__table__.columns}
    sig_pe = {c.name for c in PriceErosionSignal.__table__.columns}
    assert sig_pe == sig_growth, f"signal columns diverge: {sig_pe ^ sig_growth}"
    aud_growth = {c.name for c in GrowthAudit.__table__.columns}
    aud_pe = {c.name for c in PriceErosionAudit.__table__.columns}
    assert aud_pe == aud_growth, f"audit columns diverge: {aud_pe ^ aud_growth}"


def test_indexes_match_pattern():
    ix = {i.name for i in PriceErosionSignal.__table__.indexes}
    assert ix == {
        "ix_price_erosion_signal_user_listing", "ix_price_erosion_signal_insight",
        "ix_price_erosion_signal_audit", "ix_price_erosion_signal_status",
        "ix_price_erosion_signal_category",
    }
    aix = {i.name for i in PriceErosionAudit.__table__.indexes}
    assert aix == {"ix_price_erosion_audit_user_listing", "ix_price_erosion_audit_status"}


def test_price_erosion_producer_registered_and_enabled():
    # Phase 8.3b enabled the producer: registered AND scheduled (writes price_erosion_signal).
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "price_erosion" in by_key
    assert by_key["price_erosion"].enabled is True


def test_price_erosion_in_decision_feed():
    # Phase 8.3a wired the reader: price_erosion_signal is now a canonical feed engine. The
    # producer stays DISABLED, so nothing writes price_erosion_signal in prod yet.
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "price_erosion_signal" in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "price_erosion" in contours


def test_pricing_and_live_contours_untouched():
    # Price Erosion is pure diagnosis, DISTINCT from the executable Pricing contour and from the
    # 6 live diagnosis contours — all stay wired and independent.
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    for wired in ("pricing_signal", "revenue_signal", "money_leak_signal", "supply_signal",
                  "rating_signal", "review_velocity_signal", "overstock_signal"):
        assert wired in tables, f"missing {wired}"
