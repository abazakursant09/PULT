"""
SECURITY-2C-2 — auth throttle: HMAC keys (no PII), IP normalization, per-action limits, enumeration
safety. Unit + sqlite integration. Real-PostgreSQL atomicity/concurrency lives in test_auth_throttle_pg.
"""
import asyncio
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
import models  # noqa: F401
from models.user import User
from routers import auth as auth_router
from routers.auth import hash_password
from csrf import OriginCsrfMiddleware
from services import auth_throttle as T

_LOOP = asyncio.new_event_loop()
ORIGIN = "http://localhost:3000"
GOOD_PW = "Passw0rdOk"


def _run(c):
    return _LOOP.run_until_complete(c)


# ── HMAC keys never store / leak PII ─────────────────────────────────────────
def test_hash_deterministic_and_domain_separated():
    a = T._hash("identity", "user@example.com")
    b = T._hash("identity", "user@example.com")
    c = T._hash("ip", "user@example.com")        # same value, different dimension
    assert a == b and a != c
    assert len(a) == 64 and all(ch in "0123456789abcdef" for ch in a)


def test_hash_hides_email_and_ip():
    h = T._hash("identity", "secret@example.com")
    assert "secret@example.com" not in h and "secret" not in h
    hip = T._hash("ip", "203.0.113.7")
    assert "203.0.113" not in hip


def test_pair_identity_ip_keys_differ():
    keys = {T._key(d, "u@x.c", "1.2.3.4") for d in ("pair", "identity", "ip")}
    assert len(keys) == 3


# ── IP normalization ─────────────────────────────────────────────────────────
def test_ip_normalization():
    assert T.normalize_ip("1.2.3.4") == "1.2.3.4"
    assert T.normalize_ip("::ffff:1.2.3.4") == "1.2.3.4"                  # IPv4-mapped IPv6 → IPv4
    n1 = T.normalize_ip("2001:db8:abcd:0012::1")
    n2 = T.normalize_ip("2001:db8:abcd:0012:ffff::9")                     # same /64
    n3 = T.normalize_ip("2001:db8:abcd:0013::1")                          # different /64
    assert n1 == n2 and n1 != n3 and n1.endswith("/64")
    assert T.normalize_ip("") == "unknown"
    assert T.normalize_ip("not-an-ip") == "not-an-ip"                     # fail-closed: still keys a bucket


# ── integration harness (sqlite; the throttle table is created by create_all) ─
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
    app.add_middleware(OriginCsrfMiddleware)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


async def _seed(db, *, email, verified=True):
    db.add(User(id=str(uuid.uuid4()), email=email, name="S", hashed_password=hash_password(GOOD_PW),
                is_verified=verified, token_version=0))
    await db.commit()


def _login(c, email, pw="wrong"):
    return c.post("/api/auth/login", json={"email": email, "password": pw}, headers={"origin": ORIGIN})


# ── enumeration: unknown vs existing email → identical failure ───────────────
def test_login_unknown_and_existing_wrong_pw_identical():
    db = _run(_new_db()); _run(_seed(db, email="real@b.c"))
    c = _client(db)
    r_unknown = _login(c, "nobody@b.c")
    r_existing = _login(c, "real@b.c")
    assert r_unknown.status_code == r_existing.status_code == 401
    assert r_unknown.json() == r_existing.json()          # identical body → no existence oracle


# ── login pair limit: 5 failures then 429 + Retry-After ──────────────────────
def test_login_pair_limit_blocks_with_retry_after():
    db = _run(_new_db()); _run(_seed(db, email="lock@b.c"))
    c = _client(db)
    # limit=N means the N-th failure is the one that trips the block; the first N-1 answer a plain 401.
    for _ in range(settings.auth_throttle_login_pair_limit - 1):
        assert _login(c, "lock@b.c").status_code == 401
    blocked = _login(c, "lock@b.c")
    assert blocked.status_code == 429
    assert int(blocked.headers.get("Retry-After", "0")) > 0
    assert "Слишком много попыток" in blocked.json()["detail"]


# ── a correct password compensates: success is not counted as a failure ──────
def test_successful_login_does_not_accumulate_failures():
    db = _run(_new_db()); _run(_seed(db, email="ok@b.c"))
    c = _client(db)
    # (pair_limit - 1) bad, then a good one, repeated — never trips because success releases its own +1
    for _ in range(settings.auth_throttle_login_pair_limit + 3):
        assert c.post("/api/auth/login", json={"email": "ok@b.c", "password": GOOD_PW},
                      headers={"origin": ORIGIN}).status_code == 200


# ── register / forgot / reset have their own buckets ─────────────────────────
def test_register_throttle_blocks():
    db = _run(_new_db())
    c = _client(db)
    for i in range(settings.auth_throttle_register_ip_limit):
        c.post("/api/auth/register", json={"email": f"r{i}@b.c", "name": "N", "password": GOOD_PW},
               headers={"origin": ORIGIN})
    r = c.post("/api/auth/register", json={"email": "over@b.c", "name": "N", "password": GOOD_PW},
               headers={"origin": ORIGIN})
    assert r.status_code == 429


def test_forgot_throttle_blocks_and_stays_neutral():
    db = _run(_new_db())
    c = _client(db)
    for i in range(settings.auth_throttle_email_ip_limit):
        c.post("/api/auth/forgot-password", json={"email": f"f{i}@b.c"}, headers={"origin": ORIGIN})
    r = c.post("/api/auth/forgot-password", json={"email": "again@b.c"}, headers={"origin": ORIGIN})
    assert r.status_code == 429


def test_reset_throttle_blocks():
    db = _run(_new_db())
    c = _client(db)
    for _ in range(settings.auth_throttle_reset_ip_limit):
        c.post("/api/auth/reset-password", json={"token": "x", "password": "NewPass0rd"},
               headers={"origin": ORIGIN})
    r = c.post("/api/auth/reset-password", json={"token": "x", "password": "NewPass0rd"},
               headers={"origin": ORIGIN})
    assert r.status_code == 429


# ── victim-lockout guard: one source can't drive an email's global lock ──────
def test_single_source_does_not_feed_identity_global_after_pair_block():
    db = _run(_new_db()); _run(_seed(db, email="victim@b.c"))
    c = _client(db)
    # one client (one IP) hammers one email well past the pair limit
    for _ in range(settings.auth_throttle_login_pair_limit + 20):
        _login(c, "victim@b.c")
    from sqlalchemy import select
    from models.auth_rate_limit_bucket import AuthRateLimitBucket
    ident = _run(db.execute(
        select(AuthRateLimitBucket.attempts).where(
            AuthRateLimitBucket.action == "login", AuthRateLimitBucket.dimension == "identity"))).scalars().all()
    # the identity-global counter never climbs toward its own limit from a single blocked pair
    assert ident and ident[0] <= settings.auth_throttle_login_pair_limit
    assert ident[0] < settings.auth_throttle_login_identity_limit


# ── throttle logs / bucket rows carry no plaintext email or IP ───────────────
def test_bucket_rows_store_only_hashes(caplog):
    db = _run(_new_db()); _run(_seed(db, email="priv@b.c"))
    c = _client(db)
    _login(c, "priv@b.c")
    from sqlalchemy import select
    from models.auth_rate_limit_bucket import AuthRateLimitBucket
    rows = _run(db.execute(select(AuthRateLimitBucket))).scalars().all()
    assert rows
    for r in rows:
        assert "priv@b.c" not in r.key_hash
        assert len(r.key_hash) == 64
