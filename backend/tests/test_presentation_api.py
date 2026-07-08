"""
Presentation Intelligence P4 — read API tests.

GET /api/presentation/cards re-frames the caller's live Decision Feed into (marketplace, sku)
PresentationCards with P1 recommendation groups, P2 severity ordering and P3 root-cause
narrative. Additive + read-only: same tenant scoping as /decision-feed, no writes, no internal
sort keys leaked, and /decision-feed + /today envelopes are unchanged.
"""
import asyncio
import inspect
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_signal import SeoSignal
from models.legal_signal import LegalSignal

from datetime import datetime

from dependencies import get_current_user
from services.decision_feed.builder import build_feed
from services.presentation import build_presentation_cards
from services.presentation.cards import PresentationCard, RecommendationGroup
from routers import presentation as pr
from routers.presentation import (
    get_presentation_cards, PresentationResponse, PresentationCardView,
    RecommendationGroupView, _card_view,
)

T0 = datetime(2026, 6, 21)


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


CONVERGE = "улучшить контент карточки"


async def _seed(db, uid):
    """SKU1/wb: two diagnoses (seo + legal) that CONVERGE on the same action → 1 group, narrative.
    SKU2/wb: a single seo diagnosis → its own card, no convergence."""
    aid = str(uuid.uuid4())
    db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
           problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1",
           marketplace="wb", sku="SKU1", status="active", what="короткий тайтл", why="ранж",
           meaning="x", what_to_do=CONVERGE, expected_effect="охват", created_at=T0))
    db.add(LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_content_claim_risk",
           requirement_type="content_claim_risk", insight_key="legal_content_claim_risk:wb:SKU1",
           marketplace="wb", sku="SKU1", status="active", what="формулировки", why="претензии",
           meaning="x", what_to_do=CONVERGE, expected_effect="риск", created_at=T0))
    db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
           problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU2",
           marketplace="wb", sku="SKU2", status="active", what="короткий", why="ранж",
           meaning="x", what_to_do="переписать заголовок", expected_effect="охват", created_at=T0))
    await db.commit()


async def _fetch(db, uid, **kw):
    return await get_presentation_cards(current_user=_User(uid), db=db, **kw)


# ── 1. GET returns 200 envelope ──────────────────────────────────────────────

def test_get_cards_envelope():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        resp = await _fetch(db, uid)
        assert isinstance(resp, PresentationResponse)
        assert set(resp.model_dump().keys()) == {"cards"}
        assert len(resp.cards) == 2                          # SKU1 + SKU2 cards
    _run(go())


# ── 2. cards grouped by marketplace + sku ────────────────────────────────────

def test_cards_grouped_by_marketplace_sku():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        resp = await _fetch(db, uid)
        keys = {(c.marketplace, c.sku) for c in resp.cards}
        assert keys == {("wb", "SKU1"), ("wb", "SKU2")}
        sku1 = next(c for c in resp.cards if c.sku == "SKU1")
        assert len(sku1.items) == 2                          # seo + legal grouped under one card
        assert sku1.group_key == "wb:SKU1"
    _run(go())


# ── 3. response includes recommendation_groups ───────────────────────────────

def test_response_includes_recommendation_groups():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        sku1 = next(c for c in (await _fetch(db, uid)).cards if c.sku == "SKU1")
        assert len(sku1.recommendation_groups) == 1
        g = sku1.recommendation_groups[0]
        assert isinstance(g, RecommendationGroupView)
        assert g.recommendation == CONVERGE
        assert set(g.contributing_contours) == {"seo", "legal"}
    _run(go())


# ── 4. response includes group_severity ──────────────────────────────────────

def test_response_includes_group_severity():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        items = await build_feed(db, user_id=uid)
        expected = {c.group_key: c.recommendation_groups for c in build_presentation_cards(items)}
        sku1 = next(c for c in (await _fetch(db, uid)).cards if c.sku == "SKU1")
        g = sku1.recommendation_groups[0]
        assert "group_severity" in g.model_dump()
        assert g.group_severity == expected["wb:SKU1"][0].group_severity
    _run(go())


# ── 5. response includes root_cause_narrative ────────────────────────────────

def test_response_includes_root_cause_narrative():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        sku1 = next(c for c in (await _fetch(db, uid)).cards if c.sku == "SKU1")
        assert "root_cause_narrative" in sku1.model_dump()
        assert "root_cause_narrative" in sku1.recommendation_groups[0].model_dump()
    _run(go())


# ── 6. internal fields never exposed ─────────────────────────────────────────

def test_internal_fields_not_exposed():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        blob = (await _fetch(db, uid)).model_dump_json()
        assert "_priority_bucket" not in blob
        assert "_order_bucket" not in blob
        # also absent from the item view schema itself
        assert "_priority_bucket" not in RecommendationGroupView.model_fields
    _run(go())


# ── 7. P2 group ordering preserved through JSON ──────────────────────────────

def test_p2_group_ordering_preserved_through_json():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        items = await build_feed(db, user_id=uid)
        expected = {c.group_key: [g.recommendation for g in c.recommendation_groups]
                    for c in build_presentation_cards(items)}
        for c in (await _fetch(db, uid)).cards:
            assert [g.recommendation for g in c.recommendation_groups] == expected[c.group_key]
    _run(go())


# ── 8. P3 narrative appears when diagnoses converge ──────────────────────────

def test_narrative_present_on_convergence():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        sku1 = next(c for c in (await _fetch(db, uid)).cards if c.sku == "SKU1")
        g = sku1.recommendation_groups[0]
        assert g.root_cause_narrative is not None
        assert "seo" in g.root_cause_narrative and "legal" in g.root_cause_narrative
        assert sku1.root_cause_narrative == g.root_cause_narrative      # card mirrors group
    _run(go())


# ── 9. narrative null when not enough convergence ────────────────────────────

def test_narrative_null_without_convergence():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        sku2 = next(c for c in (await _fetch(db, uid)).cards if c.sku == "SKU2")
        assert len(sku2.items) == 1
        assert sku2.recommendation_groups[0].root_cause_narrative is None
        assert sku2.root_cause_narrative is None
    _run(go())


# ── 10. auth is required ──────────────────────────────────────────────────────

def test_auth_required():
    dep = inspect.signature(get_presentation_cards).parameters["current_user"].default
    assert dep.dependency is get_current_user      # endpoint gated by the same auth dependency


# ── 11. tenant scoping respected ─────────────────────────────────────────────

def test_tenant_scoping():
    async def go():
        db = await _engine()
        a = str(uuid.uuid4()); b = str(uuid.uuid4())
        await _seed(db, a)                          # only user A has signals
        resp_b = await _fetch(db, b)                # user B is a different tenant
        assert resp_b.cards == []                   # B sees none of A's cards
        resp_a = await _fetch(db, a)
        assert len(resp_a.cards) == 2
    _run(go())


# ── 12. empty feed returns {"cards": []} ─────────────────────────────────────

def test_empty_feed_returns_empty_cards():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())   # no seed
        resp = await _fetch(db, uid)
        assert resp.model_dump() == {"cards": []}
    _run(go())


# ── 13. None marketplace/sku serializes safely ───────────────────────────────

def test_none_marketplace_sku_serializes():
    # a card with None marketplace/sku (by design, never dropped) must serialize without error
    card = PresentationCard(
        marketplace=None, sku=None, items=[], highest_severity=None,
        contributing_contours=(), recommendations=[], evidence=[],
        recommendation_groups=[], root_cause_narrative=None, group_key="None:None")
    view = _card_view(card)
    assert isinstance(view, PresentationCardView)
    dumped = view.model_dump()
    assert dumped["marketplace"] is None and dumped["sku"] is None
    assert dumped["group_key"] == "None:None"


# ── 14. existing /api/today response unchanged (still mounted) ────────────────

def test_today_route_unchanged():
    import main
    app_paths = set(main.app.openapi()["paths"])
    assert "/api/today" in app_paths                # today endpoint still present, untouched


# ── 15. existing /api/decision-feed response unchanged ───────────────────────

def test_decision_feed_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        from routers.decision_feed import get_decision_feed, FeedResponse
        resp = await get_decision_feed(current_user=_User(uid), db=db)
        assert isinstance(resp, FeedResponse)
        assert set(resp.model_dump().keys()) == {"items", "total"}   # envelope unchanged
    _run(go())
    import main
    app_paths = set(main.app.openapi()["paths"])
    assert "/api/decision-feed" in app_paths
    assert "/api/presentation/cards" in app_paths   # new endpoint additive alongside


# ── 16. new route mounted ─────────────────────────────────────────────────────

def test_presentation_route_mounted():
    paths = {getattr(r, "path", None) for r in pr.router.routes}
    assert "/presentation/cards" in paths
