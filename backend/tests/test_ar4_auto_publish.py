"""AR4 — safe auto-review automation foundation.

Proves the six security gates on the L4 auto-publisher and the guard: SAFE-only (a 5★ complaint is
RISK and never auto-publishes), marketplace scope, min_rating, caps, backoff + attempt ceiling,
circuit breaker, soft-deleted-seller exclusion, and that automation is OFF by default. The executor
is mocked — no real marketplace call. Manual /publish, the ReviewProvider and /sync are untouched.
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import datetime, timedelta

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # register tables
from models.automation_rule import AutomationRule
from models.review_response import ReviewResponse
from models.product import Product
from models.user import User
from models.execution_log import ExecutionLog
from config import settings
import tasks.auto_publish_reviews as ap
from services.marketplace import guard as guard_mod
from services.marketplace.errors import ExecutionError

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _install(monkeypatch, db, *, ok=True, error="TIMEOUT"):
    """Point auto_publish at our session and a controllable executor. Returns the call recorder."""
    monkeypatch.setattr(settings, "automation_enabled", True)

    @asynccontextmanager
    async def _factory():
        yield db      # do not close — the test inspects the same session afterwards
    monkeypatch.setattr(ap, "AsyncSessionLocal", _factory)

    calls = {"n": 0, "payloads": []}

    async def _fake_execute(*, db, user_id, action_type, payload, mode, insight_key,
                            idempotency_key, rule=None):
        calls["n"] += 1
        calls["payloads"].append(payload)
        # Run the REAL guard so the SAFE/negative/cap gates are exercised end to end. Mirror the
        # real executor: a guard rejection is caught and returned as a non-ok result (not raised).
        try:
            await guard_mod.check(db=db, user_id=user_id, action_type=action_type,
                                  payload=payload, mode=mode, rule=rule)
        except ExecutionError as e:
            log = ExecutionLog(id=str(uuid.uuid4()), user_id=user_id, action_type=action_type,
                               mode=mode, status="rejected", error_code=e.code,
                               idempotency_key=idempotency_key, payload={})
            db.add(log); await db.flush()
            return SimpleNamespace(ok=False, error=e.code, log_id=log.id)
        log = ExecutionLog(id=str(uuid.uuid4()), user_id=user_id, action_type=action_type,
                           mode=mode, status="success" if ok else "failed",
                           error_code=None if ok else error, idempotency_key=idempotency_key,
                           payload={})
        db.add(log); await db.flush()
        return SimpleNamespace(ok=ok, error=None if ok else error, log_id=log.id)

    monkeypatch.setattr(ap.executor, "execute", _fake_execute)
    return calls


async def _seed(db, *, marketplace="wildberries", rating=5, safety="SAFE", text="Спасибо!",
                deleted=False, guard=None):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True,
                deleted_at=(datetime.utcnow() if deleted else None)))
    p = Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="NM", marketplace=marketplace)
    db.add(p)
    r = ReviewResponse(id=str(uuid.uuid4()), product_id=p.id, marketplace=marketplace,
                       external_review_id=str(uuid.uuid4()), rating=rating, safety_category=safety,
                       response_text=text, status="approved")
    db.add(r)
    db.add(AutomationRule(id=str(uuid.uuid4()), user_id=uid, contour="reputation",
                          action_type="publish_review_response", mode="auto", enabled=True,
                          guard=guard or {}))
    await db.commit()
    return uid, r


# ── Defaults ─────────────────────────────────────────────────────────────────

def test_automation_off_by_default(monkeypatch):
    db = _run(_new_db())
    _run(_seed(db))
    monkeypatch.setattr(settings, "automation_enabled", False)
    out = _run(ap.run_auto_publish_reviews())
    assert out["ran"] is False


# ── Safety gate (G1) ─────────────────────────────────────────────────────────

def test_risk_5star_complaint_never_auto_publishes(monkeypatch):
    db = _run(_new_db())
    uid, r = _run(_seed(db, rating=5, safety="RISK"))   # 5★ but complaint → RISK
    _install(monkeypatch, db)
    out = _run(ap.run_auto_publish_reviews())
    assert out["published"] == 0                         # candidate query excludes non-SAFE
    assert _run(db.get(ReviewResponse, r.id)).status == "approved"   # untouched


def test_guard_blocks_unsafe_even_if_query_let_it_through(monkeypatch):
    # Second belt: call the guard directly with a non-SAFE payload in L4.
    db = _run(_new_db())
    with pytest.raises(ExecutionError):
        _run(guard_mod.check(db=db, user_id="u", action_type="publish_review_response",
                             payload={"rating": 5, "safety_category": "ATTENTION"},
                             mode="automated_l4", rule={"enabled": True, "guard": {}}))


def test_safe_review_publishes_when_enabled(monkeypatch):
    db = _run(_new_db())
    uid, r = _run(_seed(db, rating=5, safety="SAFE"))
    _install(monkeypatch, db)
    out = _run(ap.run_auto_publish_reviews())
    assert out["published"] == 1
    assert _run(db.get(ReviewResponse, r.id)).status == "published"


# ── Policy (G4/G6 + min_rating) ──────────────────────────────────────────────

def test_marketplace_scope(monkeypatch):
    db = _run(_new_db())
    _run(_seed(db, marketplace="wildberries", guard={"marketplaces": ["ozon"]}))
    _install(monkeypatch, db)
    out = _run(ap.run_auto_publish_reviews())
    assert out["published"] == 0                          # WB review, rule scoped to ozon


def test_min_rating(monkeypatch):
    db = _run(_new_db())
    _run(_seed(db, rating=4, safety="SAFE", guard={"min_rating": 5}))
    _install(monkeypatch, db)
    assert _run(ap.run_auto_publish_reviews())["published"] == 0


def test_daily_cap_blocks(monkeypatch):
    db = _run(_new_db())
    uid, r = _run(_seed(db, guard={"daily_cap": 1}))
    # pre-existing success today → cap already reached
    _run(_add(db, ExecutionLog(id=str(uuid.uuid4()), user_id=uid,
                               action_type="publish_review_response", mode="automated_l4",
                               status="success", payload={})))
    _install(monkeypatch, db)
    out = _run(ap.run_auto_publish_reviews())
    assert _run(db.get(ReviewResponse, r.id)).status != "published"   # guard DAILY_CAP → not ok


# ── Retry / backoff / ceiling (G2/G3) ────────────────────────────────────────

def test_failed_attempt_increments_and_backs_off(monkeypatch):
    db = _run(_new_db())
    uid, r = _run(_seed(db))
    _install(monkeypatch, db, ok=False)
    _run(ap.run_auto_publish_reviews())
    row = _run(db.get(ReviewResponse, r.id))
    assert row.publication_attempts == 1
    assert row.failure_reason and row.retry_next_at is not None and row.retry_next_at > datetime.utcnow()
    assert row.status == "approved"                       # still publishable, not terminal yet


def test_backoff_defers_next_attempt(monkeypatch):
    db = _run(_new_db())
    uid, r = _run(_seed(db))
    _install(monkeypatch, db, ok=False)
    _run(ap.run_auto_publish_reviews())                   # attempt 1 → retry_next_at future
    calls = _install(monkeypatch, db, ok=False)
    out = _run(ap.run_auto_publish_reviews())             # immediate re-run
    assert out["deferred"] == 1 and calls["n"] == 0       # not re-attempted (backoff respected)


def test_attempt_ceiling_is_terminal(monkeypatch):
    db = _run(_new_db())
    uid, r = _run(_seed(db))
    row = _run(db.get(ReviewResponse, r.id))
    row.publication_attempts = ap._MAX_ATTEMPTS - 1
    _run(_commit(db))
    _install(monkeypatch, db, ok=False)
    _run(ap.run_auto_publish_reviews())
    row = _run(db.get(ReviewResponse, r.id))
    assert row.publication_attempts == ap._MAX_ATTEMPTS
    assert row.status == "failed" and row.retry_next_at is None   # terminal, never re-selected


# ── Circuit breaker ──────────────────────────────────────────────────────────

def test_breaker_skips_rule_after_consecutive_failures(monkeypatch):
    db = _run(_new_db())
    uid, r = _run(_seed(db))
    for _ in range(ap._BREAKER_FAILS):
        _run(_add(db, ExecutionLog(id=str(uuid.uuid4()), user_id=uid,
                                   action_type="publish_review_response", mode="automated_l4",
                                   status="failed", payload={})))
    calls = _install(monkeypatch, db)
    _run(ap.run_auto_publish_reviews())
    assert calls["n"] == 0                                 # breaker tripped → rule skipped entirely


# ── Security: soft-deleted seller excluded ───────────────────────────────────

def test_deleted_seller_excluded(monkeypatch):
    db = _run(_new_db())
    _run(_seed(db, deleted=True))
    _install(monkeypatch, db)
    assert _run(ap.run_auto_publish_reviews())["published"] == 0


# ── Audit ────────────────────────────────────────────────────────────────────

def test_audit_log_created_on_publish(monkeypatch):
    db = _run(_new_db())
    uid, r = _run(_seed(db))
    _install(monkeypatch, db)
    _run(ap.run_auto_publish_reviews())
    logs = _run(db.execute(select(ExecutionLog).where(
        ExecutionLog.user_id == uid,
        ExecutionLog.action_type == "publish_review_response",
        ExecutionLog.mode == "automated_l4"))).scalars().all()
    assert len(logs) == 1 and logs[0].status == "success"


async def _add(db, obj):
    db.add(obj); await db.commit()


async def _commit(db):
    await db.commit()
