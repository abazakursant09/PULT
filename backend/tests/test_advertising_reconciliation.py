"""
Advertising A6 — reconciliation / lifecycle tests.

Re-audit semantics keyed on insight_key: active+same → unchanged; active+changed
→ updated; active+gone → resolved; dismissed+same → unchanged; dismissed+changed
→ reopened; resolved+reappeared → reopened; promoted_to_decision → unchanged;
not_evaluated never resolves; never two live signals per insight_key;
deterministic; marketplace-agnostic; core imports no MP clients.
"""
import ast
import asyncio
import inspect
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal

from services.advertising.snapshot import AdvertisingSnapshot, AdvertisingThresholds
from services.advertising.audit_persist import audit_and_persist
from services.advertising import reconciliation

T = AdvertisingThresholds(max_drr=20.0, min_revenue_for_signal=1000.0,
                          min_ad_spend_for_signal=100.0, low_margin_threshold=10.0,
                          low_stock_units=5, oos_risk_days=7.0)
_FIELDS = ("revenue", "net_profit", "ad_spend", "units_sold", "margin", "drr",
           "orders", "stock_units", "days_to_oos", "active_seo_problems",
           "critical_seo_problems", "category", "price_band", "margin_band", "thresholds")
T0 = datetime(2026, 6, 21)
IKEY = "adv_ad_destroying_profit:wildberries:SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _snap(*, mp="wildberries", net_profit=-500.0):
    # only ad_destroying_profit triggers (ops/seo unavailable so others not_evaluated)
    avail = {k: True for k in _FIELDS}
    avail.update({"stock_units": False, "days_to_oos": False, "active_seo_problems": False})
    return AdvertisingSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=T0, source="finance",
        revenue=10000.0, net_profit=net_profit, ad_spend=1000.0, orders=None, units_sold=20,
        margin=20.0, drr=10.0, stock_units=None, days_to_oos=None,
        active_seo_problems=None, critical_seo_problems=None,
        category=None, price_band=None, margin_band=None, thresholds=T, field_availability=avail)


async def _healthy(mp="wildberries"):
    return _snap(mp=mp, net_profit=2000.0)   # ad_destroying_profit not_triggered


async def _signal(db, uid):
    return (await db.execute(select(AdvertisingSignal).where(
        AdvertisingSignal.user_id == uid, AdvertisingSignal.insight_key == IKEY))).scalar_one_or_none()


async def _all(db, uid):
    return (await db.execute(select(AdvertisingSignal).where(AdvertisingSignal.user_id == uid))).scalars().all()


# ── 1. active + same evidence → unchanged ────────────────────────────────────

def test_active_same_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r1 = await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        assert r1.reconciliation.created == 1
        r2 = await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        assert r2.reconciliation.created == 0 and r2.reconciliation.unchanged >= 1
        assert (await _signal(db, uid)).status == "active"
        assert len(await _all(db, uid)) == 1
    _run(go())


# ── 2. active + changed evidence → updated ───────────────────────────────────

def test_active_changed_updated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-500.0), now=T0); await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-900.0), now=T0)
        await db.commit()
        assert r.reconciliation.updated == 1 and r.reconciliation.created == 0
        assert (await _signal(db, uid)).status == "active"
        assert len(await _all(db, uid)) == 1
    _run(go())


# ── 3. active + disappeared → resolved ───────────────────────────────────────

def test_active_disappeared_resolved():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=await _healthy(), now=T0)
        await db.commit()
        assert r.reconciliation.resolved == 1
        assert (await _signal(db, uid)).status == "resolved"
    _run(go())


# ── 4. dismissed + same → unchanged ──────────────────────────────────────────

def test_dismissed_same_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        s = await _signal(db, uid); s.status = "dismissed"; await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        assert r.reconciliation.reopened == 0 and r.reconciliation.unchanged >= 1
        assert (await _signal(db, uid)).status == "dismissed"
    _run(go())


# ── 5. dismissed + changed → reopened ────────────────────────────────────────

def test_dismissed_changed_reopened():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-500.0), now=T0); await db.commit()
        s = await _signal(db, uid); s.status = "dismissed"; await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-900.0), now=T0)
        await db.commit()
        assert r.reconciliation.reopened == 1
        assert (await _signal(db, uid)).status == "reopened"
        assert len(await _all(db, uid)) == 1
    _run(go())


# ── 6. resolved + reappeared → reopened ──────────────────────────────────────

def test_resolved_reappeared_reopened():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        await audit_and_persist(db, user_id=uid, snapshot=await _healthy(), now=T0); await db.commit()
        assert (await _signal(db, uid)).status == "resolved"
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        assert r.reconciliation.reopened == 1
        assert (await _signal(db, uid)).status == "reopened"
        assert len(await _all(db, uid)) == 1
    _run(go())


# ── 7. promoted_to_decision → unchanged ──────────────────────────────────────

def test_promoted_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        s = await _signal(db, uid); s.status = "promoted_to_decision"; await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-900.0), now=T0)
        await db.commit()
        assert r.reconciliation.unchanged >= 1
        assert (await _signal(db, uid)).status == "promoted_to_decision"  # Decision owns it
    _run(go())


# ── 8. not_evaluated does not resolve ────────────────────────────────────────

def test_not_evaluated_does_not_resolve():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        # next audit: ad_destroying_profit becomes not_evaluated (thresholds gone)
        snap = _snap()
        avail = dict(snap.field_availability); avail["thresholds"] = False
        from dataclasses import replace
        snap2 = replace(snap, thresholds=None, field_availability=avail)
        r = await audit_and_persist(db, user_id=uid, snapshot=snap2, now=T0); await db.commit()
        assert r.reconciliation.resolved == 0   # unknown ≠ fixed
        assert (await _signal(db, uid)).status == "active"
    _run(go())


# ── 9. duplicate live signal impossible ──────────────────────────────────────

def test_no_duplicate_live():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for _ in range(4):
            await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0); await db.commit()
        sigs = await _all(db, uid)
        assert len(sigs) == 1
        assert len([s for s in sigs if s.status in ("active", "reopened")]) == 1
    _run(go())


# ── 10. deterministic ────────────────────────────────────────────────────────

def test_deterministic():
    async def seq(uid):
        db = await _engine()
        a = (await audit_and_persist(db, user_id=uid, snapshot=_snap(), now=T0)).reconciliation
        await db.commit()
        b = (await audit_and_persist(db, user_id=uid, snapshot=await _healthy(), now=T0)).reconciliation
        await db.commit()
        return (a.created, a.unchanged), (b.resolved,)
    assert _run(seq(str(uuid.uuid4()))) == _run(seq(str(uuid.uuid4())))


# ── 11. marketplace agnostic ─────────────────────────────────────────────────

def test_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await audit_and_persist(db, user_id=uid, snapshot=_snap(mp=mp), now=T0); await db.commit()
            r = await audit_and_persist(db, user_id=uid, snapshot=_snap(mp=mp, net_profit=2000.0), now=T0)
            await db.commit()
            assert r.reconciliation.resolved == 1
    _run(go())


# ── 12. core imports no marketplace clients ──────────────────────────────────

def test_core_no_marketplace_client_imports():
    core_dir = Path(inspect.getfile(reconciliation)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog", "credential_vault")
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
