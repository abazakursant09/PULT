"""AR-CONTROL — per-connection Auto Reviews control + consent + server-side gate.

Covers Inal's mandate: the seller enables/disables automation and chooses the mode per marketplace
connection, only after EXPLICIT consent; the backend enforces this on the server (never a trusted
frontend checkbox), re-reading the rule immediately before every automatic publish; default OFF;
negative never auto; WB + Ozon work; Yandex stays closed until AR-YM.
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
import models  # noqa: F401  register tables
from models.user import User
from models.product import Product
from models.review_response import ReviewResponse
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from models.execution_log import ExecutionLog
from config import settings
import tasks.auto_publish_reviews as ap
from services.marketplace import review_automation_gate as gate
from services.marketplace.review_automation_gate import AutoPublishBlocked, CONSENT_VERSION
from routers import automation as automation_router

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_seller(db, *, marketplace="wildberries", conn_status="connected",
                       scopes=("feedbacks",)):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                 status=conn_status, scopes=list(scopes))
    db.add(conn)
    await db.commit()
    return uid, conn


async def _seed_rule(db, uid, conn, *, enabled=False, mode="confirm", consent=True,
                     revoked=False):
    rule = AutomationRule(
        id=str(uuid.uuid4()), user_id=uid, contour="reputation",
        action_type="publish_review_response", mode=mode, enabled=enabled,
        guard={}, connection_id=conn.id,
        consent_at=(datetime.utcnow() if consent else None),
        consent_version=(CONSENT_VERSION if consent else None),
        consent_revoked_at=(datetime.utcnow() if revoked else None),
    )
    db.add(rule)
    await db.commit()
    return rule


async def _seed_review(db, uid, *, marketplace="wildberries", rating=5, safety="SAFE"):
    p = Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="NM", marketplace=marketplace)
    db.add(p)
    r = ReviewResponse(id=str(uuid.uuid4()), product_id=p.id, marketplace=marketplace,
                       external_review_id=str(uuid.uuid4()), rating=rating, safety_category=safety,
                       response_text="Спасибо!", status="approved")
    db.add(r)
    await db.commit()
    return r


def _client(db, uid):
    app = FastAPI()
    app.include_router(automation_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    return TestClient(app)


def _install_worker(monkeypatch, db, *, ok=True, status="success", error=None):
    monkeypatch.setattr(settings, "automation_enabled", True)

    @asynccontextmanager
    async def _factory():
        yield db
    monkeypatch.setattr(ap, "AsyncSessionLocal", _factory)

    calls = {"n": 0}

    async def _fake_execute(*, db, user_id, action_type, payload, mode, insight_key,
                            idempotency_key, rule=None):
        calls["n"] += 1
        log = ExecutionLog(id=str(uuid.uuid4()), user_id=user_id, action_type=action_type,
                           mode=mode, status=status, idempotency_key=idempotency_key, payload={})
        db.add(log)
        await db.flush()
        return SimpleNamespace(ok=ok, status=status, error=error, log_id=log.id)

    monkeypatch.setattr(ap.executor, "execute", _fake_execute)
    return calls


# ── 1. Default OFF ────────────────────────────────────────────────────────────
def test_1_default_off_on_create():
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    r = _client(db, uid).post("/api/automation-rules", json={
        "contour": "reputation", "action_type": "publish_review_response",
        "connection_id": conn.id})
    assert r.status_code == 201
    body = r.json()
    assert body["enabled"] is False and body["mode"] == "confirm"
    assert body["consent_at"] is None


# ── 2. Cannot enable without consent ─────────────────────────────────────────
def test_2_cannot_enable_without_consent():
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    rule = _run(_seed_rule(db, uid, conn, enabled=False, consent=False))
    r = _client(db, uid).patch(f"/api/automation-rules/{rule.id}/toggle")
    assert r.status_code == 409 and "NO_CONSENT" in r.json()["detail"]
    assert _run(db.get(AutomationRule, rule.id)).enabled is False


# ── 3. Cannot manage another seller's connection ─────────────────────────────
def test_3_cannot_use_foreign_connection():
    db = _run(_new_db())
    uid_a, conn_a = _run(_seed_seller(db))
    uid_b, _ = _run(_seed_seller(db))
    # seller B tries to create a rule bound to seller A's connection
    r = _client(db, uid_b).post("/api/automation-rules", json={
        "contour": "reputation", "action_type": "publish_review_response",
        "connection_id": conn_a.id})
    assert r.status_code == 404


# ── 4/5. Consent allows enable (confirm) then switch to auto ─────────────────
def test_4_5_consent_enables_confirm_then_auto(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    c = _client(db, uid)
    rule = _run(_seed_rule(db, uid, conn, enabled=False, consent=False))
    # grant consent
    assert c.post(f"/api/automation-rules/{rule.id}/consent").status_code == 200
    # enable (confirm mode)
    t = c.patch(f"/api/automation-rules/{rule.id}/toggle")
    assert t.status_code == 200 and t.json()["enabled"] is True and t.json()["mode"] == "confirm"
    # switch to auto — allowed only while the system kill switch is on
    monkeypatch.setattr(settings, "automation_enabled", True)
    m = c.patch(f"/api/automation-rules/{rule.id}/mode", json={"mode": "auto"})
    assert m.status_code == 200 and m.json()["mode"] == "auto"


# ── Kill switch honesty: no false "on" while global automation is off ─────────
def test_kill_switch_blocks_enabling_auto(monkeypatch):
    monkeypatch.setattr(settings, "automation_enabled", False)   # global OFF
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    c = _client(db, uid)
    # a consented rule already in auto mode, currently disabled
    rule = _run(_seed_rule(db, uid, conn, enabled=False, mode="auto", consent=True))
    r = c.patch(f"/api/automation-rules/{rule.id}/toggle")
    assert r.status_code == 409 and "KILL_SWITCH" in r.json()["detail"]
    assert _run(db.get(AutomationRule, rule.id)).enabled is False   # stayed off


def test_kill_switch_allows_confirm_enable(monkeypatch):
    monkeypatch.setattr(settings, "automation_enabled", False)   # global OFF
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    c = _client(db, uid)
    rule = _run(_seed_rule(db, uid, conn, enabled=False, mode="confirm", consent=True))
    r = c.patch(f"/api/automation-rules/{rule.id}/toggle")     # confirm never needs the worker
    assert r.status_code == 200 and r.json()["enabled"] is True


def test_kill_switch_blocks_switch_to_auto_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "automation_enabled", False)   # global OFF
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    c = _client(db, uid)
    rule = _run(_seed_rule(db, uid, conn, enabled=True, mode="confirm", consent=True))
    r = c.patch(f"/api/automation-rules/{rule.id}/mode", json={"mode": "auto"})
    assert r.status_code == 409 and "KILL_SWITCH" in r.json()["detail"]
    assert _run(db.get(AutomationRule, rule.id)).mode == "confirm"   # unchanged


def test_availability_reports_global_flag(monkeypatch):
    db = _run(_new_db())
    uid, _ = _run(_seed_seller(db))
    c = _client(db, uid)
    monkeypatch.setattr(settings, "automation_enabled", False)
    assert c.get("/api/automation-rules/availability").json() == {"automation_enabled": False}
    monkeypatch.setattr(settings, "automation_enabled", True)
    assert c.get("/api/automation-rules/availability").json() == {"automation_enabled": True}


# ── 6. Revoke disables the rule in the same transaction ──────────────────────
def test_6_revoke_disables_rule():
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    rule = _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    r = _client(db, uid).post(f"/api/automation-rules/{rule.id}/consent/revoke")
    assert r.status_code == 200
    fresh = _run(db.get(AutomationRule, rule.id))
    assert fresh.enabled is False and fresh.consent_revoked_at is not None


# ── 7. Disable blocks new auto publishes ─────────────────────────────────────
def test_7_disabled_blocks_publish(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    _run(_seed_rule(db, uid, conn, enabled=False, mode="auto", consent=True))
    _run(_seed_review(db, uid))
    calls = _install_worker(monkeypatch, db)
    out = _run(ap.run_auto_publish_reviews())
    assert out["published"] == 0 and calls["n"] == 0


# ── 8. Global kill switch blocks ─────────────────────────────────────────────
def test_8_kill_switch_blocks(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    rule = _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    monkeypatch.setattr(settings, "automation_enabled", False)
    with __import__("pytest").raises(AutoPublishBlocked) as e:
        _run(gate.assert_auto_publish_allowed(db, rule.id, uid))
    assert e.value.reason == "KILL_SWITCH"


# ── 9. Unsupported marketplace (Yandex) blocked ──────────────────────────────
def test_9_yandex_marketplace_unsupported(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db, marketplace="yandex_market"))
    rule = _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    monkeypatch.setattr(settings, "automation_enabled", True)
    with __import__("pytest").raises(AutoPublishBlocked) as e:
        _run(gate.assert_auto_publish_allowed(db, rule.id, uid))
    assert e.value.reason == "MARKETPLACE_UNSUPPORTED"


# ── 10. No feedbacks permission blocked ──────────────────────────────────────
def test_10_no_feedbacks_permission(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db, scopes=("prices",)))   # no feedbacks
    rule = _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    monkeypatch.setattr(settings, "automation_enabled", True)
    with __import__("pytest").raises(AutoPublishBlocked) as e:
        _run(gate.assert_auto_publish_allowed(db, rule.id, uid))
    assert e.value.reason == "NO_FEEDBACKS_PERMISSION"


# ── 11. Negative never auto-published ────────────────────────────────────────
def test_11_negative_never_auto(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    _run(_seed_review(db, uid, rating=2, safety="RISK"))
    calls = _install_worker(monkeypatch, db)
    out = _run(ap.run_auto_publish_reviews())
    assert out["published"] == 0   # excluded by SAFE-only + rating>3 candidate filter


# ── 12. SAFE publishes with enabled+auto+consent ─────────────────────────────
def test_12_safe_publishes(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    r = _run(_seed_review(db, uid))
    _install_worker(monkeypatch, db)
    out = _run(ap.run_auto_publish_reviews())
    assert out["published"] == 1
    assert _run(db.get(ReviewResponse, r.id)).status == "published"


# ── 13. Two runs do not double-publish (existing idempotency at review level) ─
def test_13_no_double_publish(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    _run(_seed_review(db, uid))
    calls = _install_worker(monkeypatch, db)
    _run(ap.run_auto_publish_reviews())
    _run(ap.run_auto_publish_reviews())   # second tick
    assert calls["n"] == 1                # published row is no longer a candidate


# ── 14. Timeout → ambiguous/needs_reconcile, terminal, not retried ───────────
def test_14_timeout_ambiguous_terminal(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db))
    _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    r = _run(_seed_review(db, uid))
    _install_worker(monkeypatch, db, ok=False, status="ambiguous")
    out = _run(ap.run_auto_publish_reviews())
    row = _run(db.get(ReviewResponse, r.id))
    assert out["terminal"] == 1 and row.status == "failed" and row.retry_next_at is None


# ── 15. Manual scenarios unaffected: provider registry intact ────────────────
def test_15_manual_provider_registry_intact():
    from services.marketplace.reviews import get_review_provider
    assert get_review_provider("wildberries").supports_reviews() is True
    assert get_review_provider("ozon").supports_reviews() is True


# ── 16. WB works through the gate ────────────────────────────────────────────
def test_16_wb_gate_allows(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db, marketplace="wildberries"))
    rule = _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    monkeypatch.setattr(settings, "automation_enabled", True)
    got_rule, got_conn = _run(gate.assert_auto_publish_allowed(db, rule.id, uid))
    assert got_conn.marketplace == "wildberries"


# ── 17. Ozon works through the gate + publishes ──────────────────────────────
def test_17_ozon_gate_allows_and_publishes(monkeypatch):
    db = _run(_new_db())
    uid, conn = _run(_seed_seller(db, marketplace="ozon"))
    _run(_seed_rule(db, uid, conn, enabled=True, mode="auto", consent=True))
    _run(_seed_review(db, uid, marketplace="ozon"))
    _install_worker(monkeypatch, db)
    out = _run(ap.run_auto_publish_reviews())
    assert out["published"] == 1


# ── 18. Yandex stays closed until AR-YM ──────────────────────────────────────
def test_18_yandex_closed():
    from services.marketplace.reviews import get_review_provider
    assert get_review_provider("yandex") is None
    assert get_review_provider("yandex_market") is None
    assert gate.marketplace_supports_reviews("yandex_market") is False
