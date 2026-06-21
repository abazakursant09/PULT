"""
Review A6 — reconciliation / lifecycle tests.

Re-audit semantics keyed on insight_key (rev_<type>:<mp>:<sku>:<review_id>):
active+same → unchanged; active+changed → updated; active+gone → resolved;
dismissed+same → unchanged; dismissed+changed → reopened; resolved+reappeared →
reopened; promoted_to_decision → unchanged; not_evaluated never resolves; one
review → one signal; insight_key contains review_id; deterministic; agnostic;
core imports no MP clients.
"""
import ast
import asyncio
import inspect
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.review_signal import ReviewSignal

from services.review.snapshot import ReviewSnapshot
from services.review.audit_persist import audit_and_persist
from services.review import reconciliation
from services.review.safety_policy import OFF, MANUAL_ONLY, RISK, SAFE, MANUAL_APPROVAL

T0 = datetime(2026, 6, 21)
_FIELDS = ("rating", "text", "has_text", "answered", "answer_text", "answer_created_at",
           "product_name", "brand", "category", "safety_category")
IKEY = "rev_unanswered_negative_review:wildberries:SKU1:rev-1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _risk(*, mp="wildberries", rating=1, text=None, review_id="rev-1"):
    # RISK + unanswered → unanswered_negative_review triggers (no complaint marker unless text set)
    return ReviewSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=T0, source="reviews",
        review_id=review_id, rating=rating, text=text, has_text=bool(text), created_at=T0,
        answered=False, answer_text=None, answer_created_at=None, product_name="P", brand=None,
        category="Кухня", safety_category=RISK, allowed_modes=(OFF, MANUAL_ONLY),
        default_mode=MANUAL_ONLY, field_availability={k: True for k in _FIELDS})


def _answered():
    # same review now answered → unanswered_negative_review not_triggered
    return ReviewSnapshot(
        listing_id="L1", marketplace="wildberries", sku="SKU1", captured_at=T0, source="reviews",
        review_id="rev-1", rating=1, text=None, has_text=False, created_at=T0,
        answered=True, answer_text="спасибо", answer_created_at=T0, product_name="P", brand=None,
        category="Кухня", safety_category=RISK, allowed_modes=(OFF, MANUAL_ONLY),
        default_mode=MANUAL_ONLY, field_availability={k: True for k in _FIELDS})


async def _sig(db, uid):
    return (await db.execute(select(ReviewSignal).where(
        ReviewSignal.user_id == uid, ReviewSignal.insight_key == IKEY))).scalar_one_or_none()


async def _all(db, uid):
    return (await db.execute(select(ReviewSignal).where(ReviewSignal.user_id == uid))).scalars().all()


def test_create_active():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        assert r.reconciliation.created >= 1
        s = await _sig(db, uid)
        assert s.status == "active" and s.review_id == "rev-1"
        assert s.insight_key == IKEY   # contains review_id
    _run(go())


def test_unchanged_same_evidence():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        assert r.reconciliation.created == 0 and r.reconciliation.unchanged >= 1
        assert len(await _all(db, uid)) == 1
    _run(go())


def test_update_changed_evidence():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_risk(rating=1), now=T0); await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_risk(rating=2), now=T0); await db.commit()
        assert r.reconciliation.updated == 1
        assert (await _sig(db, uid)).status == "active" and len(await _all(db, uid)) == 1
    _run(go())


def test_resolved_on_not_triggered():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_answered(), now=T0); await db.commit()
        assert r.reconciliation.resolved == 1
        assert (await _sig(db, uid)).status == "resolved"
    _run(go())


def test_reopened_after_resolved():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        await audit_and_persist(db, user_id=uid, snapshot=_answered(), now=T0); await db.commit()
        assert (await _sig(db, uid)).status == "resolved"
        r = await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        assert r.reconciliation.reopened == 1 and (await _sig(db, uid)).status == "reopened"
        # one signal PER insight_key: the negative key has exactly one row (reopened)
        neg = [s for s in await _all(db, uid) if s.insight_key == IKEY]
        assert len(neg) == 1
    _run(go())


def test_dismissed_same_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        s = await _sig(db, uid); s.status = "dismissed"; await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        assert r.reconciliation.reopened == 0 and r.reconciliation.unchanged >= 1
        assert (await _sig(db, uid)).status == "dismissed"
    _run(go())


def test_dismissed_changed_reopened():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_risk(rating=1), now=T0); await db.commit()
        s = await _sig(db, uid); s.status = "dismissed"; await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_risk(rating=2), now=T0); await db.commit()
        assert r.reconciliation.reopened == 1 and (await _sig(db, uid)).status == "reopened"
    _run(go())


def test_promoted_respected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        s = await _sig(db, uid); s.status = "promoted_to_decision"; await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_risk(rating=2), now=T0); await db.commit()
        assert r.reconciliation.unchanged >= 1
        assert (await _sig(db, uid)).status == "promoted_to_decision"
    _run(go())


def test_not_evaluated_never_resolves():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        snap = _risk()
        avail = dict(snap.field_availability); avail["answered"] = False; avail["safety_category"] = False
        snap2 = replace(snap, field_availability=avail)   # rule → not_evaluated
        r = await audit_and_persist(db, user_id=uid, snapshot=snap2, now=T0); await db.commit()
        assert r.reconciliation.resolved == 0
        assert (await _sig(db, uid)).status == "active"
    _run(go())


def test_one_review_one_signal_over_audits():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for _ in range(4):
            await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0); await db.commit()
        sigs = await _all(db, uid)
        assert len(sigs) == 1
        assert len([s for s in sigs if s.status in ("active", "reopened")]) == 1
    _run(go())


def test_deterministic():
    async def seq(uid):
        db = await _engine()
        a = (await audit_and_persist(db, user_id=uid, snapshot=_risk(), now=T0)).reconciliation
        await db.commit()
        b = (await audit_and_persist(db, user_id=uid, snapshot=_answered(), now=T0)).reconciliation
        await db.commit()
        return (a.created, a.unchanged), (b.resolved,)
    assert _run(seq(str(uuid.uuid4()))) == _run(seq(str(uuid.uuid4())))


def test_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await audit_and_persist(db, user_id=uid, snapshot=_risk(mp=mp), now=T0); await db.commit()
            sig = (await db.execute(select(ReviewSignal).where(ReviewSignal.user_id == uid))).scalars().first()
            assert f":{mp}:SKU1:rev-1" in sig.insight_key
    _run(go())


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
