"""
Growth A3 — Opportunity Snapshot tests.

Read-only aggregation of EXISTING PULT data (finance / seo / reviews / operations
/ context) into one GrowthSnapshot. Honest availability map; no fake defaults; no
external API; no rules/signals yet. Finance is the anchor (no finance →
GrowthDataUnavailable). days_to_oos is never fabricated (no forecast).
"""
import ast
import asyncio
import inspect
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.product import Product
from models.seo_signal import SeoSignal
from models.review_signal import ReviewSignal

from services.growth import internal_source
from services.growth.internal_source import build_snapshot_from_internal
from services.growth.snapshot import GrowthSnapshot, GrowthDataUnavailable

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_finance(db, uid, *, mp="wildberries", sku="SKU1",
                        revenue=10000.0, net_profit=1500.0, ad_spend=2000.0, quantity=40):
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=mp, sku=sku,
                              revenue=revenue, net_profit=net_profit, ad_spend=ad_spend,
                              quantity=quantity))
    await db.flush()


# ── full build across all internal sources ───────────────────────────────────

def test_snapshot_build_full():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); lid = "seo:wildberries:SKU1"
        await _seed_finance(db, uid)
        db.add(ImportedProductRow(import_id="imp1", user_id=uid, marketplace="wildberries",
                                  sku="SKU1", stock=12))
        db.add(Product(user_id=uid, name="P", marketplace="wildberries", sku="SKU1", category="Кухня"))
        db.add(SeoSignal(audit_id=str(uuid.uuid4()), user_id=uid, listing_id=lid,
                         signal_key="seo_x", problem_type="x", status="active", priority_level="critical"))
        db.add(ReviewSignal(audit_id=str(uuid.uuid4()), user_id=uid, signal_key="rev_x",
                            problem_type="x", status="active", safety_category="RISK"))
        await db.commit()

        snap = await build_snapshot_from_internal(db, user_id=uid, marketplace="wildberries",
                                                  sku="SKU1", listing_id=lid, now=T0)
        assert isinstance(snap, GrowthSnapshot)
        assert snap.source == "internal" and snap.captured_at == T0
        assert snap.revenue == 10000.0 and snap.net_profit == 1500.0 and snap.units_sold == 40
        assert round(snap.margin, 2) == 15.0 and round(snap.drr, 2) == 20.0
        assert snap.margin_band == "medium"
        assert snap.active_seo_signals == 1 and snap.critical_seo_signals == 1
        assert snap.active_review_signals == 1 and snap.risk_review_signals == 1
        assert snap.stock_units == 12 and snap.category == "Кухня"
        assert snap.days_to_oos is None
    _run(go())


# ── unavailable data ─────────────────────────────────────────────────────────

def test_finance_missing():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        snap = await build_snapshot_from_internal(db, user_id=uid, marketplace="ozon", sku="NOPE")
        assert isinstance(snap, GrowthDataUnavailable) and snap.reason == "finance_missing"
    _run(go())


def test_insufficient_data_no_sku():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        snap = await build_snapshot_from_internal(db, user_id=uid, marketplace="ozon", sku=None)
        assert isinstance(snap, GrowthDataUnavailable) and snap.reason == "insufficient_data"
    _run(go())


def test_no_db_context():
    snap = _run(build_snapshot_from_internal(None, user_id="u", marketplace="wb", sku="S"))
    assert isinstance(snap, GrowthDataUnavailable) and snap.reason == "no_db_context"


# ── availability map honesty ─────────────────────────────────────────────────

def test_availability_map():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        # no product row, no listing_id → stock/category/seo unavailable
        snap = await build_snapshot_from_internal(db, user_id=uid, marketplace="wildberries", sku="SKU1")
        fa = snap.field_availability
        assert fa["revenue"] and fa["margin"] and fa["drr"]
        assert fa["active_review_signals"] and fa["risk_review_signals"]
        assert fa["stock_units"] is False and fa["category"] is False
        assert fa["active_seo_signals"] is False     # no listing_id
        assert fa["days_to_oos"] is False            # never forecast
    _run(go())


# ── no fake defaults: missing data stays None, not 0 ─────────────────────────

def test_no_fake_defaults():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        snap = await build_snapshot_from_internal(db, user_id=uid, marketplace="wildberries", sku="SKU1")
        assert snap.stock_units is None        # not 0
        assert snap.category is None            # not ""
        assert snap.days_to_oos is None
        assert snap.active_seo_signals is None  # not 0 when not scoped
    _run(go())


# ── marketplace agnostic ─────────────────────────────────────────────────────

def test_marketplace_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await _seed_finance(db, uid, mp=mp); await db.commit()
            snap = await build_snapshot_from_internal(db, user_id=uid, marketplace=mp, sku="SKU1")
            assert isinstance(snap, GrowthSnapshot) and snap.marketplace == mp
            assert snap.revenue == 10000.0
    _run(go())


# ── no external API in the growth service ────────────────────────────────────

def test_no_external_api_imports():
    core_dir = Path(inspect.getfile(internal_source)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "requests", "httpx",
                 "aiohttp", "credential_vault", "openai", "anthropic")
    offenders = []
    for path in core_dir.rglob("*.py"):
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


# ── no persist / signal-builder / API yet (rule engine lands in A4) ──────────

def test_no_persist_or_api_yet():
    core_dir = Path(inspect.getfile(internal_source)).parent
    names = {p.name for p in core_dir.glob("*.py")}
    # snapshot/rules/persist/signal_builder/reconciliation allowed; API (A7) is not
    for forbidden in ("router.py", "api.py"):
        assert forbidden not in names, f"must not ship {forbidden} yet"
