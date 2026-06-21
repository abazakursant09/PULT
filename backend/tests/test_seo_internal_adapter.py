"""
SEO A8 — real internal CardSnapshot adapter tests.

The WB adapter builds a snapshot from existing PULT data (ProductListing +
PhysicalProduct), no external API. Whatever PULT does not store is honestly
unavailable → SEO rules return not_evaluated (never a false "ok"). Other adapters
stay SnapshotUnavailable; the agnostic registry is intact; SEO core imports no
marketplace clients.
"""
import ast
import asyncio
import inspect
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.seo_rule_evaluation import SeoRuleEvaluation
from models.seo_signal import SeoSignal

from services.seo.card_snapshot import CardSnapshot
from services.seo.adapter import SnapshotUnavailable
from services.seo.registry import get_seo_adapter, registered_marketplaces
from services.seo import internal_source
from services.seo.engine import evaluate_snapshot
from services.seo.evaluation import RuleResult
from services.seo.audit_persist import audit_and_persist


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_listing(db, uid, *, marketplace="wb", title="Хороший товар для дома", brand="Acme"):
    phys = PhysicalProduct(id=str(uuid.uuid4()), user_id=uid, title=title, brand=brand)
    db.add(phys)
    listing = ProductListing(id=str(uuid.uuid4()), physical_product_id=phys.id, user_id=uid,
                             marketplace=marketplace, external_id="SKU1", title=title)
    db.add(listing)
    await db.flush()
    return listing


# ── build_snapshot from real internal data ───────────────────────────────────

def test_build_snapshot_from_internal_data():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing = await _seed_listing(db, uid)
        snap = await get_seo_adapter("wb").build_snapshot(listing_id=listing.id, db=db)
        assert isinstance(snap, CardSnapshot)
        assert snap.title == "Хороший товар для дома" and snap.brand == "Acme"
        assert snap.sku == "SKU1" and snap.marketplace == "wb" and snap.source == "internal"
        assert snap.constraints is None                  # never invented
    _run(go())


# ── missing fields → field_availability False (not faked "ok") ───────────────

def test_missing_fields_marked_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing = await _seed_listing(db, uid)
        snap = await get_seo_adapter("wb").build_snapshot(listing_id=listing.id, db=db)
        fa = snap.field_availability
        assert fa["title"] is True and fa["brand"] is True
        for missing in ("description", "attributes", "category_schema", "category_path",
                        "expected_category_path", "variants", "media", "constraints"):
            assert fa[missing] is False
    _run(go())


# ── missing constraints/fields → rules NOT_EVALUATED, not NOT_TRIGGERED ───────

def test_thin_snapshot_yields_not_evaluated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing = await _seed_listing(db, uid)
        snap = await get_seo_adapter("wb").build_snapshot(listing_id=listing.id, db=db)
        results = evaluate_snapshot(snap)
        # every rule is not_evaluated — we lack the fields to assess SEO honestly
        assert all(r.result == RuleResult.NOT_EVALUATED for r in results)
        by = {r.problem_type: r for r in results}
        # title rules: title present but no constraints → not_evaluated (NOT not_triggered)
        assert by["title_too_short"].result == RuleResult.NOT_EVALUATED
        assert "constraints" in by["title_too_short"].reason
        # description absent from source → not_evaluated, NOT a false "description_missing"
        assert by["description_missing"].result == RuleResult.NOT_EVALUATED
    _run(go())


# ── persisted audit: 12 not_evaluated, 0 problems, 0 signals ─────────────────

def test_internal_audit_persists_honestly():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing = await _seed_listing(db, uid)
        snap = await get_seo_adapter("wb").build_snapshot(listing_id=listing.id, db=db)
        res = await audit_and_persist(db, user_id=uid, snapshot=snap)
        await db.commit()
        assert res.total_problems == 0 and res.total_not_evaluated == 12
        ledger = (await db.execute(select(SeoRuleEvaluation).where(
            SeoRuleEvaluation.audit_id == res.audit_id))).scalars().all()
        assert len(ledger) == 12 and all(r.result == "not_evaluated" for r in ledger)
        sigs = (await db.execute(select(SeoSignal))).scalars().all()
        assert sigs == []   # nothing claimed → no signals
    _run(go())


# ── honest failure modes ─────────────────────────────────────────────────────

def test_listing_not_found_and_no_db():
    async def go():
        db = await _engine()
        miss = await get_seo_adapter("wb").build_snapshot(listing_id="nope", db=db)
        assert isinstance(miss, SnapshotUnavailable) and miss.reason == "listing_not_found"
        nodb = await get_seo_adapter("wb").build_snapshot(listing_id="x", db=None)
        assert isinstance(nodb, SnapshotUnavailable) and nodb.reason == "no_db_context"
    _run(go())


# ── multi-marketplace architecture intact ────────────────────────────────────

def test_registry_intact_others_still_stub():
    async def go():
        assert registered_marketplaces() == {"wildberries", "ozon", "yandex"}
        for mp in ("ozon", "yandex"):
            res = await get_seo_adapter(mp).build_snapshot(listing_id="L1", db=None)
            assert isinstance(res, SnapshotUnavailable)
            assert res.reason == "adapter_not_implemented"
    _run(go())


# ── SEO core imports no marketplace clients ──────────────────────────────────

def test_core_no_marketplace_client_imports():
    core_dir = Path(inspect.getfile(internal_source)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog", "credential_vault")
    offenders = []
    for path in core_dir.rglob("*.py"):
        if "adapters" in path.parts:
            continue
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
