"""
SEO seller-facing verification (Phase C3c follow-up) — tests-only.

SEO is the 9th live contour: run_seo_producer writes seo_signal on schedule. This
proves the LIVE producer output surfaces correctly, end-to-end, on every seller-facing
surface WITHOUT any diagnosis-logic change:

    run_seo_producer → seo_signal → Decision Feed (build_feed) → Today (build_today)
                                  → Dashboard DTO / Telegram line / Copilot DTO

Verification only — NO production code changed. Asserts:
  1. Feed — a real producer run yields a FeedItem with contour="seo", canonical
     item_key seo_<pt>:<mp>:<sku>, correct marketplace/sku, an observed priority_level,
     and NON-EMPTY, non-placeholder doctrine copy (what/why/meaning/what_to_do/expected_effect).
  2. Today — the SEO item appears in build_today / top_action and honours snooze+dismiss
     exactly like every other contour (contour-agnostic DecisionFeedState overlay).
  3. Surface rendering — Dashboard FeedItemView, Copilot TodayItemView and the Telegram
     one-liner all render readable SEO text (no blank title/description, no leaked
     "{placeholder}" braces).
  4. Copy completeness — every SEO problem_type in the signal builder produces readable
     5-part doctrine copy with no unresolved template placeholder.
  5. Isolation — no uploaded card → no phantom SEO feed item; absent/unresolved Reference
     → no fabricated constraint feed item.

Advisory-only, no marketplace API, no marketplace write, no engine/rule/schema change.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_card_content import ImportedCardContentRow
from models.marketplace_category import MarketplaceCategoryRow
from models.marketplace_category_attribute import MarketplaceCategoryAttributeRow
from models.seo_signal import SeoSignal
from models.decision_feed_state import DecisionFeedState

from services.advisory_runtime.producers import run_seo_producer
from services.advisory_runtime.runtime import RuntimeContext
from services.decision_feed.builder import build_feed
from services.decision_feed.today import build_today, top_action
from services.seo.signal_builder import build_signal, _TEMPLATES
from services.seo.evaluation import RuleEvaluation, RuleResult

# surface DTO mappers (read-only projections used by the routers/telegram task)
from routers.decision_feed import _view as feed_view
from routers.today import _view as today_view
from tasks.scheduler import _format_top_action

NOW = datetime(2026, 7, 15)
MP = "wildberries"
SKU = "SKU1"

# constraint problem_types that require fresh Reference to fire — must NOT appear
# when the reference is absent/unresolved (honest degradation, not fabrication).
_CONSTRAINT_PROBLEMS = ("title_too_short", "title_too_long", "description_too_short",
                        "media_below_minimum", "content_completeness_low",
                        "required_attributes_missing", "filter_attributes_missing",
                        "variant_attributes_missing", "wrong_category_placement",
                        "attributes_incomplete", "attribute_values_invalid")


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _card(db, uid, *, marketplace=MP, sku=SKU, category="Красота",
                description="Крем", image_count=0, day=10):
    db.add(ImportedCardContentRow(
        import_id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace, sku=sku,
        title="Крем", description=description, brand="AquaCare", category=category,
        characteristics_json=json.dumps({"Объём": "75 мл"}, ensure_ascii=False),
        image_count=image_count, image_urls_json=None, created_at=datetime(2026, 7, day)))
    await db.flush()


async def _reference(db, *, marketplace=MP, category_id="128", name="Красота",
                     path="Красота>Уход", captured=datetime(2026, 7, 1), version="v1"):
    db.add(MarketplaceCategoryRow(
        marketplace=marketplace, category_id=category_id, name=name, path=path,
        captured_at=captured, version=version, source="api_snapshot"))
    db.add(MarketplaceCategoryAttributeRow(
        marketplace=marketplace, category_id=category_id, attribute_id="1", name="Состав",
        type="text", is_required=True, is_filterable=False, is_variant=False,
        captured_at=captured, version=version, source="api_snapshot"))
    await db.flush()


def _ctx(db, uid):
    return RuntimeContext(db=db, user_id=uid, now=NOW, run_id="r1", logger=None,
                          triggered_by="scheduled")


async def _produce(db, uid, **card_kw):
    """Run the LIVE SEO producer over a seeded card + fresh reference; commit."""
    await _card(db, uid, **card_kw)
    await _reference(db)
    await db.commit()
    await run_seo_producer(_ctx(db, uid))
    await db.commit()


_DOCTRINE = ("title", "what_happened", "why_it_matters", "meaning",
             "recommended_action", "expected_effect")


def _assert_readable(item):
    """Every doctrine field non-empty and free of leaked template placeholders."""
    for f in _DOCTRINE:
        v = getattr(item, f)
        assert v and v.strip(), f"blank {f} on {getattr(item, 'item_key', item)}"
        assert "{" not in v and "}" not in v, f"leaked placeholder in {f}: {v!r}"


# ── 1. live producer output surfaces in the Decision Feed ─────────────────────

def test_seo_signal_surfaces_in_feed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _produce(db, uid)
        feed = await build_feed(db, user_id=uid, now=NOW)
        seo = [i for i in feed if i.contour == "seo"]
        assert seo, "expected at least one live SEO feed item from the producer"
        for i in seo:
            # canonical item_key: seo_<problem_type>:<marketplace>:<sku>
            parts = i.item_key.split(":")
            assert len(parts) == 3 and parts[0].startswith("seo_"), i.item_key
            assert parts[1] == MP and parts[2] == SKU, i.item_key
            assert i.marketplace == MP and i.sku == SKU
            # observed severity propagates into the feed sort key (priority is an
            # ordering signal, not a seller-displayed DTO field)
            assert i._priority_bucket in ("critical", "high", "medium", "low")
            _assert_readable(i)
    _run(go())


# ── 2. same item flows into Today + top_action ───────────────────────────────

def test_seo_in_today_and_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _produce(db, uid)
        today = await build_today(db, user_id=uid, now=NOW)
        seo = [a for a in today if a.contour == "seo"]
        assert seo, "expected SEO item in Today"
        for a in seo:
            _assert_readable(a)
        # top_action is the first feed item; whatever contour it is, it must be readable
        top = await top_action(db, user_id=uid, now=NOW)
        assert top is not None
        # scope Today to seo → its top is an SEO item
        seo_top = await top_action(db, user_id=uid, contour="seo", now=NOW)
        assert seo_top is not None and seo_top.contour == "seo"
        _assert_readable(seo_top)
    _run(go())


# ── 3. snooze / dismiss behave for SEO exactly like other contours ───────────

def test_seo_snooze_and_dismiss_like_other_contours():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _produce(db, uid)
        key = next(i.item_key for i in await build_feed(db, user_id=uid, now=NOW)
                   if i.contour == "seo")

        # snoozed → hidden by default, visible with include_snoozed
        db.add(DecisionFeedState(user_id=uid, item_key=key, contour="seo", state="snoozed",
                                 snooze_until=NOW + timedelta(days=3), created_at=NOW))
        await db.commit()
        default_keys = {i.item_key for i in await build_feed(db, user_id=uid, now=NOW)}
        assert key not in default_keys
        snoozed_keys = {i.item_key for i in await build_feed(
            db, user_id=uid, include_snoozed=True, now=NOW)}
        assert key in snoozed_keys

        # flip to dismissed → hidden by default, visible with include_dismissed
        st = (await db.execute(select(DecisionFeedState).where(
            DecisionFeedState.item_key == key))).scalars().one()
        st.state = "dismissed"; st.snooze_until = None
        await db.commit()
        assert key not in {i.item_key for i in await build_feed(db, user_id=uid, now=NOW)}
        assert key in {i.item_key for i in await build_feed(
            db, user_id=uid, include_dismissed=True, now=NOW)}
    _run(go())


# ── 4. surface DTOs render readable SEO copy (Dashboard / Copilot / Telegram) ─

def test_surface_dtos_render_readable_seo():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _produce(db, uid)
        feed = [i for i in await build_feed(db, user_id=uid, now=NOW) if i.contour == "seo"]
        today = [a for a in await build_today(db, user_id=uid, now=NOW) if a.contour == "seo"]
        assert feed and today

        # Dashboard: FeedItem → FeedItemView
        for i in feed:
            _assert_readable(feed_view(i))

        # Copilot: TodayAction → TodayItemView
        for a in today:
            _assert_readable(today_view(a))

        # Telegram: one-line 'Главное сейчас' from a SEO TodayAction
        line = _format_top_action(today[0])
        assert line and line.strip()
        assert "{" not in line and "}" not in line
        assert line not in ("Проверить дашборд",)  # fell through to fallback = blank copy
    _run(go())


# ── 5. copy completeness — every SEO problem_type has readable 5-part copy ────

def _eval(pt, evidence):
    return RuleEvaluation(problem_type=pt, category="content", severity="medium",
                          estimated_effect_type="visibility", detectability="static_card",
                          result=RuleResult.TRIGGERED, evidence=evidence)


def test_every_problem_type_has_readable_copy():
    # superset of every placeholder any template references — proves no template
    # leaves a hole even when fully evidenced.
    evidence = {
        "filled_count": 2, "required_count": 5,
        "title_length": 8, "title_min_len": 20, "title_max_len": 60,
        "attribute_fill_rate": "40%", "description_length": 30, "description_min_len": 100,
        "content_completeness": "55%", "image_count": 1, "media_min_images": 3,
    }
    for pt in _TEMPLATES:
        d = build_signal(_eval(pt, evidence), marketplace=MP, sku=SKU)
        for field in (d.what, d.why, d.meaning, d.what_to_do, d.expected_effect):
            assert field and field.strip(), f"blank copy for {pt}"
            assert "{" not in field and "}" not in field, f"leaked placeholder in {pt}: {field!r}"
        assert d.signal_key == f"seo_{pt}"
        assert d.insight_key == f"seo_{pt}:{MP}:{SKU}"


# ── 6. isolation — no phantom / no fabricated feed items ─────────────────────

def test_no_card_no_seo_feed_item():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _reference(db); await db.commit()          # reference only, NO card
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        feed = await build_feed(db, user_id=uid, now=NOW)
        assert [i for i in feed if i.contour == "seo"] == []
        assert (await db.execute(select(SeoSignal).where(SeoSignal.user_id == uid))).scalars().all() == []
    _run(go())


def test_absent_reference_no_fabricated_constraint_feed_item():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # card present, NO reference → constraint rules degrade to not_evaluated
        await _card(db, uid, description="", image_count=0); await db.commit()
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        feed = await build_feed(db, user_id=uid, now=NOW)
        seo_problem_types = {i.source_context.get("problem_type")
                             for i in feed if i.contour == "seo"}
        for constraint in _CONSTRAINT_PROBLEMS:
            assert constraint not in seo_problem_types, f"fabricated {constraint} without reference"
    _run(go())
