"""ME-6 — insight_mapping turns an insight into an executor plan (one path)."""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.product import Product
from models.pricing_rule import PricingRule
from models.competitor_analysis import CompetitorAnalysis
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog          # noqa: F401
from models.automation_rule import AutomationRule      # noqa: F401
from models.review_response import ReviewResponse       # noqa: F401
from services.marketplace import insight_mapping, executor, credential_vault


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


# ── margin_crisis maps to a set_price plan from real product+rule+competitors ──
def test_margin_crisis_maps_to_set_price():
    async def go():
        db = await _engine()
        uid = str(uuid.uuid4())
        p = Product(id=str(uuid.uuid4()), user_id=uid, name="X", marketplace="wildberries",
                    sku="999", price=1000.0)
        db.add(p); await db.flush()
        db.add(PricingRule(product_id=p.id, target_position="equal_top_1",
                           target_percent=0.0, min_price=500, max_price=5000, auto_mode=False))
        db.add(CompetitorAnalysis(product_id=p.id, competitor_name="C1",
                                  marketplace="wildberries", price=1800.0,
                                  significance="primary"))
        await db.commit()
        plan = await insight_mapping.resolve_plan(db, uid, "margin_crisis:wildberries:999")
        assert plan.ready, plan.needs_input
        assert plan.action_type == "set_price"
        assert plan.payload["offer_id"] == "999"
        assert plan.payload["price"] is not None
        assert plan.automation_eligible is True
    _run(go())


# ── high_ad_spend needs campaign_id; given it, becomes an executable plan ──────
def test_high_ad_spend_needs_input_then_ready():
    async def go():
        db = await _engine()
        uid = str(uuid.uuid4())
        p1 = await insight_mapping.resolve_plan(db, uid, "high_ad_spend:wildberries:5")
        assert not p1.ready and "campaign_id" in p1.needs_input
        p2 = await insight_mapping.resolve_plan(
            db, uid, "high_ad_spend:wildberries:5",
            overrides={"campaign_id": 7, "cpm": 200},
        )
        assert p2.ready and p2.action_type == "ad_set_bid"
    _run(go())


# ── seo_opportunity needs a card; given it, becomes update_card ───────────────
def test_seo_opportunity_needs_card():
    async def go():
        db = await _engine()
        uid = str(uuid.uuid4())
        p1 = await insight_mapping.resolve_plan(db, uid, "seo_opportunity:wildberries:5")
        assert not p1.ready and p1.needs_input == ["card"]
        p2 = await insight_mapping.resolve_plan(
            db, uid, "seo_opportunity:wildberries:5",
            overrides={"card": {"title": "t"}},
        )
        assert p2.ready and p2.action_type == "update_card"
    _run(go())


# ── rating_good is a batch publish plan ───────────────────────────────────────
def test_rating_good_is_batch():
    async def go():
        db = await _engine()
        plan = await insight_mapping.resolve_plan(db, str(uuid.uuid4()), "rating_good:wildberries:1")
        assert plan.ready and plan.batch and plan.action_type == "publish_review_response"
    _run(go())


# ── unsupported insight → needs_input(unsupported) ────────────────────────────
def test_unsupported_insight():
    async def go():
        db = await _engine()
        plan = await insight_mapping.resolve_plan(db, str(uuid.uuid4()), "low_stock:wildberries:1")
        assert not plan.ready and plan.needs_input == ["unsupported_insight"]
    _run(go())


# ── the plan feeds the SAME executor (margin_crisis end-to-end via executor) ──
def test_plan_executes_through_shared_executor():
    async def go():
        db = await _engine()
        uid = str(uuid.uuid4())
        p = Product(id=str(uuid.uuid4()), user_id=uid, name="X", marketplace="wildberries",
                    sku="42", price=1000.0)
        db.add(p); await db.flush()
        db.add(PricingRule(product_id=p.id, target_position="equal_top_1",
                           target_percent=0.0, min_price=500, max_price=5000, auto_mode=False))
        db.add(CompetitorAnalysis(product_id=p.id, competitor_name="C1",
                                  marketplace="wildberries", price=1800.0,
                                  significance="primary"))
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                     status="connected", scopes=["prices"])
        db.add(conn); await db.flush()
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
        await db.commit()

        from services.marketplace.wb_client import wb_client
        calls = {"n": 0}
        async def fake(*, token, offer_id, price, discount=None):
            calls["n"] += 1; return {"requestId": "rq"}
        wb_client.set_price = fake

        plan = await insight_mapping.resolve_plan(db, uid, "margin_crisis:wildberries:42")
        res = await executor.execute(db=db, user_id=uid, action_type=plan.action_type,
                                     payload=plan.payload, mode="manual_l3",
                                     insight_key="margin_crisis:wildberries:42")
        assert res.status == "success"
        assert calls["n"] == 1
        # execution log carries the insight_key (traceability requirement)
        from sqlalchemy import select
        logs = (await db.execute(select(ExecutionLog).where(ExecutionLog.user_id == uid))).scalars().all()
        assert any(l.insight_key == "margin_crisis:wildberries:42" for l in logs)
    _run(go())
