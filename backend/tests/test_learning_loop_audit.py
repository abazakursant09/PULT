"""
Learning loop end-to-end audit (post L3 enrichment + L4 recency weighting).

Verifies the WRITE→READ context round-trip — the one link the per-slice suites
do not exercise together:

  record_decision_memory (enriching writer the close bridge calls)
    -> writes the SAME enriched context_group the read side queries with
    -> rank_actions reads enriched rows (not degraded fallback) when data exists
    -> recency weighting orders them
    -> get_ranked_alternatives exposes weighted_rate
    -> promote_insight_alternatives(context_group=...) uses the weighted order

Read-only checks (no writes, refuted-loop determinism) are covered by the
purity guards in the per-slice suites; this file proves the data actually lines
up across the write and read sides.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from models.product import Product
from models.product_listing import ProductListing
from models.imported_finance import ImportedFinanceRow

from services.decision_memory import record_decision_memory, build_context_group
from services.outcome_memory_ranking import rank_actions
from services.ranked_alternatives import get_ranked_alternatives
from services.insight_decision_bridge import (
    promote_insight_alternatives, InsightPromotionDTO,
)

NOW = datetime(2026, 6, 20)
ACTIONS = ["set_price", "reduce_discount", "stop_auto_promotion"]
SKU = "SKU1"
IKEY = f"margin_crisis:wildberries:{SKU}"
# Expected enriched context: marketplace stored as the listing's real value
# ("wildberries"), category "electronics", price 1000 -> mid, margin 30% -> high_margin.
ENRICHED = "wildberries|electronics|mid|high_margin"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_domain(db, uid):
    """Product (category/price) + listing (marketplace) + finance (margin) for SKU."""
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="P", marketplace="wildberries",
                   category="electronics", sku=SKU, price=1000.0)
    db.add(prod)
    listing = ProductListing(id=str(uuid.uuid4()), physical_product_id=str(uuid.uuid4()),
                             user_id=uid, marketplace="wildberries", external_id="nm1",
                             legacy_product_id=prod.id)
    db.add(listing)
    # 30% margin -> high_margin
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace="wildberries",
                              sku=SKU, net_profit=300.0, revenue=1000.0))
    await db.flush()
    return listing.id


def _decision(uid, listing_id, action):
    return Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                    listing_id=listing_id, action_key=action, insight_key=IKEY)


# ── CHECK 1: enriched context actually written by the close-path writer ───────

def test_enriched_context_written_on_record():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid)
        d = _decision(uid, listing_id, "set_price")
        db.add(d); await db.flush()
        row = await record_decision_memory(db, decision=d, outcome="refuted", now=NOW)
        # enrichment produced real segments, not the degraded marketplace|unknown|... key
        assert row.context_group == ENRICHED
        assert "unknown" not in row.context_group
    _run(go())


# ── CHECK 3: missing data degrades segment-by-segment to unknown ──────────────

def test_missing_data_degrades_per_segment():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # no listing -> marketplace unknown; no product -> category/price unknown;
        # no finance -> margin unknown. insight_key still carries sku but no finance row.
        d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                     action_key="set_price", insight_key=IKEY)
        db.add(d); await db.flush()
        row = await record_decision_memory(db, decision=d, outcome="refuted", now=NOW)
        assert row.context_group == "unknown|unknown|unknown|unknown"
    _run(go())


# ── CHECK 2 + 5 + 6: read side queries the SAME enriched context, recency orders,
#                     min_sample stays on raw sample ──────────────────────────

async def _mem(db, uid, action, outcome, *, age_days, n, context_group):
    created = NOW - timedelta(days=age_days)
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"{IKEY}:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action, context_group=context_group,
                              outcome=outcome, created_at=created))
    await db.flush()


def test_read_uses_enriched_context_and_recency():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid)
        # write one real memory row via the enriching writer; capture its context
        d = _decision(uid, listing_id, "set_price")
        db.add(d); await db.flush()
        written = (await record_decision_memory(db, decision=d, outcome="confirmed",
                                                now=NOW - timedelta(days=2))).context_group
        assert written == ENRICHED

        # add history under the SAME enriched context:
        # set_price: +2 recent confirmed (total 3 confirmed recent) , 2 OLD refuted
        await _mem(db, uid, "set_price", "confirmed", age_days=2, n=2, context_group=ENRICHED)
        await _mem(db, uid, "set_price", "refuted", age_days=120, n=2, context_group=ENRICHED)
        # reduce_discount: 3 recent confirmed + 3 recent refuted (raw 0.5, weighted 0.5)
        await _mem(db, uid, "reduce_discount", "confirmed", age_days=2, n=3, context_group=ENRICHED)
        await _mem(db, uid, "reduce_discount", "refuted", age_days=2, n=3, context_group=ENRICHED)
        # a DEGRADED-context confirmed pile must NOT leak into the enriched query
        await _mem(db, uid, "stop_auto_promotion", "confirmed", age_days=1, n=5,
                   context_group="wildberries|unknown|unknown|unknown")
        await db.commit()

        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=ENRICHED, available_actions=ACTIONS, now=NOW)
        by = {r["action_key"]: r for r in ranked}

        # CHECK 2: enriched rows were read (set_price sample = 3 conf + 2 ref = 5)
        assert by["set_price"]["sample"] == 5
        # CHECK 6: min_sample on RAW sample
        assert by["set_price"]["eligible"] is True
        # CHECK 5: set_price refuteds are OLD -> weighted_rate 3.0/(3.0+0.5)=0.857 > raw 0.6
        assert by["set_price"]["confirmed_rate"] == 0.6
        assert by["set_price"]["weighted_rate"] == round(3.0 / 3.5, 4)
        # reduce_discount recent refuteds -> weighted == raw 0.5
        assert by["reduce_discount"]["weighted_rate"] == 0.5
        # CHECK 2 (isolation): degraded-context stop_auto_promotion NOT counted here
        assert by["stop_auto_promotion"]["sample"] == 0
        # order: set_price (0.857) > reduce_discount (0.5) > stop (fallback)
        assert [r["action_key"] for r in ranked] == \
            ["set_price", "reduce_discount", "stop_auto_promotion"]
    _run(go())


# ── CHECK 4: old marketplace|unknown|unknown|unknown rows still rank ──────────

def test_legacy_degraded_context_still_ranks():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        cg = "wildberries|unknown|unknown|unknown"
        await _mem(db, uid, "reduce_discount", "confirmed", age_days=5, n=3, context_group=cg)
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=cg, available_actions=ACTIONS, now=NOW)
        rd = next(r for r in ranked if r["action_key"] == "reduce_discount")
        assert rd["eligible"] is True and rd["weighted_rate"] == 1.0
    _run(go())


# ── CHECK 7 + 8: ranked_alternatives exposes weighted_rate + weighted reason ──

def test_ranked_alternatives_exposes_weighted_rate():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", age_days=2, n=4, context_group=ENRICHED)
        await _mem(db, uid, "reduce_discount", "refuted", age_days=200, n=1, context_group=ENRICHED)
        await db.commit()
        alts = await get_ranked_alternatives(db, user_id=uid, insight_key=IKEY,
                                             context_group=ENRICHED)
        top = next(a for a in alts if a["action_key"] == "reduce_discount")
        assert "weighted_rate" in top and top["weighted_rate"] is not None
        # CHECK 8: reason notes recency weighting
        assert "recent outcomes weighted" in top["reason"]
    _run(go())


# ── CHECK 9: ranked promotion uses the weighted order ─────────────────────────

def test_promotion_uses_weighted_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # reduce_discount: clean recent confirmed -> top; set_price: recent refuted heavy
        await _mem(db, uid, "reduce_discount", "confirmed", age_days=2, n=3, context_group=ENRICHED)
        await _mem(db, uid, "set_price", "refuted", age_days=2, n=3, context_group=ENRICHED)
        await db.commit()
        dto = InsightPromotionDTO(insight_key=IKEY, itype="margin_crisis",
                                  marketplace="wildberries", sku=SKU)
        results = await promote_insight_alternatives(db, user_id=uid, insight=dto,
                                                     context_group=ENRICHED)
        await db.commit()
        # all three promoted; reduce_discount promoted first (highest weighted_rate)
        ids = [r.decision_id for r in results]
        first = await db.get(Decision, ids[0])
        assert first.action_key == "reduce_discount"
        assert len(results) == 3
    _run(go())
