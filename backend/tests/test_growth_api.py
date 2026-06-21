"""
Growth A7 — API layer tests.

Handlers called directly with a real in-memory db (DI bypassed). POST audit builds
a GrowthSnapshot from seeded ImportedFinanceRow; missing finance →
growth_unavailable (not error). Verifies all endpoints, doctrine fields, no fake
numbers / no growth score / no forecast-competitor-AI leak, marketplace
independence, routes mounted.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow

from routers import growth_engine as gr
from routers.growth_engine import (
    run_growth_audit, growth_overview, growth_signals, growth_problems, growth_audits,
    GrowthAuditRequest, ThresholdsIn, GrowthAuditResponse, GrowthOverviewResponse,
)

TH = ThresholdsIn(low_stock_units=5, min_revenue_for_growth_signal=1000.0,
                  min_net_profit_for_growth_signal=100.0)


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


async def _seed_finance(db, uid, *, marketplace="wildberries", sku="SKU1",
                        revenue=10000.0, net_profit=2000.0, ad_spend=0.0, quantity=40):
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace, sku=sku,
                              revenue=revenue, net_profit=net_profit, ad_spend=ad_spend,
                              quantity=quantity))
    await db.flush()


async def _audit(db, uid, *, mp="wildberries", sku="SKU1", listing_id="L1", thresholds=TH):
    return await run_growth_audit(
        GrowthAuditRequest(listing_id=listing_id, marketplace=mp, sku=sku, thresholds=thresholds),
        current_user=_User(uid), db=db)


# ── 1. POST audit from finance ───────────────────────────────────────────────

def test_post_audit_from_finance():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        resp = await _audit(db, uid)
        assert isinstance(resp, GrowthAuditResponse)
        assert resp.ok and resp.status == "completed"
        assert resp.total_problems >= 1   # profitable_ad / margin_expansion trigger
        assert resp.reconciliation.created >= 1
    _run(go())


# ── 2. missing finance → growth_unavailable (not error) ──────────────────────

def test_missing_finance_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _audit(db, uid, sku="NOPE")
        assert resp.ok is False and resp.status == "growth_unavailable"
        assert resp.reason == "finance_missing" and resp.audit_id is None
    _run(go())


# ── 3. overview without fake numbers / no score ──────────────────────────────

def test_overview_no_fake_no_score():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        ov = await growth_overview(listing_id="L1", marketplace="wildberries",
                                   current_user=_User(uid), db=db)
        assert isinstance(ov, GrowthOverviewResponse)
        assert ov.active_signals >= 1 and ov.unresolved_opportunities >= 1
        assert ov.high_signals >= 1            # profitable_ad is high severity
        assert ov.last_audit_at is not None
        d = ov.model_dump()
        assert "score" not in d and "growth_score" not in d and "internal_health_index" not in d
        # empty user → zeros, no fabrication
        ov2 = await growth_overview(current_user=_User(str(uuid.uuid4())), db=db)
        assert ov2.active_signals == 0 and ov2.last_audit_at is None
    _run(go())


# ── 4. signals doctrine fields + category filter ─────────────────────────────

def test_signals_doctrine_and_filter():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        sg = await growth_signals(listing_id="L1", status="active",
                                  current_user=_User(uid), db=db)
        assert sg.total >= 1
        s = next(x for x in sg.items if x.problem_type == "profitable_ad_candidate")
        assert s.what and s.why and s.meaning and s.recommended_action and s.expected_effect
        assert s.priority_level == "high" and s.effect_band and s.confidence is not None
        assert s.category == "advertising" and s.recommended_action_key == "start_advertising"
        # category filter
        sg2 = await growth_signals(category="advertising", current_user=_User(uid), db=db)
        assert all(x.category == "advertising" for x in sg2.items) and sg2.total >= 1
    _run(go())


# ── 5. problems = latest audit ───────────────────────────────────────────────

def test_problems_latest_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        pr = await growth_problems(listing_id="L1", current_user=_User(uid), db=db)
        assert pr.total >= 1
        p = next(x for x in pr.items if x.problem_type == "profitable_ad_candidate")
        assert p.severity == "high" and p.estimated_effect_type == "revenue_gain"
        assert "thresholds_used" in (p.evidence or {}) and p.detected_at
    _run(go())


# ── 6. audits history ────────────────────────────────────────────────────────

def test_audits_history():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        au = await growth_audits(listing_id="L1", current_user=_User(uid), db=db)
        assert au.total == 1
        a = au.items[0].model_dump()
        assert a["status"] == "completed" and a["triggered_by"] == "manual"
        for bad in ("score", "growth_score", "internal_health_index", "snapshot_hash"):
            assert bad not in a
    _run(go())


# ── 7. marketplace agnostic ──────────────────────────────────────────────────

def test_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await _seed_finance(db, uid, marketplace=mp); await db.commit()
            resp = await _audit(db, uid, mp=mp)
            assert resp.ok and resp.total_problems >= 1
            sg = await growth_signals(current_user=_User(uid), db=db)
            assert any(f":{mp}:SKU1" in (x.insight_key or "") for x in sg.items)
    _run(go())


# ── 8. no forecast / competitor / AI fields leak in responses ────────────────

def test_no_forecast_competitor_ai_leak():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        sg = await growth_signals(current_user=_User(uid), db=db)
        blob = " ".join(str(x.model_dump()) for x in sg.items).lower()
        for bad in ("forecast", "expected_revenue", "competitor", "market_size", "predict", "ai_"):
            assert bad not in blob
    _run(go())


# ── 9. routes mounted in main.py ─────────────────────────────────────────────

def test_routes_mounted():
    paths = {getattr(r, "path", None) for r in gr.router.routes}
    assert {"/growth/audit", "/growth/overview", "/growth/signals",
            "/growth/problems", "/growth/audits"} <= paths
    import main
    app_paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/growth/audit" in app_paths
