"""
Characterization guard — the OBSERVED not-evaluated reason already survives the
read path effect_measurement -> effect_summary -> decision_feed without any backend
change. Locks the passthrough the frontend Reason-Visibility slice depends on.

NO production code is exercised beyond the existing read path; this test only
asserts that `reason`/`missing` written into EngineEffectObservation.evidence reach
`FeedItem.source_context` (and DecisionEffectSummary.evidence) unchanged.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.imported_finance import ImportedFinanceRow
from models.pricing_signal import PricingSignal

# 2.1A: the former promoted vehicle (operations auto-promo → stop_auto_promotion) is contained; the
# not-evaluated passthrough is action-agnostic, so it rides a live net_profit lever instead.
SIGNAL_KEY = "pricing_price_below_floor"
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement,
)
from services.decision_outcome.effect_summary import build_effect_summaries
from services.decision_feed.builder import build_feed

T0 = datetime(2026, 6, 1)
T1 = datetime(2026, 6, 21)
SKU = "SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_promoted(db, uid, *, with_baseline_finance: bool):
    """Ozon listing + operations signal promoted to a Decision (link → promoted).
    Optionally a baseline finance row so the measurement can capture a baseline."""
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="товар", cogs=50.0, cogs_source="manual"))
    db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace="ozon", external_id=SKU))
    db.add(PricingSignal(user_id=uid, signal_key=SIGNAL_KEY,
           insight_key=f"{SIGNAL_KEY}:ozon:{SKU}", problem_type="price_below_floor",
           category="pricing", marketplace="ozon", sku=SKU, what="x", why="y", meaning="m",
           what_to_do="w", expected_effect="z", priority_level="critical", status="active"))
    if with_baseline_finance:
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                                  date="2026-06-01", sku=SKU, revenue=10000.0, net_profit=-200.0))
    await db.commit()
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()


async def _ops_feed_item(db, uid):
    items = await build_feed(db, user_id=uid, include_resolved=True)
    return next(it for it in items
                if it.action_key == "set_price" and (it.group_key or "").startswith(SIGNAL_KEY))


# ── (1) not_evaluated → reason=insufficient_data + missing=no_finance_rows ─────

def test_not_evaluated_reason_reaches_feed_source_context():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_promoted(db, uid, with_baseline_finance=True)
        # open captures baseline; close finds NO after-finance in the window → not_evaluated
        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()

        item = await _ops_feed_item(db, uid)
        assert item.effect_status == "not_evaluated"
        sc = item.source_context
        assert sc.get("reason") == "insufficient_data"
        assert sc.get("missing") == "no_finance_rows"     # observed cause, passed through
    _run(go())


# ── (2) not_measured_yet → reason=no_finance_rows survives effect_summary ──────

def test_not_measured_yet_reason_survives_effect_summary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_promoted(db, uid, with_baseline_finance=False)
        # open with no finance at all → baseline unavailable, measured_at stays None
        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()

        summaries = await build_effect_summaries(db, user_id=uid, contour="pricing")
        s = next(x for x in summaries if x.action_key == "set_price")
        assert s.effect_status == "not_measured_yet"
        assert s.evidence.get("reason") == "no_finance_rows"   # observed cause, passed through
    _run(go())
