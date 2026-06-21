"""
Growth A6 — reconciliation / lifecycle tests.

Re-audit semantics keyed on insight_key (growth_<type>:<mp>:<sku>):
active+same → unchanged; active+changed → updated; active+gone → resolved;
dismissed+same → unchanged; dismissed+changed → reopened; resolved+reappeared →
reopened; promoted_to_decision → unchanged; not_evaluated never resolves; four
audits → one signal; deterministic; marketplace agnostic; core imports no external
API clients.
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
from models.growth_signal import GrowthSignal

from services.growth.snapshot import GrowthSnapshot
from services.growth.audit_persist import audit_and_persist
from services.growth import reconciliation
from services.growth.rules import GrowthThresholds

T0 = datetime(2026, 6, 21)
_FA_KEYS = ("revenue", "net_profit", "margin", "units_sold", "ad_spend", "drr",
            "active_seo_signals", "critical_seo_signals", "active_review_signals",
            "risk_review_signals", "stock_units", "days_to_oos", "category", "margin_band")
FULL_TH = GrowthThresholds(low_stock_units=5, min_revenue_for_growth_signal=1000.0,
                           min_net_profit_for_growth_signal=100.0)
IKEY = "growth_profitable_ad_candidate:wildberries:SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _snap(*, mp="wildberries", net_profit=2000.0, ad_spend=0.0, margin_band="high", avail=None):
    fa = {k: True for k in _FA_KEYS}
    if avail:
        fa.update(avail)
    return GrowthSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=T0, source="internal",
        revenue=10000.0, net_profit=net_profit, margin=20.0, units_sold=40,
        ad_spend=ad_spend, drr=0.0, active_seo_signals=2, critical_seo_signals=1,
        active_review_signals=1, risk_review_signals=1, stock_units=3, days_to_oos=None,
        category="Кухня", margin_band=margin_band, field_availability=fa)


async def _audit(db, uid, snap, th=FULL_TH):
    r = await audit_and_persist(db, user_id=uid, snapshot=snap, thresholds=th, now=T0)
    await db.commit()
    return r


async def _sig(db, uid, ikey=IKEY):
    return (await db.execute(select(GrowthSignal).where(
        GrowthSignal.user_id == uid, GrowthSignal.insight_key == ikey))).scalar_one_or_none()


async def _all(db, uid):
    return (await db.execute(select(GrowthSignal).where(GrowthSignal.user_id == uid))).scalars().all()


def test_create_active():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await _audit(db, uid, _snap())
        assert r.reconciliation.created >= 1
        s = await _sig(db, uid)
        assert s.status == "active" and s.insight_key == IKEY
    _run(go())


def test_unchanged_same_evidence():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _audit(db, uid, _snap())
        r = await _audit(db, uid, _snap())
        assert r.reconciliation.created == 0 and r.reconciliation.unchanged >= 1
        assert len([s for s in await _all(db, uid) if s.insight_key == IKEY]) == 1
    _run(go())


def test_updated_changed_evidence():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _audit(db, uid, _snap(net_profit=2000.0))
        r = await _audit(db, uid, _snap(net_profit=2500.0))
        assert r.reconciliation.updated >= 1
        assert (await _sig(db, uid)).status == "active"
    _run(go())


def test_resolved_on_not_triggered():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _audit(db, uid, _snap())
        r = await _audit(db, uid, _snap(ad_spend=500.0))   # profitable_ad now not_triggered
        assert r.reconciliation.resolved >= 1
        assert (await _sig(db, uid)).status == "resolved"
    _run(go())


def test_reopened_after_resolved():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _audit(db, uid, _snap())
        await _audit(db, uid, _snap(ad_spend=500.0))
        assert (await _sig(db, uid)).status == "resolved"
        r = await _audit(db, uid, _snap())
        assert r.reconciliation.reopened >= 1 and (await _sig(db, uid)).status == "reopened"
        assert len([s for s in await _all(db, uid) if s.insight_key == IKEY]) == 1
    _run(go())


def test_dismissed_same_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _audit(db, uid, _snap())
        s = await _sig(db, uid); s.status = "dismissed"; await db.commit()
        r = await _audit(db, uid, _snap())
        assert r.reconciliation.reopened == 0 and r.reconciliation.unchanged >= 1
        assert (await _sig(db, uid)).status == "dismissed"
    _run(go())


def test_dismissed_changed_reopened():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _audit(db, uid, _snap(net_profit=2000.0))
        s = await _sig(db, uid); s.status = "dismissed"; await db.commit()
        r = await _audit(db, uid, _snap(net_profit=2500.0))
        assert r.reconciliation.reopened >= 1 and (await _sig(db, uid)).status == "reopened"
    _run(go())


def test_promoted_respected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _audit(db, uid, _snap())
        s = await _sig(db, uid); s.status = "promoted_to_decision"; await db.commit()
        r = await _audit(db, uid, _snap(net_profit=2500.0))
        assert r.reconciliation.unchanged >= 1
        assert (await _sig(db, uid)).status == "promoted_to_decision"
    _run(go())


def test_not_evaluated_never_resolves():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _audit(db, uid, _snap())
        # drop min_net_profit threshold → profitable_ad becomes not_evaluated
        th = GrowthThresholds(low_stock_units=5, min_revenue_for_growth_signal=1000.0)
        r = await _audit(db, uid, _snap(), th=th)
        assert r.reconciliation.resolved == 0
        assert (await _sig(db, uid)).status == "active"
    _run(go())


def test_four_audits_one_signal():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for _ in range(4):
            await _audit(db, uid, _snap())
        per_key = [s for s in await _all(db, uid) if s.insight_key == IKEY]
        assert len(per_key) == 1
        assert per_key[0].status in ("active", "reopened")
    _run(go())


def test_deterministic():
    async def seq(uid):
        db = await _engine()
        a = (await _audit(db, uid, _snap())).reconciliation
        b = (await _audit(db, uid, _snap(ad_spend=500.0))).reconciliation
        return (a.created, a.unchanged), (b.resolved,)
    assert _run(seq(str(uuid.uuid4()))) == _run(seq(str(uuid.uuid4())))


def test_marketplace_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await _audit(db, uid, _snap(mp=mp))
            sig = (await db.execute(select(GrowthSignal).where(
                GrowthSignal.user_id == uid,
                GrowthSignal.insight_key == f"growth_profitable_ad_candidate:{mp}:SKU1"))).scalar_one_or_none()
            assert sig is not None and sig.status == "active"
    _run(go())


def test_core_no_external_api_imports():
    core_dir = Path(inspect.getfile(reconciliation)).parent
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
