"""
SEO A6 — reconciliation / lifecycle tests.

Re-audit semantics keyed on insight_key: active+same → unchanged; active+gone →
resolved; dismissed+same → unchanged; dismissed+changed → reopened; resolved+
reappeared → reopened; never two live signals for one insight_key; insight_key
canonical; deterministic; marketplace-agnostic. A NOT_EVALUATED rule never
resolves a signal.
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
from models.seo_signal import SeoSignal

from services.seo.card_snapshot import (
    CardSnapshot, SeoConstraints, CategorySchema, CardAttribute, CardMedia,
)
from services.seo.audit_persist import audit_and_persist

CONS = SeoConstraints(title_min_len=20, title_max_len=100, description_min_len=200,
                      media_min_images=3, attribute_fill_rate_threshold=0.6,
                      content_completeness_threshold=0.7)
_ALL = {"title", "description", "attributes", "media", "category_schema",
        "category_path", "expected_category_path", "variants", "constraints"}
T0 = datetime(2026, 6, 21)
IKEY = "seo_media_below_minimum:wildberries:SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _snap(*, mp="wildberries", images=0):
    # only media_below_minimum can trigger (everything else healthy)
    return CardSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=T0, source="api",
        title="x" * 40, description="d" * 300, brand="b",
        category_path=("root", "cat"), expected_category_path=("root", "cat"),
        category_schema=CategorySchema(),
        attributes=(CardAttribute("colour", "red", True),), variants=("size",),
        media=CardMedia(image_count=images), constraints=CONS,
        field_availability={k: True for k in _ALL})


async def _signal(db, uid):
    return (await db.execute(select(SeoSignal).where(
        SeoSignal.user_id == uid, SeoSignal.insight_key == IKEY))).scalar_one_or_none()


async def _all_signals(db, uid):
    return (await db.execute(select(SeoSignal).where(SeoSignal.user_id == uid))).scalars().all()


# ── 1. active + same problem → unchanged ─────────────────────────────────────

def test_active_same_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r1 = await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0)
        await db.commit()
        assert r1.reconciliation.created == 1
        r2 = await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0)
        await db.commit()
        assert r2.reconciliation.created == 0 and r2.reconciliation.unchanged >= 1
        sig = await _signal(db, uid)
        assert sig.status == "active"
        assert len(await _all_signals(db, uid)) == 1   # no duplicate
    _run(go())


# ── 2. active + disappeared → resolved ───────────────────────────────────────

def test_active_disappeared_resolved():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0); await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(images=4), now=T0)  # fixed
        await db.commit()
        assert r.reconciliation.resolved == 1
        assert (await _signal(db, uid)).status == "resolved"
    _run(go())


# ── 3. dismissed + same evidence → unchanged ─────────────────────────────────

def test_dismissed_same_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0); await db.commit()
        sig = await _signal(db, uid); sig.status = "dismissed"; await db.commit()
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0)  # same evidence
        await db.commit()
        assert r.reconciliation.reopened == 0 and r.reconciliation.unchanged >= 1
        assert (await _signal(db, uid)).status == "dismissed"
    _run(go())


# ── 4. dismissed + changed evidence → reopened ───────────────────────────────

def test_dismissed_changed_reopened():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0); await db.commit()
        sig = await _signal(db, uid); sig.status = "dismissed"; await db.commit()
        # still triggered (1 < 3) but evidence image_count changed 0 → 1
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(images=1), now=T0)
        await db.commit()
        assert r.reconciliation.reopened == 1
        assert (await _signal(db, uid)).status == "reopened"
        assert len(await _all_signals(db, uid)) == 1
    _run(go())


# ── 5. resolved + reappeared → reopened ──────────────────────────────────────

def test_resolved_reappeared_reopened():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0); await db.commit()
        await audit_and_persist(db, user_id=uid, snapshot=_snap(images=4), now=T0); await db.commit()
        assert (await _signal(db, uid)).status == "resolved"
        r = await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0)  # back
        await db.commit()
        assert r.reconciliation.reopened == 1
        assert (await _signal(db, uid)).status == "reopened"
        assert len(await _all_signals(db, uid)) == 1
    _run(go())


# ── 6. duplicate active impossible ───────────────────────────────────────────

def test_no_duplicate_live_signal():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for _ in range(4):
            await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0)
            await db.commit()
        sigs = await _all_signals(db, uid)
        assert len(sigs) == 1
        live = [s for s in sigs if s.status in ("active", "reopened")]
        assert len(live) == 1
    _run(go())


# ── 7. insight_key canonical ─────────────────────────────────────────────────

def test_insight_key_canonical():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0); await db.commit()
        assert (await _signal(db, uid)).insight_key == "seo_media_below_minimum:wildberries:SKU1"
    _run(go())


# ── 8. deterministic ─────────────────────────────────────────────────────────

def test_deterministic_reconciliation():
    async def seq(uid):
        db = await _engine()
        a = (await audit_and_persist(db, user_id=uid, snapshot=_snap(images=0), now=T0)).reconciliation
        await db.commit()
        b = (await audit_and_persist(db, user_id=uid, snapshot=_snap(images=4), now=T0)).reconciliation
        await db.commit()
        return (a.created, a.unchanged, a.resolved, a.reopened), (b.created, b.resolved)

    def go():
        u = str(uuid.uuid4())
        assert _run(seq(u)) == _run(seq(str(uuid.uuid4())))
    go()


# ── 9. marketplace agnostic ──────────────────────────────────────────────────

def test_agnostic_lifecycle():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await audit_and_persist(db, user_id=uid, snapshot=_snap(mp=mp, images=0), now=T0); await db.commit()
            r = await audit_and_persist(db, user_id=uid, snapshot=_snap(mp=mp, images=4), now=T0)
            await db.commit()
            assert r.reconciliation.resolved == 1
    _run(go())
