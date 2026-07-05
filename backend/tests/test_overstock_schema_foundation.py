"""
Overstock / Dead Stock Diagnosis schema foundation (Phase 7.0) — INERT schema only.

Proves the two new tables (overstock_signal, overstock_audit) create, insert, and query, that
both models are registered on Base.metadata, are field-for-field parity with the Growth
contour, and — critically — that this schema is wired to NOTHING: the Advisory Runtime registry
has no overstock producer and the Decision Feed reads no overstock table. Overstock / Dead Stock
cannot run yet. The mirror of Supply (stock-out runway) — supply_signal stays independent; also
distinct from Revenue / Money Leak / Pricing.
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
from models.overstock_signal import OverstockSignal
from models.overstock_audit import OverstockAudit


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def test_tables_registered_on_metadata():
    assert "overstock_signal" in Base.metadata.tables
    assert "overstock_audit" in Base.metadata.tables


def test_insert_and_query_roundtrip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(OverstockAudit(id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
                              status="completed", total_problems=1, created_at=datetime(2026, 7, 5)))
        db.add(OverstockSignal(
            id=str(uuid.uuid4()), audit_id=aid, user_id=uid, marketplace="wildberries", sku="SKU1",
            signal_key="overstock_dead_stock",
            insight_key="overstock_dead_stock:wildberries:SKU1",
            problem_type="overstock_dead_stock", status="active",
            what="w", why="y", meaning="m", what_to_do="d", expected_effect="e",
            created_at=datetime(2026, 7, 5)))
        await db.commit()

        sig = (await db.execute(select(OverstockSignal).where(
            OverstockSignal.user_id == uid))).scalars().one()
        assert sig.insight_key == "overstock_dead_stock:wildberries:SKU1"
        assert sig.status == "active" and sig.audit_id == aid
        aud = (await db.execute(select(OverstockAudit).where(
            OverstockAudit.user_id == uid))).scalars().one()
        assert aud.total_problems == 1 and aud.status == "completed"
    _run(go())


def test_schema_parity_with_growth():
    from models.growth_signal import GrowthSignal
    from models.growth_audit import GrowthAudit
    sig_growth = {c.name for c in GrowthSignal.__table__.columns}
    sig_ov = {c.name for c in OverstockSignal.__table__.columns}
    assert sig_ov == sig_growth, f"signal columns diverge: {sig_ov ^ sig_growth}"
    aud_growth = {c.name for c in GrowthAudit.__table__.columns}
    aud_ov = {c.name for c in OverstockAudit.__table__.columns}
    assert aud_ov == aud_growth, f"audit columns diverge: {aud_ov ^ aud_growth}"


def test_expected_columns_present():
    # the fields the future producer needs: seller (user_id), marketplace, sku/listing identity,
    # severity (priority_level/effect_band), evidence hash, lifecycle status, timestamps.
    # (stock qty / recent units sold / velocity window travel in the evidence dict at producer
    # time — same as Supply — not as columns; parity with growth is the schema contract.)
    cols = {c.name for c in OverstockSignal.__table__.columns}
    for needed in ("user_id", "marketplace", "sku", "listing_id", "priority_level",
                   "effect_band", "evidence_hash", "status", "created_at", "updated_at",
                   "insight_key", "signal_key", "problem_type"):
        assert needed in cols, f"missing {needed}"


def test_indexes_match_pattern():
    ix = {i.name for i in OverstockSignal.__table__.indexes}
    assert ix == {
        "ix_overstock_signal_user_listing", "ix_overstock_signal_insight",
        "ix_overstock_signal_audit", "ix_overstock_signal_status",
        "ix_overstock_signal_category",
    }
    aix = {i.name for i in OverstockAudit.__table__.indexes}
    assert aix == {"ix_overstock_audit_user_listing", "ix_overstock_audit_status"}


def test_overstock_producer_registered_but_disabled():
    # Phase 7.1 wired the shadow producer: registered but DISABLED (never scheduled).
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "overstock" in by_key
    assert by_key["overstock"].enabled is False


def test_overstock_not_in_decision_feed():
    # Schema wired to NOTHING: the Decision Feed reads no overstock table yet.
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "overstock_signal" not in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "overstock" not in contours


def test_supply_and_siblings_untouched():
    # Overstock is the mirror of Supply, NOT a reuse — supply_signal stays its own engine;
    # Rating / Review / Review Velocity remain wired and independent.
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "supply_signal" in tables
    assert "rating_signal" in tables
    assert "review_signal" in tables
    assert "review_velocity_signal" in tables
