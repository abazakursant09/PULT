"""
Presentation Intelligence P0 — PresentationCard grouping.

Read-layer synthesis foundation: group build_feed's FeedItems into (marketplace, sku)
cards. P0 = grouping ONLY (no dedup, no re-prioritization, no root cause, no volume mgmt).
Proves: one SKU's items collapse to one card; different SKUs / marketplaces stay separate;
EVERY FeedItem is preserved in build_feed order; grouping is deterministic; no diagnosis
field is altered; empty in → empty out. Plus one integration test through the real
build_feed to prove Presentation consumes FeedItems only.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select  # noqa: F401 (kept parallel to sibling tests)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_signal import SeoSignal
from models.returns_signal import ReturnsSignal

from services.decision_feed.builder import FeedItem, build_feed
from services.presentation.cards import build_presentation_cards, PresentationCard

T0 = datetime(2026, 7, 1)


def _run(c):
    return asyncio.run(c)


def _fi(contour, sku, marketplace, *, item_key=None, rec="сделать X", severity="high",
        what="что-то произошло", order_bucket="active"):
    """Minimal FeedItem for grouping tests — only the fields P0 grouping reads matter."""
    it = FeedItem(
        item_key=item_key or f"{contour}_x:{marketplace}:{sku}",
        contour=contour, source_table=f"{contour}_signal", source_id=str(uuid.uuid4()),
        source_status="active", attention_state="new", marketplace=marketplace, sku=sku,
        title=f"{contour} title", what_happened=what, why_it_matters="почему",
        meaning="смысл", recommended_action=rec, expected_effect="эффект")
    it._priority_bucket = severity
    it._order_bucket = order_bucket
    return it


# ── 1. one SKU, multiple items → one card ────────────────────────────────────

def test_one_sku_multiple_items_one_card():
    items = [_fi("seo", "SKU1", "wildberries"),
             _fi("returns", "SKU1", "wildberries"),
             _fi("advertising", "SKU1", "wildberries")]
    cards = build_presentation_cards(items)
    assert len(cards) == 1
    c = cards[0]
    assert isinstance(c, PresentationCard)
    assert c.marketplace == "wildberries" and c.sku == "SKU1"
    assert len(c.items) == 3
    assert c.contributing_contours == ("seo", "returns", "advertising")


# ── 2. different SKUs stay separate ──────────────────────────────────────────

def test_different_skus_separate():
    items = [_fi("seo", "SKU1", "wildberries"), _fi("seo", "SKU2", "wildberries")]
    cards = build_presentation_cards(items)
    assert {(c.marketplace, c.sku) for c in cards} == {("wildberries", "SKU1"),
                                                       ("wildberries", "SKU2")}
    assert len(cards) == 2


# ── 3. different marketplaces stay separate (same sku) ───────────────────────

def test_different_marketplaces_separate():
    items = [_fi("seo", "SKU1", "wildberries"), _fi("seo", "SKU1", "ozon")]
    cards = build_presentation_cards(items)
    assert len(cards) == 2
    assert {c.group_key for c in cards} == {"wildberries:SKU1", "ozon:SKU1"}


# ── 4. every FeedItem preserved (nothing hidden/removed) ─────────────────────

def test_every_feed_item_preserved():
    items = [_fi("seo", "SKU1", "wildberries"), _fi("returns", "SKU1", "wildberries"),
             _fi("seo", "SKU2", "ozon"), _fi("rating", "SKU2", "ozon"),
             _fi("money_leak", "SKU3", "wildberries")]
    cards = build_presentation_cards(items)
    regrouped = [it for c in cards for it in c.items]
    assert len(regrouped) == len(items)
    # exact same FeedItem objects, none added/dropped
    assert {id(it) for it in regrouped} == {id(it) for it in items}


# ── 5. deterministic ─────────────────────────────────────────────────────────

def test_deterministic():
    items = [_fi("seo", "SKU1", "wildberries"), _fi("returns", "SKU2", "wildberries"),
             _fi("advertising", "SKU1", "wildberries")]
    a = build_presentation_cards(items)
    b = build_presentation_cards(items)
    assert [c.group_key for c in a] == [c.group_key for c in b]
    assert [[it.item_key for it in c.items] for c in a] == \
           [[it.item_key for it in c.items] for c in b]
    # card order = first-appearance of each group
    assert [c.group_key for c in a] == ["wildberries:SKU1", "wildberries:SKU2"]


# ── 6. no diagnosis data changes (verbatim passthrough) ──────────────────────

def test_no_diagnosis_data_changes():
    src = _fi("returns", "SKU1", "wildberries", rec="проверить причины", what="возвраты выросли")
    c = build_presentation_cards([src])[0]
    it = c.items[0]
    assert it is src                                   # same object, not a copy
    assert it.recommended_action == "проверить причины"
    assert it.what_happened == "возвраты выросли"
    assert c.recommendations == ["проверить причины"]  # verbatim
    assert c.evidence[0]["what_happened"] == "возвраты выросли"


# ── 7. order inside a group preserved (build_feed order) ─────────────────────

def test_order_inside_group_preserved():
    items = [_fi("seo", "SKU1", "wildberries", item_key="a"),
             _fi("returns", "SKU1", "wildberries", item_key="b"),
             _fi("rating", "SKU1", "wildberries", item_key="c")]
    c = build_presentation_cards(items)[0]
    assert [it.item_key for it in c.items] == ["a", "b", "c"]


# ── 8. empty feed → empty cards ──────────────────────────────────────────────

def test_empty_feed_empty_cards():
    assert build_presentation_cards([]) == []


# ── 9. highest severity = most severe member ─────────────────────────────────

def test_highest_severity_is_most_severe_member():
    items = [_fi("seo", "SKU1", "wildberries", severity="low"),
             _fi("returns", "SKU1", "wildberries", severity="critical"),
             _fi("rating", "SKU1", "wildberries", severity="medium")]
    c = build_presentation_cards(items)[0]
    assert c.highest_severity == "critical"


def test_highest_severity_none_when_all_unranked():
    items = [_fi("seo", "SKU1", "wildberries", severity=None)]
    c = build_presentation_cards(items)[0]
    assert c.highest_severity is None


# ── 10. P0 keeps duplicate recommendations (dedup is P1) ─────────────────────

def test_duplicate_recommendations_kept_in_p0():
    items = [_fi("seo", "SKU1", "wildberries", rec="improve_listing"),
             _fi("advertising", "SKU1", "wildberries", rec="improve_listing")]
    c = build_presentation_cards(items)[0]
    assert c.recommendations == ["improve_listing", "improve_listing"]


# ── 11. root-cause placeholder stays None in P0 ──────────────────────────────

def test_root_cause_narrative_placeholder_none():
    c = build_presentation_cards([_fi("seo", "SKU1", "wildberries")])[0]
    assert c.root_cause_narrative is None


# ── 12. None marketplace/sku still grouped, never dropped ─────────────────────

def test_none_marketplace_or_sku_not_dropped():
    items = [_fi("decision_outcome", None, None, item_key="d1"),
             _fi("seo", "SKU1", "wildberries")]
    cards = build_presentation_cards(items)
    assert len(cards) == 2
    total = [it for c in cards for it in c.items]
    assert len(total) == 2                              # nothing hidden


# ── 13. integration: consumes real build_feed output ─────────────────────────

async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def test_integration_groups_real_feed_items():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        # two live signals for the SAME (mp, sku) from two different contours
        db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
               problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1",
               marketplace="wb", sku="SKU1", status="active", what="короткий тайтл",
               why="ранжирование", meaning="...", what_to_do="дополнить",
               expected_effect="охват", priority_level="high", created_at=T0))
        db.add(ReturnsSignal(audit_id=aid, user_id=uid,
               signal_key="returns_return_rate_rise", problem_type="return_rate_rise",
               insight_key="returns_return_rate_rise:wb:SKU1", category="returns",
               marketplace="wb", sku="SKU1", status="active", what="возвраты растут",
               why="частота", meaning="...", what_to_do="проверить", expected_effect="маржа",
               priority_level="medium", effect_type="return_rate_rise", created_at=T0))
        await db.commit()
        feed = await build_feed(db, user_id=uid, now=T0)
        assert len([i for i in feed if i.sku == "SKU1"]) == 2
        cards = build_presentation_cards(feed)
        sku1 = [c for c in cards if c.sku == "SKU1"]
        assert len(sku1) == 1
        c = sku1[0]
        assert set(c.contributing_contours) == {"seo", "returns"}
        assert c.highest_severity == "high"            # most severe of {high, medium}
        assert len(c.items) == 2
    _run(go())
