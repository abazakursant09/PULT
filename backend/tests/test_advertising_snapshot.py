"""
Advertising A3 — money snapshot contract tests.

Snapshot built from ImportedFinanceRow (DRR/margin computed); missing finance →
AdvertisingDataUnavailable; missing thresholds → None + availability False; no
hardcoded defaults (AdvertisingThresholds requires every value); marketplace
agnostic; rules not yet implemented; core imports no marketplace clients.
"""
import ast
import asyncio
import inspect
import uuid
from pathlib import Path

import pytest

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow

from services.advertising.snapshot import (
    AdvertisingSnapshot, AdvertisingThresholds, AdvertisingDataUnavailable,
)
from services.advertising import internal_source
from services.advertising.internal_source import build_snapshot_from_finance

THRESH = dict(max_drr=20.0, min_revenue_for_signal=1000.0, min_ad_spend_for_signal=100.0,
              low_margin_threshold=10.0, low_stock_units=5, oos_risk_days=7.0)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_finance(db, uid, *, marketplace="wb", sku="SKU1",
                        revenue=10000.0, net_profit=500.0, ad_spend=4000.0, quantity=20):
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace, sku=sku,
                              revenue=revenue, net_profit=net_profit, ad_spend=ad_spend,
                              quantity=quantity))
    await db.flush()


# ── 1. snapshot from finance ─────────────────────────────────────────────────

def test_snapshot_from_finance():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid)
        snap = await build_snapshot_from_finance(
            db, user_id=uid, marketplace="wb", sku="SKU1",
            thresholds=AdvertisingThresholds(**THRESH))
        assert isinstance(snap, AdvertisingSnapshot)
        assert snap.revenue == 10000.0 and snap.ad_spend == 4000.0 and snap.net_profit == 500.0
        assert snap.units_sold == 20 and snap.source == "finance"
        assert snap.drr == 40.0 and snap.margin == 5.0   # 4000/10000, 500/10000
        assert snap.field_availability["drr"] is True and snap.field_availability["thresholds"] is True
    _run(go())


# ── 2. missing finance → AdvertisingDataUnavailable ──────────────────────────

def test_missing_finance_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_snapshot_from_finance(db, user_id=uid, marketplace="wb", sku="NOPE",
                                                thresholds=AdvertisingThresholds(**THRESH))
        assert isinstance(res, AdvertisingDataUnavailable) and res.reason == "finance_missing"
        # no db / no sku also honest
        assert (await build_snapshot_from_finance(None, user_id=uid, marketplace="wb", sku="X")).reason == "no_db_context"
        assert (await build_snapshot_from_finance(db, user_id=uid, marketplace="wb", sku=None)).reason == "insufficient_data"
    _run(go())


# ── 3. missing thresholds → None + availability False ────────────────────────

def test_missing_thresholds():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid)
        snap = await build_snapshot_from_finance(db, user_id=uid, marketplace="wb", sku="SKU1")
        assert isinstance(snap, AdvertisingSnapshot)
        assert snap.thresholds is None
        assert snap.field_availability["thresholds"] is False
        # money still computed; operations/seo honestly unavailable
        assert snap.drr == 40.0
        assert snap.field_availability["stock_units"] is False
        assert snap.field_availability["orders"] is False   # finance lacks order count
    _run(go())


# ── 4. no hardcoded defaults ─────────────────────────────────────────────────

def test_thresholds_no_defaults():
    with pytest.raises(TypeError):
        AdvertisingThresholds()  # type: ignore[call-arg]
    # core builder source contains no magic limit numbers
    src = inspect.getsource(internal_source)
    assert "max_drr" not in src and "low_margin_threshold" not in src  # thresholds only via param


# ── 5. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await _seed_finance(db, uid, marketplace=mp)
            snap = await build_snapshot_from_finance(
                db, user_id=uid, marketplace=mp, sku="SKU1",
                thresholds=AdvertisingThresholds(**THRESH))
            assert isinstance(snap, AdvertisingSnapshot)
            assert snap.marketplace == mp and snap.drr == 40.0
    _run(go())


# ── 6. reconciliation not implemented yet (persist/builder landed in A5) ─────

def test_no_reconciliation_yet():
    adv_dir = Path(inspect.getfile(internal_source)).parent
    assert not (adv_dir / "reconciliation.py").exists(), "reconciliation is A6, not yet"


# ── 7. core imports no marketplace clients ───────────────────────────────────

def test_core_no_marketplace_client_imports():
    adv_dir = Path(inspect.getfile(internal_source)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog", "credential_vault")
    offenders = []
    for path in adv_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for bad in forbidden:
                    if bad in m:
                        offenders.append(f"{path.name}:{bad}")
    assert not offenders, offenders
