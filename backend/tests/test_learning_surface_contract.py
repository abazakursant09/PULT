"""
Sprint E3 — Learning Surface contract tests.

The Learning Surface is a pure FRONTEND composition of two existing read-only
endpoints (GET /api/learning/alternatives + GET /api/learning/evidence). No
backend code is added. These tests model the documented assembler (see
docs/LEARNING_SURFACE_CONTRACT.md) over real endpoint outputs and assert the
contract: recommendation/evidence/alternatives mapping, ui_state derivation, and
degraded/empty/fallback behavior.

They also pin a KNOWN BLOCKER (see docs §5): the two endpoints resolve
context_group independently — /alternatives from listing_id, /evidence from the
insight_key — so they coincide ONLY in the fully-degraded (no-listing) case. The
evidence block can therefore reflect a more-degraded context than the
recommendation/alternatives block. Consistent-surface tests use a no-listing
insight where both resolve the same all-unknown context.
"""
import asyncio
import uuid

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

from routers.learning import (
    ranked_alternatives_endpoint, decision_evidence_endpoint,
)

SKU = "SKU1"
# Bare insight (no marketplace/sku segment): both endpoints resolve the SAME
# all-unknown context, so the surface is internally consistent.
IKEY = "margin_crisis"
CG_UNK = "unknown|unknown|unknown|unknown"
# Enriched insight WITH a listing: used only to demonstrate the divergence blocker.
IKEY_ENR = f"margin_crisis:wildberries:{SKU}"
ACTIONS = ["set_price", "reduce_discount", "stop_auto_promotion"]
RECOMMENDED_KEYS = {"action_key", "rank", "reason", "confidence_source"}


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


async def _mem(db, uid, action, outcome, n, cg=CG_UNK):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"margin_crisis:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action,
                              context_group=cg, outcome=outcome))
    await db.flush()


# ── documented assembler (frontend logic, modeled here; NOT backend) ─────────

async def _build_surface(db, uid, insight_key, listing_id=None):
    alts = await ranked_alternatives_endpoint(
        insight_key=insight_key, listing_id=listing_id, current_user=_User(uid), db=db)
    alternatives = [a.model_dump() for a in alts.alternatives]

    if not alternatives:
        recommended, evidence, ui = None, None, "empty"
    else:
        top = alts.alternatives[0]
        recommended = {
            "action_key": top.action_key,
            "rank": top.rank,
            "reason": top.reason,
            "confidence_source": alts.source,
        }
        ev_resp = await decision_evidence_endpoint(
            insight_key=insight_key, action_key=top.action_key,
            listing_id=listing_id, current_user=_User(uid), db=db)
        evidence = ev_resp.evidence.model_dump() if ev_resp.evidence else None
        ui = "fallback" if top.fallback else "ranked"

    return {
        "insight_key": alts.insight_key,
        "recommended_action": recommended,
        "evidence": evidence,
        "alternatives": alternatives,
        "degraded": alts.degraded,
        "source": alts.source,
        "ui_state": ui,
    }


# ── ranked: history present (consistent, no-listing context) ─────────────────

def test_surface_ranked_with_history():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await db.commit()
        s = await _build_surface(db, uid, IKEY)
        assert s["ui_state"] == "ranked"
        rec = s["recommended_action"]
        assert set(rec) == RECOMMENDED_KEYS
        assert rec["action_key"] == "reduce_discount" and rec["rank"] == 1
        assert rec["confidence_source"] == "decision_memory"
        # recommendation mirrors alternatives[0]
        assert rec["action_key"] == s["alternatives"][0]["action_key"]
        assert rec["reason"] == s["alternatives"][0]["reason"]
        # evidence populated for the recommended action; stats agree (same context)
        ev = s["evidence"]
        assert ev["action_key"] == rec["action_key"]
        assert ev["confirmed"] == 4 and ev["refuted"] == 1 and ev["sample"] == 5
        assert ev["confirmed_rate"] == 0.8 and ev["weighted_rate"] == 0.8
        assert ev["fallback"] is False and ev["context_group"] == CG_UNK
        # evidence stats == alternatives[0] stats (internal consistency)
        a0 = s["alternatives"][0]
        for k in ("confirmed", "refuted", "sample", "confirmed_rate", "weighted_rate", "fallback"):
            assert ev[k] == a0[k]
        assert {a["action_key"] for a in s["alternatives"]} == set(ACTIONS)
    _run(go())


# ── fallback: no history ─────────────────────────────────────────────────────

def test_surface_fallback_no_history():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        s = await _build_surface(db, uid, IKEY)
        assert s["ui_state"] == "fallback"
        assert s["recommended_action"] is not None       # recommendation still shown
        assert s["alternatives"][0]["fallback"] is True   # banner trigger
        assert s["evidence"]["fallback"] is True and s["evidence"]["sample"] == 0
        assert len(s["alternatives"]) == 3               # never hidden
    _run(go())


# ── empty: malformed / unsupported insight ───────────────────────────────────

def test_surface_empty_malformed():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        s = await _build_surface(db, uid, "")
        assert s["ui_state"] == "empty"
        assert s["recommended_action"] is None and s["evidence"] is None
        assert s["alternatives"] == []
    _run(go())


def test_surface_empty_unsupported_insight():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        s = await _build_surface(db, uid, "low_stock:wb:SKU1")
        assert s["ui_state"] == "empty" and s["alternatives"] == []
    _run(go())


# ── degraded: recommendation + alternatives shown, never hidden ──────────────

def test_surface_degraded_keeps_recommendation_and_alternatives():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await db.commit()
        s = await _build_surface(db, uid, IKEY)
        assert s["degraded"] is True                      # all-unknown context
        assert s["recommended_action"] is not None        # shown
        assert len(s["alternatives"]) == 3                # never hidden
        assert s["ui_state"] == "ranked"                  # degraded orthogonal to ui_state
    _run(go())


# ── contract field-shape lock ────────────────────────────────────────────────

def test_surface_top_level_shape():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        s = await _build_surface(db, uid, IKEY)
        assert set(s) == {"insight_key", "recommended_action", "evidence",
                          "alternatives", "degraded", "source", "ui_state"}
        assert s["source"] == "decision_memory"
        assert s["ui_state"] in {"ranked", "fallback", "empty"}
    _run(go())


# ── E4 fix: with the SAME listing_id, /alternatives and /evidence agree ──────
#    (BLOCKER-1 resolved — evidence now resolves context the same way as
#    alternatives, so an enriched insight stays enriched on both sides.)

async def _seed_domain(db, uid):
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="P", marketplace="wildberries",
                   category="electronics", sku=SKU, price=1000.0)
    db.add(prod)
    listing = ProductListing(id=str(uuid.uuid4()), physical_product_id=str(uuid.uuid4()),
                             user_id=uid, marketplace="wildberries", external_id="nm1",
                             legacy_product_id=prod.id)
    db.add(listing)
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace="wildberries",
                              sku=SKU, net_profit=300.0, revenue=1000.0))
    await db.flush()
    return listing.id


def test_listing_backed_context_consistent_across_endpoints():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid)
        await db.commit()
        # /alternatives WITH listing → enriched context, not degraded
        alts = await ranked_alternatives_endpoint(
            insight_key=IKEY_ENR, listing_id=listing_id, current_user=_User(uid), db=db)
        assert alts.degraded is False
        # /evidence WITH the same listing_id → same enriched context, also not degraded
        ev = await decision_evidence_endpoint(
            insight_key=IKEY_ENR, action_key="set_price", listing_id=listing_id,
            current_user=_User(uid), db=db)
        assert ev.evidence is not None
        assert ev.evidence.context_group == "wildberries|electronics|mid|high_margin"
        assert "unknown" not in ev.evidence.context_group     # enriched, NOT degraded
    _run(go())


def test_surface_enriched_consistent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid)
        await db.commit()
        s = await _build_surface(db, uid, IKEY_ENR, listing_id=listing_id)
        assert s["degraded"] is False                # alternatives enriched
        assert s["evidence"]["context_group"] == "wildberries|electronics|mid|high_margin"
        # evidence stats agree with alternatives[0] for the same listing context
        a0 = s["alternatives"][0]; ev = s["evidence"]
        assert ev["action_key"] == a0["action_key"]
        for k in ("reason", "confirmed", "refuted", "sample",
                  "confirmed_rate", "weighted_rate", "fallback"):
            assert ev[k] == a0[k]
    _run(go())
