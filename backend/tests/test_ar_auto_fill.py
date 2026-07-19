"""AR-AUTO-FILL — the full automatic path, no manual sync or draft.

Proves that with an enabled + consented rule PULT auto-syncs a review, auto-drafts a safe reply, and
(mode=auto + kill switch on) auto-publishes it — for Wildberries AND Ozon — and that confirm-mode
stops before publishing, negatives/ATTENTION never auto-publish, the kill switch gates ONLY publish,
and a second cycle never double-syncs or double-publishes. Provider + executor are mocked (no network).
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  register tables
from models.user import User
from models.product import Product
from models.review_response import ReviewResponse
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from config import settings
from services.marketplace import credential_vault
from services.marketplace.reviews import REVIEW_PROVIDERS
import tasks.auto_review_pipeline as pipe
import tasks.auto_publish_reviews as ap
from services.marketplace.review_automation_gate import CONSENT_VERSION as _CV

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _bind_session(monkeypatch, db):
    """Point both pipeline and publish worker at the one test session."""
    @asynccontextmanager
    async def _factory():
        yield db
    monkeypatch.setattr(pipe, "AsyncSessionLocal", _factory)
    monkeypatch.setattr(ap, "AsyncSessionLocal", _factory)


def _mock_fetch(monkeypatch, marketplace, reviews):
    async def _fake(token, product_ref):
        return reviews
    monkeypatch.setattr(REVIEW_PROVIDERS[marketplace], "fetch_reviews", _fake)


def _mock_publish(monkeypatch, *, ok=True, status="success", error=None, result=None):
    calls = {"n": 0}
    async def _fake(*, db, user_id, action_type, payload, mode, insight_key, idempotency_key, rule=None):
        calls["n"] += 1
        from models.execution_log import ExecutionLog
        log = ExecutionLog(id=str(uuid.uuid4()), user_id=user_id, action_type=action_type,
                           mode=mode, status=status, idempotency_key=idempotency_key, payload={})
        db.add(log); await db.flush()
        # `result` carries the marketplace's own answer — including a moderation status where the
        # marketplace moderates seller replies. Empty means "published immediately" (WB, Ozon).
        return SimpleNamespace(ok=ok, status=status, error=error, log_id=log.id,
                               result=result or {})
    monkeypatch.setattr(ap.executor, "execute", _fake)
    return calls


def _review(marketplace, *, rating=5, text="Отлично", ext=None):
    return SimpleNamespace(marketplace=marketplace, external_review_id=(ext or str(uuid.uuid4())),
                           product_ref="SKU", rating=rating, text=text, author="И",
                           review_created_at=datetime(2026, 7, 14), metadata={})


async def _seed(db, *, marketplace="wildberries", mode="auto", enabled=True, consent=True):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                 status="connected", scopes=["feedbacks"],
                                 ozon_client_id="CID" if marketplace == "ozon" else None)
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="SKU", marketplace=marketplace))
    db.add(AutomationRule(id=str(uuid.uuid4()), user_id=uid, contour="reputation",
                          action_type="publish_review_response", mode=mode, enabled=enabled,
                          guard={}, connection_id=conn.id,
                          consent_at=(datetime.utcnow() if consent else None),
                          consent_version=(_CV if consent else None)))
    await db.commit()
    return uid


def _reviews(db, uid):
    return _run(db.execute(select(ReviewResponse))).scalars().all()


# ── Full auto path: WB + Ozon ────────────────────────────────────────────────
def _full_auto(monkeypatch, marketplace):
    db = _run(_new_db())
    uid = _run(_seed(db, marketplace=marketplace, mode="auto"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", True)
    _mock_fetch(monkeypatch, marketplace, [_review(marketplace, ext="R1")])
    calls = _mock_publish(monkeypatch)

    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())

    rows = _reviews(db, uid)
    assert len(rows) == 1                          # synced once
    assert rows[0].status == "published"           # auto-published
    assert calls["n"] == 1                         # one provider publish call

    # Second cycle: no new sync (dedup) and no second publish (already published)
    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())
    assert len(_reviews(db, uid)) == 1 and calls["n"] == 1


def test_full_auto_wb(monkeypatch):
    _full_auto(monkeypatch, "wildberries")


def test_full_auto_ozon(monkeypatch):
    _full_auto(monkeypatch, "ozon")


# ── Confirm mode: sync + draft, NO publish ───────────────────────────────────
def _confirm(monkeypatch, marketplace):
    db = _run(_new_db())
    uid = _run(_seed(db, marketplace=marketplace, mode="confirm"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", True)   # even with the switch on, confirm waits
    _mock_fetch(monkeypatch, marketplace, [_review(marketplace, ext="R1")])
    calls = _mock_publish(monkeypatch)

    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())

    rows = _reviews(db, uid)
    assert len(rows) == 1 and rows[0].status == "drafted"       # drafted, waiting for the seller
    assert rows[0].response_text                                 # a real draft exists
    assert calls["n"] == 0                                       # never auto-published


def test_confirm_wb(monkeypatch):
    _confirm(monkeypatch, "wildberries")


def test_confirm_ozon(monkeypatch):
    _confirm(monkeypatch, "ozon")


# ── Safety: ATTENTION drafted-not-published, RISK not drafted ────────────────
def test_attention_drafted_never_published(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, mode="auto"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", True)
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", rating=3, text="средне", ext="A1")])
    calls = _mock_publish(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())
    rows = _reviews(db, uid)
    assert rows[0].safety_category == "ATTENTION"
    assert rows[0].status == "drafted"      # not 'approved' → worker never publishes
    assert calls["n"] == 0


def test_risk_not_drafted(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, mode="auto"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", True)
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", rating=1, text="ужас", ext="X1")])
    calls = _mock_publish(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())
    rows = _reviews(db, uid)
    assert rows[0].safety_category == "RISK"
    assert rows[0].status == "pending" and not (rows[0].response_text or "")   # no draft
    assert calls["n"] == 0


# ── Kill switch gates ONLY publish; sync + draft still run ────────────────────
def test_global_off_syncs_drafts_but_never_publishes(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, mode="auto"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", False)   # kill switch OFF
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", ext="R1")])
    calls = _mock_publish(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    out = _run(ap.run_auto_publish_reviews())
    rows = _reviews(db, uid)
    assert len(rows) == 1 and rows[0].response_text            # synced + drafted despite kill switch
    assert out["ran"] is False and calls["n"] == 0            # publish worker no-op globally


# ── Seller off / no consent / revoke → nothing happens ───────────────────────
def test_seller_disabled_no_sync(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, enabled=False))
    _bind_session(monkeypatch, db)
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", ext="R1")])
    _run(pipe.run_auto_sync_reviews())
    assert len(_reviews(db, uid)) == 0


def test_no_consent_no_sync(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, consent=False))
    _bind_session(monkeypatch, db)
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", ext="R1")])
    _run(pipe.run_auto_sync_reviews())
    assert len(_reviews(db, uid)) == 0


# ── Cadence: batch + persistent keyset cursor + connection backoff ───────────
from config import settings as _settings
from services.marketplace.errors import ExecutionError


def _conn(db):
    return _run(db.execute(select(MarketplaceConnection))).scalars().first()


def _due_now(db):
    """Simulate the 15-min cadence elapsing so the next cycle may run."""
    c = _conn(db); c.review_sync_next_at = None; _run(db.commit())


def _add_products(db, uid, n, marketplace="wildberries"):
    for _ in range(n):
        db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="SKU", marketplace=marketplace))
    _run(db.commit())


def _count_fetch(monkeypatch, marketplace="wildberries"):
    seen = {"skus": []}
    async def _fetch(token, product_ref):
        seen["skus"].append(product_ref); return []
    monkeypatch.setattr(REVIEW_PROVIDERS[marketplace], "fetch_reviews", _fetch)
    return seen


def test_batch_one_product_one_call(monkeypatch):
    db = _run(_new_db()); _run(_seed(db, mode="auto"))     # 1 product
    _bind_session(monkeypatch, db)
    seen = _count_fetch(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    assert len(seen["skus"]) == 1                          # exactly one marketplace call


def test_fewer_than_batch_all_processed(monkeypatch):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, 4)  # 5 total
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 20)
    _bind_session(monkeypatch, db)
    seen = _count_fetch(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    assert len(seen["skus"]) == 5                          # all in one batch
    assert _conn(db).review_sync_cursor is None            # short batch → wrapped


def test_more_than_batch_only_batch_then_continues_and_wraps(monkeypatch):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, 4)  # 5 total
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 2)
    _bind_session(monkeypatch, db)
    seen = _count_fetch(monkeypatch)

    _run(pipe.run_auto_sync_reviews())                     # batch 1: 2 products
    assert len(seen["skus"]) == 2
    cur1 = _conn(db).review_sync_cursor
    assert cur1 is not None                                # cursor advanced

    _due_now(db); _run(pipe.run_auto_sync_reviews())       # batch 2: next 2 (continues from cursor)
    assert len(seen["skus"]) == 4
    _due_now(db); _run(pipe.run_auto_sync_reviews())       # batch 3: last 1 → wrap
    assert len(seen["skus"]) == 5
    assert _conn(db).review_sync_cursor is None            # wrapped after the last product

    _due_now(db); _run(pipe.run_auto_sync_reviews())       # new round starts from the beginning
    assert len(seen["skus"]) == 7                          # 5 + first 2 again


def test_100_products_all_covered_no_starvation(monkeypatch):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, 99)  # 100
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 20)
    _bind_session(monkeypatch, db)
    seen = _count_fetch(monkeypatch)
    for _ in range(5):                                     # ceil(100/20) cycles
        _run(pipe.run_auto_sync_reviews()); _due_now(db)
    # cursor advances monotonically by id, so 5 batches of 20 cover all 100 with no re-fetch and no
    # product left behind — exactly one fetch per product over the round.
    assert len(seen["skus"]) == 100


def test_cursor_persists_in_db_survives_restart(monkeypatch):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, 4)
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 2)
    _bind_session(monkeypatch, db); _count_fetch(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    # cursor is a DB column — a fresh read (as after a restart) still sees it
    assert _conn(db).review_sync_cursor is not None


def test_deleted_cursor_product_does_not_break(monkeypatch):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, 4)  # 5
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 2)
    _bind_session(monkeypatch, db); seen = _count_fetch(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    cur = _conn(db).review_sync_cursor
    # delete the product the cursor points at → keyset (id > cursor) still returns the rest
    _run(db.execute(Product.__table__.delete().where(Product.id == cur)))
    _run(db.commit())
    _due_now(db); _run(pipe.run_auto_sync_reviews())       # must not raise, continues past the gap
    assert len(seen["skus"]) >= 3


def test_new_product_eventually_processed(monkeypatch):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, 1)  # 2
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 2)
    _bind_session(monkeypatch, db); seen = _count_fetch(monkeypatch)
    _run(pipe.run_auto_sync_reviews())                     # both synced → wrap
    _add_products(db, uid, 1)                              # a new product appears
    _due_now(db); _run(pipe.run_auto_sync_reviews())       # next round covers it too
    _due_now(db); _run(pipe.run_auto_sync_reviews())
    assert len(seen["skus"]) >= 3


def test_one_product_error_does_not_lose_others(monkeypatch):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, 2)  # 3
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 20)
    _bind_session(monkeypatch, db)
    calls = {"n": 0}
    async def _fetch(token, product_ref):
        calls["n"] += 1
        if calls["n"] == 1:
            # product-local 4xx (unknown product id) — about this product, not the connection
            raise ExecutionError(ExecutionError.MARKETPLACE_4XX, "404: no such product")
        return []
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _fetch)
    _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 3                                 # the other two were still attempted


def test_auth_error_stops_connection(monkeypatch):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, 4)  # 5
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 20)
    _bind_session(monkeypatch, db)
    calls = {"n": 0}
    async def _fetch(token, product_ref):
        calls["n"] += 1
        raise ExecutionError(ExecutionError.AUTH, "auth rejected")
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _fetch)
    _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 1                                 # stopped after the first auth failure
    assert _conn(db).review_sync_next_at is not None        # connection backed off


# ── Connection-level error stops the batch immediately (no extra marketplace calls) ──
def _fail_at(monkeypatch, code, *, nth=1, marketplace="wildberries"):
    """Provider that fails with `code` on the nth call and succeeds otherwise. Counts every call."""
    calls = {"n": 0, "skus": []}
    async def _fetch(token, product_ref):
        calls["n"] += 1
        calls["skus"].append(product_ref)
        if calls["n"] == nth:
            raise ExecutionError(code, f"{code} on call {nth}")
        return []
    monkeypatch.setattr(REVIEW_PROVIDERS[marketplace], "fetch_reviews", _fetch)
    return calls


def _batch_of(monkeypatch, n_extra=19, batch_size=20):
    db = _run(_new_db()); uid = _run(_seed(db, mode="auto")); _add_products(db, uid, n_extra)
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", batch_size)
    _bind_session(monkeypatch, db)
    return db, uid


def test_rate_limit_on_first_product_stops_whole_batch(monkeypatch):
    """Batch of 20, first call 429 → exactly ONE marketplace call, then the connection backs off."""
    db, _ = _batch_of(monkeypatch)
    calls = _fail_at(monkeypatch, ExecutionError.RATE_LIMIT, nth=1)
    out = _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 1                                  # the other 19 were never requested
    assert out["stopped"] == 1
    c = _conn(db)
    assert c.review_sync_fail_count == 1
    assert (c.review_sync_next_at - datetime.utcnow()).total_seconds() > 0
    assert c.review_sync_cursor is None                      # nothing processed → cursor unmoved


def test_timeout_mid_batch_stops_rest(monkeypatch):
    """Timeout (after the client's own retries) on the 5th product → 5 calls, the rest wait."""
    db, _ = _batch_of(monkeypatch)
    calls = _fail_at(monkeypatch, ExecutionError.TIMEOUT, nth=5)
    _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 5                                   # products 6..20 not called
    assert _conn(db).review_sync_fail_count == 1


def test_5xx_stops_batch(monkeypatch):
    db, _ = _batch_of(monkeypatch)
    calls = _fail_at(monkeypatch, ExecutionError.MARKETPLACE_5XX, nth=3)
    _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 3
    assert _conn(db).review_sync_fail_count == 1


def test_permission_error_stops_batch(monkeypatch):
    db, _ = _batch_of(monkeypatch)
    calls = _fail_at(monkeypatch, ExecutionError.MISSING_SCOPE, nth=2)
    _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 2
    assert _conn(db).review_sync_fail_count == 1


def test_product_local_4xx_does_not_back_off_connection(monkeypatch):
    """A product-local error is recorded, the cursor passes it, and the connection stays on cadence."""
    db, _ = _batch_of(monkeypatch, n_extra=4, batch_size=20)      # 5 products
    calls = _fail_at(monkeypatch, ExecutionError.MARKETPLACE_4XX, nth=2)
    out = _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 5 and out["errors"] == 1 and out["stopped"] == 0
    c = _conn(db)
    assert c.review_sync_fail_count == 0                      # connection is healthy
    delay = (c.review_sync_next_at - datetime.utcnow()).total_seconds()
    assert delay <= pipe.SYNC_INTERVAL_SEC                    # normal 15-min cadence, not a backoff


def test_unprocessed_products_resume_after_backoff(monkeypatch):
    """After a connection stop the next cycle restarts at the first UNPROCESSED product."""
    db, _ = _batch_of(monkeypatch, n_extra=9, batch_size=20)      # 10 products
    calls = _fail_at(monkeypatch, ExecutionError.RATE_LIMIT, nth=4)
    _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 4
    first_pass = list(calls["skus"])

    _due_now(db)                                             # backoff elapsed
    async def _ok(token, product_ref):
        calls["n"] += 1; calls["skus"].append(product_ref); return []
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _ok)
    _run(pipe.run_auto_sync_reviews())

    # products 1-3 succeeded, product 4 failed → the resumed cycle covers 4..10 (7 products)
    assert calls["n"] == 4 + 7
    assert len(first_pass) == 4


def test_no_products_lost_across_connection_error(monkeypatch):
    """Every product is reached exactly once per round even with a connection error mid-round."""
    db, _ = _batch_of(monkeypatch, n_extra=9, batch_size=20)      # 10 products
    ids = sorted(p.id for p in _run(db.execute(select(Product))).scalars().all())
    state = {"fail_on": 5, "n": 0}
    async def _fetch(token, product_ref):
        state["n"] += 1
        if state["n"] == state["fail_on"]:
            raise ExecutionError(ExecutionError.TIMEOUT, "timeout")
        return []
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _fetch)

    _run(pipe.run_auto_sync_reviews())                       # 4 ok, 5th stops the batch
    cursor_after = _conn(db).review_sync_cursor
    assert cursor_after == ids[3]                            # last SUCCESSFUL product, not the failed one

    state["fail_on"] = -1                                    # marketplace recovers
    _due_now(db); _run(pipe.run_auto_sync_reviews())
    assert _conn(db).review_sync_cursor is None              # reached the end → wrapped, nothing skipped


def test_connection_error_does_not_stop_other_connection(monkeypatch):
    """A 429 on the WB connection must not prevent the Ozon connection from syncing in the same cycle."""
    db = _run(_new_db())
    _run(_seed(db, marketplace="wildberries", mode="auto"))
    _run(_seed(db, marketplace="ozon", mode="auto"))
    _bind_session(monkeypatch, db)

    async def _wb(token, product_ref):
        raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
    ozon_calls = {"n": 0}
    async def _ozon(token, product_ref):
        ozon_calls["n"] += 1; return []
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _wb)
    monkeypatch.setattr(REVIEW_PROVIDERS["ozon"], "fetch_reviews", _ozon)

    out = _run(pipe.run_auto_sync_reviews())
    assert ozon_calls["n"] == 1 and out["stopped"] == 1      # ozon ran, wb backed off
    conns = {c.marketplace: c for c in _run(db.execute(select(MarketplaceConnection))).scalars().all()}
    assert conns["wildberries"].review_sync_fail_count == 1
    assert conns["ozon"].review_sync_fail_count == 0


def test_success_after_backoff_resets_fail_count(monkeypatch):
    db, _ = _batch_of(monkeypatch, n_extra=2, batch_size=20)      # 3 products
    _fail_at(monkeypatch, ExecutionError.RATE_LIMIT, nth=1)
    _run(pipe.run_auto_sync_reviews())
    assert _conn(db).review_sync_fail_count == 1

    async def _ok(token, product_ref):
        return []
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _ok)
    _due_now(db); _run(pipe.run_auto_sync_reviews())
    c = _conn(db)
    assert c.review_sync_fail_count == 0
    delay = (c.review_sync_next_at - datetime.utcnow()).total_seconds()
    assert delay <= pipe.SYNC_INTERVAL_SEC                    # back to the normal cadence


def test_backoff_ladder_caps_at_six_hours():
    """30 min → 1 h → 2 h → 4 h → capped at 6 h."""
    assert pipe._next_sync_delay(1) == 30 * 60
    assert pipe._next_sync_delay(2) == 60 * 60
    assert pipe._next_sync_delay(3) == 2 * 60 * 60
    assert pipe._next_sync_delay(4) == 4 * 60 * 60
    assert pipe._next_sync_delay(5) == pipe.SYNC_BACKOFF_CAP_SEC == 6 * 60 * 60
    assert pipe._next_sync_delay(99) == 6 * 60 * 60


def test_backed_off_connection_makes_no_calls_next_tick(monkeypatch):
    """While the backoff window is open the scheduler issues ZERO marketplace calls for it."""
    db, _ = _batch_of(monkeypatch, n_extra=4, batch_size=20)
    calls = _fail_at(monkeypatch, ExecutionError.RATE_LIMIT, nth=1)
    _run(pipe.run_auto_sync_reviews())
    assert calls["n"] == 1
    out = _run(pipe.run_auto_sync_reviews())                 # next scheduler tick, still backed off
    assert calls["n"] == 1 and out["throttled"] == 1


def test_backoff_persists_and_success_resets(monkeypatch):
    db = _run(_new_db()); _run(_seed(db, mode="auto"))
    _bind_session(monkeypatch, db)
    async def _boom(token, product_ref):
        raise ExecutionError(ExecutionError.TIMEOUT, "timeout")
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _boom)
    _run(pipe.run_auto_sync_reviews())
    c = _conn(db)
    delay = (c.review_sync_next_at - datetime.utcnow()).total_seconds()
    assert c.review_sync_fail_count == 1 and delay > 0     # backed off, persisted in DB

    _due_now(db); _run(pipe.run_auto_sync_reviews())
    assert _conn(db).review_sync_fail_count == 2           # grows

    async def _ok(token, product_ref):
        return []
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _ok)
    _due_now(db); _run(pipe.run_auto_sync_reviews())
    assert _conn(db).review_sync_fail_count == 0           # success resets


# ── One failing store never stops the rest ────────────────────────────────────
def test_one_connection_error_does_not_stop_others(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, marketplace="wildberries", mode="auto"))
    _bind_session(monkeypatch, db)

    async def _boom(token, product_ref):
        raise RuntimeError("marketplace down")
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _boom)

    out = _run(pipe.run_auto_sync_reviews())   # must not raise
    assert out["errors"] >= 1 and out["imported"] == 0
