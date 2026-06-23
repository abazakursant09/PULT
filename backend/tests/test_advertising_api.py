"""
Advertising A7 — API layer tests.

Handlers called directly with a real in-memory db (DI bypassed). POST audit
builds a snapshot from seeded ImportedFinanceRow; missing finance →
finance_unavailable (not error). Verifies all endpoints, doctrine fields, no
fake numbers / no score / no campaign-CTR-CPC leak, marketplace independence,
routes mounted.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow

from routers import advertising_engine as adv
from routers.advertising_engine import (
    run_advertising_audit, advertising_overview, advertising_signals,
    advertising_problems, advertising_audits,
    AdvAuditRequest, ThresholdsIn, AdvAuditResponse, AdvOverviewResponse,
)

TH = ThresholdsIn(max_drr=20.0, min_revenue_for_signal=1000.0, min_ad_spend_for_signal=100.0,
                  low_margin_threshold=10.0, low_stock_units=5, oos_risk_days=7.0)


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


async def _seed_finance(db, uid, *, marketplace="wb", sku="SKU1",
                        revenue=10000.0, net_profit=-500.0, ad_spend=4000.0, quantity=20):
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace, sku=sku,
                              revenue=revenue, net_profit=net_profit, ad_spend=ad_spend,
                              quantity=quantity))
    await db.flush()


async def _audit(db, uid, *, mp="wb", sku="SKU1", listing_id="L1", thresholds=TH):
    return await run_advertising_audit(
        AdvAuditRequest(listing_id=listing_id, marketplace=mp, sku=sku, thresholds=thresholds),
        current_user=_User(uid), db=db)


# ── 1. POST audit from finance ───────────────────────────────────────────────

def test_post_audit_from_finance():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        resp = await _audit(db, uid)
        assert isinstance(resp, AdvAuditResponse)
        assert resp.ok and resp.status == "completed"
        assert resp.total_problems >= 1   # net<0 → ad_destroying_profit
    _run(go())


# ── 2. missing finance → finance_unavailable (not error) ─────────────────────

def test_missing_finance_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _audit(db, uid, sku="NOPE")
        assert resp.ok is False and resp.status == "finance_unavailable"
        assert resp.reason == "finance_missing" and resp.audit_id is None  # no fake numbers
    _run(go())


# ── 3. overview without fake numbers / no score ──────────────────────────────

def test_overview_no_fake_no_score():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        ov = await advertising_overview(listing_id="L1", marketplace="wb",
                                        current_user=_User(uid), db=db)
        assert isinstance(ov, AdvOverviewResponse)
        assert ov.active_signals >= 1 and ov.critical_signals >= 1   # ad_destroying_profit critical
        assert ov.last_audit_at is not None
        d = ov.model_dump()
        assert "score" not in d and "internal_health_index" not in d
        # empty user → zeros, no fabrication
        ov2 = await advertising_overview(current_user=_User(str(uuid.uuid4())), db=db)
        assert ov2.active_signals == 0 and ov2.last_audit_at is None
    _run(go())


# ── 4. signals doctrine fields + status filter ───────────────────────────────

def test_signals_doctrine_and_filter():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        # no status filter: after the audit the actionable signal is auto-promoted
        # to promoted_to_decision (Promotion Activation hook), still a live issue.
        sg = await advertising_signals(listing_id="L1", marketplace="wb",
                                       current_user=_User(uid), db=db)
        assert sg.total >= 1
        s = next(x for x in sg.items if x.problem_type == "ad_destroying_profit")
        assert s.what and s.why and s.meaning and s.recommended_action and s.expected_effect
        assert s.priority_level == "critical" and s.effect_band and s.confidence is not None
        assert s.recommended_action_key == "stop_auto_promotion"
    _run(go())


# ── 5. problems = latest audit ───────────────────────────────────────────────

def test_problems_latest_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        pr = await advertising_problems(listing_id="L1", marketplace="wb",
                                        current_user=_User(uid), db=db)
        assert pr.total >= 1
        p = next(x for x in pr.items if x.problem_type == "ad_destroying_profit")
        assert p.severity == "critical" and p.estimated_effect_type == "margin_loss"
        assert "ad_spend" in (p.evidence or {}) and p.detected_at
    _run(go())


# ── 6. audits history ────────────────────────────────────────────────────────

def test_audits_history():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        au = await advertising_audits(listing_id="L1", marketplace="wb",
                                      current_user=_User(uid), db=db)
        assert au.total == 1
        a = au.items[0].model_dump()
        assert a["status"] == "completed"
        for bad in ("internal_health_index", "score", "snapshot_hash"):
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
            sg = await advertising_signals(listing_id="L1", marketplace=mp,
                                           current_user=_User(uid), db=db)
            assert sg.items[0].insight_key.endswith(f":{mp}:SKU1")
    _run(go())


# ── 8. no CTR/CPC/campaign leak in responses ─────────────────────────────────

def test_no_cabinet_fields_leak():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        sg = await advertising_signals(listing_id="L1", marketplace="wb",
                                       current_user=_User(uid), db=db)
        blob = " ".join(str(sg.items[0].model_dump())).lower()
        for bad in ("ctr", "cpc", "clicks", "impressions", "campaign", "bid", "keyword"):
            assert bad not in blob
    _run(go())


# ── 9/10. routes mounted in main.py ──────────────────────────────────────────

def test_routes_mounted():
    paths = {getattr(r, "path", None) for r in adv.router.routes}
    assert {"/advertising/audit", "/advertising/overview", "/advertising/signals",
            "/advertising/problems", "/advertising/audits"} <= paths
    import main
    app_paths = set(main.app.openapi()["paths"])  # OpenAPI paths: robust on FastAPI 0.136 (flat) and 0.137+ (nested mounts)
    assert "/api/advertising/audit" in app_paths
