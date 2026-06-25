"""
Decision Outcome A2 — data foundation schema + canonical registry tests.

Two tables: engine_signal_decision_link (binding ledger, unique per seller/insight/
action) and engine_effect_observation (append-only proof, qualitative effect_band
only). No score / forecast / ROI / money columns. CANONICAL_INSIGHT_TYPES must
cover every engine's live signal types and surface the incompatible (4-part)
Review keys. Marketplace-agnostic.
"""
import asyncio
import json
import uuid

from sqlalchemy import select, inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.decision_outcome.registry import (
    CANONICAL_INSIGHT_TYPES, BY_SIGNAL_KEY, CONTOURS, incompatible_signal_keys,
)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── 1. link round-trip ───────────────────────────────────────────────────────

def test_link_roundtrip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        link = EngineSignalDecisionLink(
            user_id=uid, contour="legal", signal_table="legal_signal",
            signal_id=str(uuid.uuid4()), insight_key="legal_content_claim_risk:wildberries:SKU1",
            action_key="review_content_claim", marketplace="wildberries", sku="SKU1")
        db.add(link); await db.commit()
        got = (await db.execute(select(EngineSignalDecisionLink))).scalar_one()
        assert got.link_status == "proposed" and got.decision_id is None
        assert got.contour == "legal" and got.insight_key.startswith("legal_")
    _run(go())


# ── 2. observation round-trip + qualitative band ─────────────────────────────

def test_observation_roundtrip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); lid = str(uuid.uuid4())
        obs = EngineEffectObservation(
            link_id=lid, user_id=uid, insight_key="adv_ad_destroying_profit:ozon:SKU1",
            metric_key="ad_profit_impact", window_days=14,
            evidence=json.dumps({"observed": "raw_only"}))
        db.add(obs); await db.commit()
        got = (await db.execute(select(EngineEffectObservation))).scalar_one()
        assert got.effect_band == "not_evaluated"      # default = honest absence of proof
        assert got.window_days == 14 and got.measured_at is None
    _run(go())


# ── 3. effect_band qualitative values storable ───────────────────────────────

def test_effect_bands_storable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for band in ("improved", "unchanged", "worsened", "not_evaluated"):
            db.add(EngineEffectObservation(link_id=str(uuid.uuid4()), user_id=uid,
                                           insight_key=f"seo_x:wb:{band}", metric_key="search_visibility",
                                           effect_band=band))
        await db.commit()
        bands = {o.effect_band for o in (await db.execute(select(EngineEffectObservation))).scalars().all()}
        assert bands == {"improved", "unchanged", "worsened", "not_evaluated"}
    _run(go())


# ── 4. uniqueness (user, insight_key, action_key) ────────────────────────────

def test_link_unique_per_user_insight_action():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(EngineSignalDecisionLink(user_id=uid, contour="seo", signal_table="seo_signal",
               signal_id="s1", insight_key="seo_title_too_short:wb:SKU1", action_key="update_card"))
        await db.commit()
        db.add(EngineSignalDecisionLink(user_id=uid, contour="seo", signal_table="seo_signal",
               signal_id="s2", insight_key="seo_title_too_short:wb:SKU1", action_key="update_card"))
        raised = False
        try:
            await db.commit()
        except Exception:
            raised = True; await db.rollback()
        assert raised
    _run(go())


# ── 5. append-only: detection tables carry no updated_at ─────────────────────

def test_append_only_no_updated_at():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    assert "updated_at" not in cols(EngineSignalDecisionLink)
    assert "updated_at" not in cols(EngineEffectObservation)


# ── 6. no score / forecast / ROI / money columns ─────────────────────────────

def test_no_score_forecast_money_columns():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (EngineSignalDecisionLink, EngineEffectObservation):
        c = cols(model)
        for bad in ("score", "forecast", "roi", "pnl", "pnl_impact", "money",
                    "expected_revenue", "projection", "predicted_effect"):
            assert bad not in c, f"{model.__tablename__}.{bad}"


# ── 7. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for i, mp in enumerate(("wildberries", "ozon", "yandex")):
            db.add(EngineSignalDecisionLink(user_id=uid, contour="growth", signal_table="growth_signal",
                   signal_id=f"s{i}", insight_key=f"growth_margin_expansion_candidate:{mp}:SKU1",
                   action_key="review_price_upside", marketplace=mp))
        await db.commit()
        mps = {l.marketplace for l in (await db.execute(select(EngineSignalDecisionLink))).scalars().all()}
        assert mps == {"wildberries", "ozon", "yandex"}
    _run(go())


# ── 8. canonical registry covers every engine's live signal types ────────────

def test_registry_covers_live_engine_types():
    from services.seo.rules import RULE_REGISTRY as SEO
    from services.advertising.rules import RULE_REGISTRY as ADV
    from services.review.rules import RULE_REGISTRY as REV
    from services.growth.rules import RULE_REGISTRY as GROW
    from services.legal.snapshot import REQUIREMENT_CANDIDATES as LEGAL
    from services.pricing.rules import RULE_REGISTRY as PRICING

    live = set()
    live |= {f"seo_{r.problem_type}" for r in SEO}
    live |= {f"adv_{r.problem_type}" for r in ADV}
    live |= {f"rev_{r.problem_type}" for r in REV}
    live |= {f"growth_{r.problem_type}" for r in GROW}
    live |= {f"legal_{rt}" for rt in LEGAL}
    live |= {f"pricing_{r.problem_type}" for r in PRICING}   # A3-pre

    registry = set(BY_SIGNAL_KEY.keys())
    missing = live - registry
    extra = registry - live
    assert not missing, f"registry missing live signal types: {missing}"
    assert not extra, f"registry has stale types not in engines: {extra}"
    assert set(CONTOURS) == {"seo", "advertising", "review", "growth", "legal", "pricing"}


# ── 9. incompatible insight keys surfaced (Review 4-part only) ───────────────

def test_incompatible_keys_are_only_review():
    inc = set(incompatible_signal_keys())
    assert inc and all(k.startswith("rev_") for k in inc)
    # all non-review entries are 3-part compatible
    for c in CANONICAL_INSIGHT_TYPES:
        if c.contour == "review":
            assert c.key_arity == 4 and c.carries_review_id and not c.three_part_compatible
        else:
            assert c.key_arity == 3 and c.three_part_compatible and not c.carries_review_id
