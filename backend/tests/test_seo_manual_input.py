"""
SEO A9 — manual CardSnapshot input.

POST /api/seo/audit supports an explicit `snapshot` payload (source="manual").
Honest: present fields → evaluated, omitted fields → not_evaluated; omitted
constraints → constraint rules not_evaluated (no default limits). adapter mode
unchanged. No SEO score. Marketplace-agnostic. Handler called directly.
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_audit import SeoAudit
from models.seo_problem import SeoProblem
from models.seo_signal import SeoSignal

from routers.seo import run_seo_audit, SeoAuditRequest, SeoAuditResponse
from services.seo.manual_source import (
    ManualSnapshot, ManualConstraints, ManualMedia, ManualCategorySchema, ManualAttribute,
)

CONS = ManualConstraints(title_min_len=60, title_max_len=120, description_min_len=300,
                         media_min_images=5, attribute_fill_rate_threshold=0.7,
                         content_completeness_threshold=0.75)


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


async def _post(db, uid, body):
    return await run_seo_audit(body, current_user=_User(uid), db=db)


def _bad_card(**over):
    base = dict(sku="SKU1", title="short", description="x" * 350, brand="Acme",
                category_path=["root", "cat"], expected_category_path=["root", "cat"],
                category_schema=ManualCategorySchema(),
                attributes=[ManualAttribute(key="colour", value="red", is_filled=True)],
                variants=["size"], media=ManualMedia(image_count=1), constraints=CONS)
    base.update(over)
    return ManualSnapshot(**base)


def _good_card():
    return ManualSnapshot(
        sku="SKU1", title="x" * 80, description="d" * 350, brand="Acme",
        category_path=["root", "cat"], expected_category_path=["root", "cat"],
        category_schema=ManualCategorySchema(),
        attributes=[ManualAttribute(key="colour", value="red", is_filled=True)],
        variants=["size"], media=ManualMedia(image_count=6), constraints=CONS)


# ── 1. adapter mode unchanged ────────────────────────────────────────────────

def test_adapter_mode_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _post(db, uid, SeoAuditRequest(listing_id="L1", marketplace="ozon"))
        assert resp.ok is False and resp.status == "snapshot_unavailable"
    _run(go())


# ── 2. manual bad card → triggered problems ──────────────────────────────────

def test_manual_bad_card_triggers():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _post(db, uid, SeoAuditRequest(
            listing_id="L1", marketplace="wildberries", snapshot=_bad_card()))
        assert isinstance(resp, SeoAuditResponse) and resp.ok and resp.status == "completed"
        assert resp.total_problems >= 1   # short title + low media trigger
        probs = {p.problem_type for p in (await db.execute(
            select(SeoProblem).where(SeoProblem.audit_id == resp.audit_id))).scalars().all()}
        assert "title_too_short" in probs and "media_below_minimum" in probs
    _run(go())


# ── 3. manual good card → no triggers ────────────────────────────────────────

def test_manual_good_card_no_triggers():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _post(db, uid, SeoAuditRequest(
            listing_id="L1", marketplace="wildberries", snapshot=_good_card()))
        assert resp.ok and resp.total_problems == 0
    _run(go())


# ── 4. missing manual constraints → not_evaluated (no default limits) ────────

def test_missing_constraints_not_evaluated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # title clearly short, but NO constraints provided → must NOT be assessed
        resp = await _post(db, uid, SeoAuditRequest(
            listing_id="L1", marketplace="wildberries",
            snapshot=_bad_card(constraints=None)))
        assert resp.ok
        probs = {p.problem_type for p in (await db.execute(
            select(SeoProblem).where(SeoProblem.audit_id == resp.audit_id))).scalars().all()}
        # constraint-dependent rules cannot fire without limits
        assert "title_too_short" not in probs and "media_below_minimum" not in probs
        assert resp.total_not_evaluated >= 1
    _run(go())


# ── 5. manual creates seo_problem + seo_signal ───────────────────────────────

def test_manual_creates_problem_and_signal():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _post(db, uid, SeoAuditRequest(
            listing_id="L1", marketplace="wildberries", snapshot=_bad_card()))
        sigs = (await db.execute(select(SeoSignal).where(
            SeoSignal.audit_id == resp.audit_id))).scalars().all()
        assert len(sigs) == resp.total_problems and sigs
        s = next(s for s in sigs if s.problem_type == "title_too_short")
        assert s.what and s.why and s.meaning and s.what_to_do and s.expected_effect
        assert s.status == "active"
    _run(go())


# ── 6. source = manual ───────────────────────────────────────────────────────

def test_source_is_manual():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _post(db, uid, SeoAuditRequest(
            listing_id="L1", marketplace="wildberries", snapshot=_bad_card()))
        audit = (await db.execute(select(SeoAudit).where(
            SeoAudit.id == resp.audit_id))).scalar_one()
        assert audit.source == "manual"
    _run(go())


# ── 7. no SEO score ──────────────────────────────────────────────────────────

def test_no_score():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _post(db, uid, SeoAuditRequest(
            listing_id="L1", marketplace="wildberries", snapshot=_bad_card()))
        audit = (await db.execute(select(SeoAudit).where(
            SeoAudit.id == resp.audit_id))).scalar_one()
        assert audit.internal_health_index is None
        assert "internal_health_index" not in resp.model_dump()
        assert "score" not in resp.model_dump()
    _run(go())


# ── 8. marketplace agnostic ──────────────────────────────────────────────────

def test_manual_agnostic():
    async def go():
        outcomes = []
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            resp = await _post(db, uid, SeoAuditRequest(
                listing_id="L1", marketplace=mp, snapshot=_bad_card()))
            assert resp.ok
            outcomes.append(resp.total_problems)
            sig = (await db.execute(select(SeoSignal).where(
                SeoSignal.audit_id == resp.audit_id))).scalars().first()
            assert sig.insight_key.endswith(f":{mp}:SKU1")
        assert len(set(outcomes)) == 1   # identical results across MPs
    _run(go())


# ── 9. no hardcoded limits (omitting one constraint set → no constraint rules) ─

def test_no_hardcoded_limits():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # good title/desc/media but NO constraints → those rules can't pass or fail
        resp = await _post(db, uid, SeoAuditRequest(
            listing_id="L1", marketplace="wildberries",
            snapshot=_good_card().model_copy(update={"constraints": None})))
        assert resp.ok
        # with no limits, content/title/media/description-length rules are not_evaluated
        assert resp.total_not_evaluated >= 4
    _run(go())
