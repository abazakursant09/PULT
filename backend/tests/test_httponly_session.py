"""
SECURITY-2B-2 — the browser session is an HttpOnly cookie; no Bearer; Origin-CSRF on mutations.

Proves the cookie contract (name/attrs per environment), that login/verify/MFA set the cookie and never
return a token in JSON, that /me is cookie-authorised (Bearer is not accepted), that logout clears the
cookie, and that the Origin/Referer CSRF guard blocks cross-site mutations while exempting the
server-to-server endpoints.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from rate_limit import limit_auth
import models  # noqa: F401
from models.user import User
from models.mfa_secret import MFASecret
from routers import auth as auth_router
from routers.auth import hash_password, create_access_token, create_mfa_pending_token
from dependencies import get_current_user
from csrf import OriginCsrfMiddleware

_LOOP = asyncio.new_event_loop()
ORIGIN = "http://localhost:3000"            # an allowed dev origin
GOOD_PW = "Passw0rdOk"


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")

    @app.post("/api/echo/mutate")            # a stand-in cookie-auth mutation for CSRF tests
    async def _mutate(user: User = Depends(get_current_user)):
        return {"ok": True, "user": str(user.id)}

    @app.post("/api/payments/webhook")       # exempt server-to-server
    async def _webhook():
        return {"ok": True}

    @app.post("/api/admin/promo")            # exempt internal-key prefix
    async def _admin():
        return {"ok": True}

    app.add_middleware(OriginCsrfMiddleware)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[limit_auth] = lambda: None
    return TestClient(app)


async def _seed(db, *, verified=True, email=None, mfa=False):
    addr = email or f"{uuid.uuid4()}@example.com"
    u = User(id=str(uuid.uuid4()), email=addr, name="S", hashed_password=hash_password(GOOD_PW),
             is_verified=verified, verification_token=None if verified else "verify-me")
    db.add(u)
    if mfa:
        db.add(MFASecret(user_id=u.id, secret="ENCSECRET", enabled=True))
    await db.commit()
    return u


def _dev_name():
    return "pult_session_dev"


def _prod_name():
    return "__Host-pult_session"


# ── cookie is set on login, no token in JSON ─────────────────────────────────
def test_login_sets_httponly_cookie_no_token_in_json():
    db = _run(_new_db())
    _run(_seed(db, email="a@b.c"))
    c = _client(db)
    r = c.post("/api/auth/login", json={"email": "a@b.c", "password": GOOD_PW}, headers={"origin": ORIGIN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" not in body and "token" not in body
    assert body["user"]["email"] == "a@b.c"
    sc = r.headers.get("set-cookie", "")
    assert _dev_name() in sc
    assert "httponly" in sc.lower()
    assert "samesite=lax" in sc.lower()
    assert "path=/" in sc.lower()
    assert "domain=" not in sc.lower()          # host-only
    assert "secure" not in sc.lower()           # dev
    assert r.headers.get("cache-control") == "no-store"


def test_prod_cookie_is_host_prefixed_and_secure(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    db = _run(_new_db())
    _run(_seed(db, email="p@b.c"))
    c = _client(db)
    r = c.post("/api/auth/login", json={"email": "p@b.c", "password": GOOD_PW}, headers={"origin": ORIGIN})
    sc = r.headers.get("set-cookie", "")
    assert _prod_name() in sc
    assert "secure" in sc.lower() and "httponly" in sc.lower()
    assert "samesite=lax" in sc.lower() and "path=/" in sc.lower()
    assert "domain=" not in sc.lower()


def test_verify_email_sets_cookie_no_token():
    db = _run(_new_db())
    u = _run(_seed(db, verified=False, email="v@b.c"))
    u.verification_token = "vtok"
    _run(db.commit())
    c = _client(db)
    r = c.get("/api/auth/verify-email", params={"token": "vtok"})
    assert r.status_code == 200 and "access_token" not in r.json()
    assert _dev_name() in r.headers.get("set-cookie", "")


def test_mfa_login_sets_cookie(monkeypatch):
    db = _run(_new_db())
    u = _run(_seed(db, email="m@b.c", mfa=True))
    async def _claim_ok(*a, **k):   # SECURITY-2C-3A — login now consumes the step via claim_totp_step
        return True
    monkeypatch.setattr(auth_router, "claim_totp_step", _claim_ok)
    monkeypatch.setattr(auth_router, "load_secret", lambda s: "x")
    # SECURITY-2C-4A — the in-memory limit_mfa is gone; the durable mfa_login throttle runs against the
    # create_all auth_rate_limit_buckets table (a single attempt is well under the limit).
    c = _client(db)
    pending = create_mfa_pending_token(str(u.id))
    r = c.post("/api/auth/login/mfa", json={"mfa_token": pending, "code": "123456"},
               headers={"origin": ORIGIN})
    assert r.status_code == 200, r.text
    assert "access_token" not in r.json()
    assert _dev_name() in r.headers.get("set-cookie", "")


# ── /me is cookie-only; Bearer is not accepted ───────────────────────────────
def test_me_with_cookie_ok_without_401_bearer_rejected():
    db = _run(_new_db())
    u = _run(_seed(db, email="me@b.c"))
    c = _client(db)
    # no cookie → 401
    assert c.get("/api/auth/me").status_code == 401
    # Bearer only (no cookie) → 401 (fallback removed)
    tok = create_access_token(str(u.id), u.token_version)
    assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    # valid cookie → 200
    c.cookies.set(_dev_name(), tok)
    r = c.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["email"] == "me@b.c"


def test_garbage_and_expired_cookie_401():
    db = _run(_new_db())
    u = _run(_seed(db, email="g@b.c"))
    c = _client(db)
    c.cookies.set(_dev_name(), "not-a-jwt")
    assert c.get("/api/auth/me").status_code == 401
    expired = jwt.encode({"sub": str(u.id), "exp": datetime.utcnow() - timedelta(hours=1)},
                         settings.secret_key, algorithm=settings.algorithm)
    c.cookies.set(_dev_name(), expired)
    assert c.get("/api/auth/me").status_code == 401


# ── logout clears cookie, idempotent ─────────────────────────────────────────
def test_logout_clears_cookie_idempotent():
    db = _run(_new_db())
    u = _run(_seed(db, email="lo@b.c"))
    c = _client(db)
    c.cookies.set(_dev_name(), create_access_token(str(u.id), u.token_version))
    r = c.post("/api/auth/logout", headers={"origin": ORIGIN})
    assert r.status_code == 204
    sc = r.headers.get("set-cookie", "").lower()
    assert _dev_name() in sc and ("max-age=0" in sc or "expires=" in sc)
    # repeat is safe
    assert c.post("/api/auth/logout", headers={"origin": ORIGIN}).status_code == 204


# ── Origin/Referer CSRF guard ────────────────────────────────────────────────
def _auth_client():
    db = _run(_new_db())
    u = _run(_seed(db, email="csrf@b.c"))
    c = _client(db)
    c.cookies.set(_dev_name(), create_access_token(str(u.id), u.token_version))
    return c


def test_csrf_foreign_origin_blocked():
    c = _auth_client()
    r = c.post("/api/echo/mutate", headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_csrf_null_origin_blocked():
    c = _auth_client()
    assert c.post("/api/echo/mutate", headers={"origin": "null"}).status_code == 403


def test_csrf_lookalike_domain_blocked():
    c = _auth_client()
    assert c.post("/api/echo/mutate",
                  headers={"origin": "http://localhost:3000.evil.example"}).status_code == 403


def test_csrf_allowed_origin_passes():
    c = _auth_client()
    r = c.post("/api/echo/mutate", headers={"origin": ORIGIN})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_csrf_referer_fallback_when_no_origin():
    c = _auth_client()
    ok = c.post("/api/echo/mutate", headers={"referer": f"{ORIGIN}/dashboard"})
    assert ok.status_code == 200
    bad = c.post("/api/echo/mutate", headers={"referer": "http://evil.example/x"})
    assert bad.status_code == 403
    none = c.post("/api/echo/mutate")            # no origin, no referer
    assert none.status_code == 403


def test_csrf_exempts_webhook_and_internal_prefix():
    c = _auth_client()
    # exempt endpoints accept a mutation with NO origin (server-to-server)
    assert c.post("/api/payments/webhook").status_code == 200
    assert c.post("/api/admin/promo").status_code == 200


def test_get_is_never_csrf_checked():
    c = _auth_client()
    assert c.get("/api/auth/me", headers={"origin": "http://evil.example"}).status_code == 200
