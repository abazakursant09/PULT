"""The /admin/promo* endpoints create and toggle GLOBAL discount codes and read marketing
analytics. They used to be gated by get_current_user alone, so any authenticated seller could
mint a 100%-off code against live payments. They are now behind the same machine-to-machine
INTERNAL_API_KEY that decisions.py uses for its cron endpoint. These tests fail the day a
normal session can reach them again.
"""
import asyncio
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from dependencies import get_current_user
import models  # registers tables
from models.promo_code import PromoCode
from routers import promo as promo_router

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db, uid):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(promo_router.router, prefix="/api")
    # A fully authenticated seller — exactly what the old gate accepted.
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def _setup():
    uid = str(uuid.uuid4())
    db = _run(_new_db())
    return db, uid, _client(db, uid)


ADMIN_GETS = ["/api/admin/promo", "/api/admin/promo/stats"]
CREATE_BODY = {"code": "FREE100", "type": "percent", "value": 100.0}


def test_authenticated_seller_cannot_list_or_read_stats():
    _db, _uid, c = _setup()
    for path in ADMIN_GETS:
        r = c.get(path)
        assert r.status_code == 403, f"{path} leaked to a seller: {r.status_code}"


def test_authenticated_seller_cannot_create_a_promo():
    db, _uid, c = _setup()
    r = c.post("/api/admin/promo", json=CREATE_BODY)
    assert r.status_code == 403
    # And nothing was written.
    rows = _run(db.execute(select(PromoCode))).scalars().all()
    assert rows == []


def test_authenticated_seller_cannot_toggle_a_promo():
    db, _uid, c = _setup()
    promo = PromoCode(code="X", type="percent", value=10.0, applicable_plans="all",
                      is_active=True)
    _run(_add(db, promo))
    r = c.patch(f"/api/admin/promo/{promo.id}/toggle")
    assert r.status_code == 403
    # State unchanged.
    fresh = _run(db.get(PromoCode, promo.id))
    assert fresh.is_active is True


def test_wrong_internal_key_is_rejected():
    _db, _uid, c = _setup()
    r = c.get("/api/admin/promo", headers={"X-Internal-Key": "not-the-key"})
    assert r.status_code == 403


def test_correct_internal_key_reaches_the_endpoint():
    # The machine path still works when the shared secret is set and presented.
    _db, _uid, c = _setup()
    prev = settings.internal_api_key
    settings.internal_api_key = "test-internal-secret"
    try:
        r = c.get("/api/admin/promo", headers={"X-Internal-Key": "test-internal-secret"})
        assert r.status_code == 200
        assert r.json() == []
    finally:
        settings.internal_api_key = prev


def test_fail_closed_when_no_key_configured():
    # Unset secret must reject everyone, even a caller presenting an empty header.
    _db, _uid, c = _setup()
    prev = settings.internal_api_key
    settings.internal_api_key = ""
    try:
        assert c.get("/api/admin/promo", headers={"X-Internal-Key": ""}).status_code == 403
        assert c.get("/api/admin/promo").status_code == 403
    finally:
        settings.internal_api_key = prev


# The seller-facing routes must still work for a normal session — this is not a lockout.
def test_seller_can_still_validate_a_promo():
    db, _uid, c = _setup()
    _run(_add(db, PromoCode(code="SELLERSEES", type="percent", value=10.0,
                            applicable_plans="all", is_active=True)))
    r = c.post("/api/promo/validate", json={"code": "SELLERSEES", "plan": "master"})
    assert r.status_code == 200
    assert r.json()["valid"] is True


async def _add(db, obj):
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
