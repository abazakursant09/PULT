"""
SEO A7 — API layer tests.

Handlers are called directly with a real in-memory db (DI bypassed). GET data is
seeded through the A5/A6 service (audit_and_persist); POST /seo/audit exercises
the adapter path (stubs → honest snapshot_unavailable). Verifies all endpoints,
honest degradation, marketplace independence, no fake numbers / no score, and
signal status filtering.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_signal import SeoSignal
from sqlalchemy import select

from services.seo.card_snapshot import (
    CardSnapshot, SeoConstraints, CategorySchema, CardAttribute, CardMedia,
)
from services.seo.audit_persist import audit_and_persist

from routers import seo
from routers.seo import (
    run_seo_audit, seo_overview, seo_signals, seo_problems, seo_audits,
    SeoAuditRequest, SeoAuditResponse, SeoOverviewResponse,
)

CONS = SeoConstraints(title_min_len=20, title_max_len=100, description_min_len=200,
                      media_min_images=3, attribute_fill_rate_threshold=0.6,
                      content_completeness_threshold=0.7)
_ALL = {"title", "description", "attributes", "media", "category_schema",
        "category_path", "expected_category_path", "variants", "constraints"}


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _User:
    def __init__(self, uid):
        self.id = uid


def _problem_snap(mp="wildberries", images=0):
    # only media_below_minimum triggers
    return CardSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=datetime(2026, 6, 21),
        source="api", title="x" * 40, description="d" * 300, brand="b",
        category_path=("root", "cat"), expected_category_path=("root", "cat"),
        category_schema=CategorySchema(), attributes=(CardAttribute("colour", "red", True),),
        variants=("size",), media=CardMedia(image_count=images), constraints=CONS,
        field_availability={k: True for k in _ALL})


async def _seed(db, uid, mp="wildberries"):
    await audit_and_persist(db, user_id=uid, snapshot=_problem_snap(mp), now=datetime(2026, 6, 21))
    await db.commit()


# ── POST /seo/audit — honest snapshot_unavailable (stub adapters) ────────────

def test_post_audit_snapshot_unavailable_honest():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await run_seo_audit(SeoAuditRequest(listing_id="L1", marketplace="ozon"),
                                   current_user=_User(uid), db=db)
        assert isinstance(resp, SeoAuditResponse)
        assert resp.ok is False and resp.status == "snapshot_unavailable"
        assert resp.reason == "adapter_not_implemented"
        assert resp.audit_id is None and resp.total_problems is None  # no fake numbers
    _run(go())


def test_post_audit_unknown_marketplace():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await run_seo_audit(SeoAuditRequest(listing_id="L1", marketplace="nonexistent"),
                                   current_user=_User(uid), db=db)
        assert resp.ok is False and resp.status == "unknown_marketplace"
    _run(go())


# ── GET /seo/overview — no score, real counts ────────────────────────────────

def test_overview_no_score():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid)
        ov = await seo_overview(listing_id="L1", current_user=_User(uid), db=db)
        assert isinstance(ov, SeoOverviewResponse)
        assert ov.active_signals == 1 and ov.unresolved_problems == 1
        assert ov.high_signals == 1 and ov.critical_signals == 0   # media = high
        assert ov.last_audit_at is not None
        assert not hasattr(ov, "score") and not hasattr(ov, "seo_score")
        assert "internal_health_index" not in ov.model_dump()
    _run(go())


def test_overview_empty_user():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ov = await seo_overview(listing_id=None, current_user=_User(uid), db=db)
        assert ov.active_signals == 0 and ov.last_audit_at is None  # no fake numbers
    _run(go())


# ── GET /seo/signals — PULT language + status filter ─────────────────────────

def test_signals_doctrine_and_filter():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid)
        active = await seo_signals(listing_id="L1", status="active", current_user=_User(uid), db=db)
        assert active.total == 1
        s = active.items[0]
        assert s.what and s.why and s.meaning and s.recommended_action and s.expected_effect
        assert s.recommended_action_key == "add_media"
        # dismiss it → active filter empty, dismissed filter shows it
        sig = (await db.execute(select(SeoSignal).where(SeoSignal.user_id == uid))).scalar_one()
        sig.status = "dismissed"; await db.commit()
        assert (await seo_signals(listing_id="L1", status="active",
                                  current_user=_User(uid), db=db)).total == 0
        assert (await seo_signals(listing_id="L1", status="dismissed",
                                  current_user=_User(uid), db=db)).total == 1
        assert (await seo_signals(listing_id="L1", status="resolved",
                                  current_user=_User(uid), db=db)).total == 0
    _run(go())


# ── GET /seo/problems ────────────────────────────────────────────────────────

def test_problems_endpoint():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid)
        pr = await seo_problems(listing_id="L1", current_user=_User(uid), db=db)
        assert pr.total == 1
        p = pr.items[0]
        assert p.problem_type == "media_below_minimum" and p.severity == "high"
        assert p.evidence == {"image_count": 0, "media_min_images": 3}  # real, from snapshot
        assert p.detected_at is not None
    _run(go())


# ── GET /seo/audits — history, no raw snapshot / no score ────────────────────

def test_audits_history_no_raw():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid)
        au = await seo_audits(listing_id="L1", current_user=_User(uid), db=db)
        assert au.total == 1
        a = au.items[0].model_dump()
        assert a["status"] == "completed" and a["total_problems"] == 1
        for forbidden in ("internal_health_index", "score", "snapshot", "snapshot_hash"):
            assert forbidden not in a
    _run(go())


# ── marketplace agnostic ─────────────────────────────────────────────────────

def test_agnostic_endpoints():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await _seed(db, uid, mp=mp)
            ov = await seo_overview(listing_id="L1", current_user=_User(uid), db=db)
            sigs = await seo_signals(listing_id="L1", current_user=_User(uid), db=db)
            assert ov.active_signals == 1 and sigs.total == 1
            assert sigs.items[0].insight_key == f"seo_media_below_minimum:{mp}:SKU1"
    _run(go())


# ── routes registered ────────────────────────────────────────────────────────

def test_routes_registered():
    paths = {getattr(r, "path", None) for r in seo.router.routes}
    assert {"/seo/audit", "/seo/overview", "/seo/signals", "/seo/problems",
            "/seo/audits"} <= paths
