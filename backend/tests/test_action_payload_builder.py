"""
Action Catalog Expansion A4 — payload builder tests.

Builds a validated executor payload ONLY from derivable data. The five advertising
"stop auto-promotion" types → {"offer_id": listing.external_id}; missing listing →
payload_not_derivable; SEO/Review → payload_not_derivable; Legal → no_binding. No
fabricated payload, no DB writes, no execution.
"""
import asyncio
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.product_listing import ProductListing

from services.action_binding.registry import BY_SIGNAL_TYPE
from services.action_binding.payload_builder import (
    build_action_payload, PayloadBuildResult, REASON_NO_BINDING, REASON_PAYLOAD_NOT_DERIVABLE,
)

# A2.2-bind: overspend types rebind to ad_set_state (campaign pause, resolver-derived);
# the indirect stock/listing types keep stop_auto_promotion (offer_id-derived).
ADV_STOP = ("adv_ad_on_low_stock", "adv_ad_on_oos_risk", "adv_ad_on_bad_listing")
ADV_OVERSPEND = ("adv_ad_destroying_profit", "adv_ad_spend_without_sales",
                 "adv_ad_on_unprofitable_product")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_listing(db, uid, *, mp="wb", external_id="SKU1"):
    db.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace=mp,
                          external_id=external_id))
    await db.commit()


async def _count_listings(db):
    return (await db.execute(select(func.count()).select_from(ProductListing))).scalar()


# ── 1. the 3 stock/listing adv types build a stop_auto_promotion payload ──────

def test_stop_adv_types_build_payload():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_listing(db, uid, external_id="SKU1")
        for st in ADV_STOP:
            res = await build_action_payload(db, user_id=uid, signal_type=st,
                                             marketplace="wildberries", sku="SKU1")
            assert isinstance(res, PayloadBuildResult) and res.ok
            assert res.action_key == "stop_auto_promotion"
            assert res.payload == {"offer_id": "SKU1"}
            assert res.action_key == BY_SIGNAL_TYPE[st].action_key   # matches registry
    _run(go())


# ── 1b. overspend types now bind ad_set_state — payload not derivable without a
#       resolvable single campaign (no connection/campaign here) ───────────────

def test_overspend_types_bind_ad_set_state_not_derivable_without_campaign():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_listing(db, uid, external_id="SKU1")
        for st in ADV_OVERSPEND:
            res = await build_action_payload(db, user_id=uid, signal_type=st,
                                             marketplace="wildberries", sku="SKU1")
            assert res.action_key == "ad_set_state"
            assert res.action_key == BY_SIGNAL_TYPE[st].action_key
            assert res.ok is False and res.payload is None     # no guessed campaign_id
    _run(go())


# ── 2. missing listing → payload_not_derivable (no fabricated offer_id) ──────

def test_missing_listing_not_derivable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid, signal_type="adv_ad_on_low_stock",
                                         marketplace="wildberries", sku="NOPE")
        assert res.ok is False and res.reason == REASON_PAYLOAD_NOT_DERIVABLE
        assert res.payload is None and res.action_key == "stop_auto_promotion"
    _run(go())


# ── 3. SEO stays payload_not_derivable ───────────────────────────────────────

def test_seo_not_derivable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid, signal_type="seo_title_too_short",
                                         marketplace="wb", sku="SKU1")
        assert res.ok is False and res.reason == REASON_PAYLOAD_NOT_DERIVABLE
        assert res.action_key is None
    _run(go())


# ── 4. Review stays payload_not_derivable ────────────────────────────────────

def test_review_not_derivable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="rev_unanswered_negative_review",
                                         marketplace="wildberries", sku="SKU3")
        assert res.ok is False and res.reason == REASON_PAYLOAD_NOT_DERIVABLE
    _run(go())


# ── 5. Legal → no_binding ────────────────────────────────────────────────────

def test_legal_no_binding():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid, signal_type="legal_content_claim_risk",
                                         marketplace="wb", sku="SKU1")
        assert res.ok is False and res.reason == REASON_NO_BINDING
    _run(go())


# ── 6. unknown signal type → no_binding ──────────────────────────────────────

def test_unknown_type_no_binding():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid, signal_type="bogus_type",
                                         marketplace="wb", sku="SKU1")
        assert res.ok is False and res.reason == REASON_NO_BINDING and res.action_key is None
    _run(go())


# ── 7. payload contains only allowed fields ──────────────────────────────────

def test_payload_only_allowed_fields():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_listing(db, uid, external_id="SKU1")
        res = await build_action_payload(db, user_id=uid, signal_type="adv_ad_on_low_stock",
                                         marketplace="wildberries", sku="SKU1")
        assert set(res.payload.keys()) == {"offer_id"}   # nothing extra, nothing generated
    _run(go())


# ── 8. no DB writes (read-only) ──────────────────────────────────────────────

def test_no_db_writes():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_listing(db, uid, external_id="SKU1")
        before = await _count_listings(db)
        await build_action_payload(db, user_id=uid, signal_type="adv_ad_on_oos_risk",
                                   marketplace="wildberries", sku="SKU1")
        assert await _count_listings(db) == before   # nothing written
    _run(go())
