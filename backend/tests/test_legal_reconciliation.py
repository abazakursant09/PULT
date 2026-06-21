"""
Legal A6 — reconciliation / lifecycle tests.

After each audit run the single live signal per insight_key is lifecycled:
detected+none → active; detected+live → unchanged/updated; detected+resolved/
dismissed → reopened; not_detected+live → resolved (cautious reason, never
"compliant"); not_evaluated → never resolves/reopens. Detection layer stays
append-only; no duplicate live signals.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_product import ImportedProductRow
from models.legal_audit import LegalAudit
from models.legal_finding import LegalFinding
from models.legal_rule_evaluation import LegalRuleEvaluation
from models.legal_signal import LegalSignal

from services.legal.persist import audit_and_persist

T0 = datetime(2026, 6, 21)
T1 = datetime(2026, 6, 22)
T2 = datetime(2026, 6, 23)
IKEY = "legal_content_claim_risk:wildberries:SKU1"
RISKY = "Крем лечит 100%"
CLEAN = "Чайник электрический синий"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _set_title(db, uid, title):
    """Make the subject's content_text be `title` (or absent when None)."""
    await db.execute(delete(ImportedProductRow).where(ImportedProductRow.user_id == uid))
    if title is not None:
        db.add(ImportedProductRow(import_id="imp1", user_id=uid, marketplace="wildberries",
                                  sku="SKU1", title=title))
    await db.commit()


async def _audit(db, uid, *, now):
    r = await audit_and_persist(db, seller_id=uid, marketplace="wildberries",
                                subject_type="product", subject_ref="SKU1", sku="SKU1", now=now)
    await db.commit()
    return r


async def _sig(db, uid):
    return (await db.execute(select(LegalSignal).where(
        LegalSignal.user_id == uid, LegalSignal.insight_key == IKEY))).scalar_one_or_none()


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


# ── active remains active when detected again ────────────────────────────────

def test_active_remains_active_on_redetect():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, RISKY)
        r1 = await _audit(db, uid, now=T0)
        r2 = await _audit(db, uid, now=T1)
        assert r1.reconciliation.created == 1
        assert r2.reconciliation.unchanged >= 1 and r2.reconciliation.created == 0
        s = await _sig(db, uid)
        assert s.status == "active"
        assert len([x for x in (await db.execute(select(LegalSignal).where(
            LegalSignal.insight_key == IKEY))).scalars().all()]) == 1
    _run(go())


# ── active/reopened resolves on not_detected (cautious reason, not compliant) ─

def test_resolves_on_not_detected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, RISKY); await _audit(db, uid, now=T0)
        await _set_title(db, uid, CLEAN)
        r = await _audit(db, uid, now=T1)
        assert r.reconciliation.resolved == 1
        s = await _sig(db, uid)
        assert s.status == "resolved"
        assert s.lifecycle_reason == "risk_not_detected_in_latest_audit"
        assert "compliant" not in (s.lifecycle_reason or "").lower()
    _run(go())


# ── not_evaluated does not resolve ───────────────────────────────────────────

def test_not_evaluated_does_not_resolve():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, RISKY); await _audit(db, uid, now=T0)
        await _set_title(db, uid, None)   # no text → content_claim_risk not_evaluated
        r = await _audit(db, uid, now=T1)
        assert r.reconciliation.resolved == 0
        assert (await _sig(db, uid)).status == "active"
    _run(go())


# ── resolved reopens on detected ─────────────────────────────────────────────

def test_resolved_reopens_on_detected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, RISKY); await _audit(db, uid, now=T0)
        await _set_title(db, uid, CLEAN); await _audit(db, uid, now=T1)
        assert (await _sig(db, uid)).status == "resolved"
        await _set_title(db, uid, RISKY)
        r = await _audit(db, uid, now=T2)
        assert r.reconciliation.reopened == 1
        s = await _sig(db, uid)
        assert s.status == "reopened" and s.lifecycle_reason == "risk_detected_again_in_latest_audit"
        assert len((await db.execute(select(LegalSignal).where(
            LegalSignal.insight_key == IKEY))).scalars().all()) == 1
    _run(go())


# ── dismissed reopens on detected ────────────────────────────────────────────

def test_dismissed_reopens_on_detected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, RISKY); await _audit(db, uid, now=T0)
        s = await _sig(db, uid); s.status = "dismissed"; await db.commit()
        r = await _audit(db, uid, now=T1)
        assert r.reconciliation.reopened == 1
        assert (await _sig(db, uid)).status == "reopened"
    _run(go())


# ── acknowledged stays on detected, resolves on not_detected ────────────────

def test_acknowledged_lifecycle():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, RISKY); await _audit(db, uid, now=T0)
        s = await _sig(db, uid); s.status = "acknowledged"; await db.commit()
        r2 = await _audit(db, uid, now=T1)          # detected again
        assert r2.reconciliation.unchanged >= 1
        assert (await _sig(db, uid)).status == "acknowledged"   # not duplicated, not reset
        await _set_title(db, uid, CLEAN)
        r3 = await _audit(db, uid, now=T2)          # no longer detected
        assert r3.reconciliation.resolved == 1
        assert (await _sig(db, uid)).status == "resolved"
    _run(go())


# ── no duplicate live signals across many runs ───────────────────────────────

def test_no_duplicate_live_signals():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, RISKY)
        for t in (T0, T1, T2):
            await _audit(db, uid, now=t)
        rows = (await db.execute(select(LegalSignal).where(
            LegalSignal.insight_key == IKEY))).scalars().all()
        assert len(rows) == 1
        assert len([x for x in rows if x.status in ("active", "acknowledged", "reopened")]) == 1
    _run(go())


# ── detection layer remains append-only ──────────────────────────────────────

def test_detection_append_only():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, RISKY)
        await _audit(db, uid, now=T0); await _audit(db, uid, now=T1)
        assert await _count(db, LegalAudit) == 2
        assert await _count(db, LegalRuleEvaluation) == 12
        assert await _count(db, LegalFinding) == 2          # one per run (detected)
        assert len((await db.execute(select(LegalSignal))).scalars().all()) == 1
    _run(go())


# ── lifecycle updated_at changes on update ───────────────────────────────────

def test_updated_at_changes_on_update():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _set_title(db, uid, "лечит"); await _audit(db, uid, now=T0)
        first = (await _sig(db, uid)).updated_at
        await _set_title(db, uid, "лечит 100% оригинал")   # changed evidence
        r = await _audit(db, uid, now=T1)
        assert r.reconciliation.updated == 1
        s = await _sig(db, uid)
        assert s.updated_at == T1 and s.updated_at != first and s.status == "active"
    _run(go())
