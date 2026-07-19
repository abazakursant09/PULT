"""Yandex Auto Reviews — the provider, and the two places it could quietly do harm.

The dangerous parts of this integration are not the HTTP calls; they are the two places where a
plausible-looking shortcut destroys a safety guarantee:

  * THE TEXT. Yandex splits a review into `advantages` / `disadvantages` / `comment`. The safety
    classifier reads the review text. Build that text from `comment` alone and a review whose
    complaint lives entirely in `disadvantages` classifies as SAFE and gets auto-published.
  * THE REPLY. The Partner API has no idempotency key, and it moderates seller replies. Treating a
    2xx as "published", or an unknown as "send it again", either lies to the seller or posts the
    same answer twice.

Everything else here is mapping.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime

import pytest
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
from services.marketplace import credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.reviews import get_review_provider, review_credential
from services.marketplace.reviews.yandex import YandexReviewProvider, compose_text
from services.marketplace.review_automation_gate import CONSENT_VERSION as _CV
from services.review.draft import classify_review
from services.review.state import derive_state
import services.review.ingest as review_ingest
import services.marketplace.yandex_client as yc
import tasks.auto_review_pipeline as pipe

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


CRED = "BIZ1:api-key"


# ── Text composition — the safety-critical part ──────────────────────────────
def test_all_three_parts_are_joined():
    text = compose_text({"advantages": "быстро", "disadvantages": "хрупко", "comment": "в целом ок"})
    assert "Достоинства: быстро" in text
    assert "Недостатки: хрупко" in text
    assert "в целом ок" in text


def test_a_complaint_living_only_in_disadvantages_reaches_the_text():
    text = compose_text({"advantages": "", "disadvantages": "товар сломан, ужасное качество",
                         "comment": ""})
    assert "сломан" in text


def test_the_classifier_sees_a_complaint_that_is_only_in_disadvantages():
    """The whole reason the parts are joined: the safety verdict must not depend on WHICH field the
    buyer typed their complaint into."""
    text = compose_text({"disadvantages": "ужасное качество, брак"})
    category, reason = classify_review(5, text)
    assert category == "RISK"
    assert reason


def test_such_a_review_is_never_auto_published():
    """End to end: a 5★ whose complaint hides in `disadvantages` gets no sendable draft at all."""
    from services.review.draft import build_draft
    text = compose_text({"disadvantages": "ужасное качество, брак"})
    category, _ = classify_review(5, text)
    assert category == "RISK"
    assert build_draft(5, text, "Товар") is None          # RISK is never drafted for sending


def test_empty_parts_leave_no_debris():
    text = compose_text({"advantages": "", "disadvantages": None, "comment": "просто отзыв"})
    assert text == "просто отзыв"
    assert "None" not in text
    assert "Достоинства" not in text and "Недостатки" not in text


def test_a_rating_only_review_has_no_text():
    assert compose_text({"advantages": "", "disadvantages": "", "comment": ""}) is None
    assert compose_text({}) is None
    assert compose_text(None) is None


def test_whitespace_only_parts_are_dropped():
    assert compose_text({"advantages": "   ", "comment": "\n"}) is None


# ── Mapping ─────────────────────────────────────────────────────────────────
def _feedback(**over):
    base = {
        "feedbackId": 909,
        "createdAt": "2026-07-18T10:20:30Z",
        "author": "Иван",
        "needReaction": True,
        "identifiers": {"offerId": "SKU-1", "orderId": 5},
        "description": {"advantages": "плюс", "disadvantages": "", "comment": "хорошо"},
        "statistics": {"rating": 5, "commentsCount": 0},
    }
    base.update(over)
    return base


def _provider_with(monkeypatch, feedbacks):
    async def _list(*, token, business_id, offer_id, limit=50):
        return feedbacks
    monkeypatch.setattr(yc.yandex_client, "list_reviews", _list)
    return YandexReviewProvider()


def test_every_field_maps(monkeypatch):
    p = _provider_with(monkeypatch, [_feedback()])
    out = _run(p.fetch_reviews(CRED, "SKU-1"))

    assert len(out) == 1
    r = out[0]
    assert r.marketplace == "yandex"
    assert r.external_review_id == "909"           # int id becomes a string
    assert r.product_ref == "SKU-1"
    assert r.rating == 5                            # read from the NESTED statistics object
    assert r.author == "Иван"
    assert r.review_created_at == datetime(2026, 7, 18, 10, 20, 30)
    assert "Достоинства: плюс" in r.text and "хорошо" in r.text


class _CaptureHandler(logging.Handler):
    """Capture straight off the provider's logger.

    Not caplog: the app configures logging at import time, and whether a record reaches the root
    handler depends on what else the suite has already run. Attaching here makes the assertion about
    what the provider LOGS, not about how logging happens to be wired this session.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextmanager
def _captured_warnings():
    """Capture what this provider logs, independently of the rest of the suite.

    `logger.disabled` is forced off for the duration: alembic's env.py calls `fileConfig`, which
    defaults to `disable_existing_loggers=True` and switches every logger created so far OFF for the
    rest of the session. Any test that runs a migration therefore silences this one, and these
    assertions would pass or fail on test ORDER rather than on provider behaviour.
    """
    logger = logging.getLogger("services.marketplace.reviews.yandex")
    handler = _CaptureHandler()
    logger.addHandler(handler)
    previous_level, previously_disabled = logger.level, logger.disabled
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previously_disabled


def test_a_missing_feedback_id_is_logged_not_swallowed(monkeypatch):
    p = _provider_with(monkeypatch, [_feedback(feedbackId=None), _feedback(feedbackId=7)])
    with _captured_warnings() as logged:
        out = _run(p.fetch_reviews(CRED, "SKU-1"))

    assert [r.external_review_id for r in out] == ["7"]      # the good one still arrives
    assert any("no feedbackId" in m for m in logged.messages)


def test_the_skip_log_carries_keys_but_never_values(monkeypatch):
    """A diagnosable log must not become a place buyer text leaks into."""
    secret = "покупатель написал нечто личное"
    p = _provider_with(monkeypatch, [_feedback(feedbackId=None,
                                               description={"comment": secret})])
    with _captured_warnings() as logged:
        _run(p.fetch_reviews(CRED, "SKU-1"))
    blob = " ".join(logged.messages)
    assert "description" in blob            # the shape is reported
    assert secret not in blob               # the content is not


def test_a_non_object_row_is_skipped(monkeypatch):
    p = _provider_with(monkeypatch, ["nonsense", _feedback()])
    assert len(_run(p.fetch_reviews(CRED, "SKU-1"))) == 1


def test_missing_rating_is_none_not_zero(monkeypatch):
    p = _provider_with(monkeypatch, [_feedback(statistics={})])
    assert _run(p.fetch_reviews(CRED, "SKU-1"))[0].rating is None


# ── Pagination ──────────────────────────────────────────────────────────────
def _script(monkeypatch, pages):
    """Answer `list_reviews` from a page_token -> payload map, recording the cursors asked for."""
    seen: list = []

    async def _request(method, path, *, token, auth_header=None, json=None, params=None):
        cursor = (params or {}).get("page_token")
        seen.append(cursor)
        return pages[cursor]
    monkeypatch.setattr(yc.yandex_client._api, "request", _request)
    return seen


def test_every_page_of_reviews_is_read(monkeypatch):
    """An offer with more feedbacks than fit on one page must not lose the rest in silence: the
    cursor is followed until Yandex stops handing one back."""
    seen = _script(monkeypatch, {
        None: {"result": {"feedbacks": [_feedback(feedbackId=1)],
                          "paging": {"nextPageToken": "P2"}}},
        "P2": {"result": {"feedbacks": [_feedback(feedbackId=2)],
                          "paging": {"nextPageToken": "P3"}}},
        "P3": {"result": {"feedbacks": [_feedback(feedbackId=3)], "paging": {}}},
    })

    out = _run(yc.yandex_client.list_reviews(token="k", business_id="BIZ1", offer_id="SKU-1"))

    assert [f["feedbackId"] for f in out] == [1, 2, 3]
    assert seen == [None, "P2", "P3"]      # the cursor moved — not the same page three times


def test_a_single_page_asks_for_no_cursor(monkeypatch):
    seen = _script(monkeypatch, {None: {"result": {"feedbacks": [_feedback()], "paging": {}}}})
    out = _run(yc.yandex_client.list_reviews(token="k", business_id="BIZ1", offer_id="SKU-1"))
    assert len(out) == 1 and seen == [None]


def test_a_cursor_that_never_ends_is_bounded(monkeypatch):
    """A malformed `nextPageToken` that keeps repeating itself would otherwise spin forever. A
    bounded failure is the only acceptable one here."""
    calls = {"n": 0}

    async def _request(method, path, *, token, auth_header=None, json=None, params=None):
        calls["n"] += 1
        return {"result": {"feedbacks": [], "paging": {"nextPageToken": "same-again"}}}
    monkeypatch.setattr(yc.yandex_client._api, "request", _request)

    _run(yc.yandex_client.list_reviews(token="k", business_id="BIZ1", offer_id="SKU"))
    assert calls["n"] == yc.MAX_PAGES


def test_page_size_is_clamped_to_the_documented_maximum(monkeypatch):
    """Asking for more than Yandex documents is a client error, so the ceiling is enforced here."""
    seen = {}

    async def _request(method, path, *, token, auth_header=None, json=None, params=None):
        seen.update(params or {})
        return {"result": {"feedbacks": [], "paging": {}}}
    monkeypatch.setattr(yc.yandex_client._api, "request", _request)

    _run(yc.yandex_client.list_reviews(token="k", business_id="B", offer_id="S", limit=500))
    assert seen["limit"] == yc.MAX_PAGE_SIZE


# ── Credential shaping ──────────────────────────────────────────────────────
def test_credential_carries_the_cabinet():
    assert review_credential("yandex", "key", {"account_ref": "BIZ9"}) == "BIZ9:key"


def test_a_missing_cabinet_never_becomes_the_string_None():
    """Interpolating an unresolved cabinet would address the literal cabinet "None" and send a
    seller's reply at it. The token is handed over alone instead, so the provider rejects it."""
    shaped = review_credential("yandex", "key", {"account_ref": None})
    assert shaped == "key" and "None" not in shaped
    with pytest.raises(ExecutionError) as e:
        _run(YandexReviewProvider().publish_answer(shaped, "909", "спасибо"))
    assert e.value.code == ExecutionError.AUTH


def test_an_incomplete_credential_is_an_auth_error(monkeypatch):
    p = YandexReviewProvider()
    for bad in ("key-without-cabinet", ":key", "BIZ:"):
        with pytest.raises(ExecutionError) as e:
            _run(p.fetch_reviews(bad, "SKU"))
        assert e.value.code == ExecutionError.AUTH


# ── Cabinet discovery ───────────────────────────────────────────────────────
def _campaigns(monkeypatch, payload):
    async def _get(path, *, token, params=None):
        return payload
    monkeypatch.setattr(yc.yandex_client._api, "request",
                        lambda *a, **k: _get(a[1] if len(a) > 1 else "", token=k.get("token")))


def test_one_cabinet_is_resolved(monkeypatch):
    _campaigns(monkeypatch, {"campaigns": [{"business": {"id": 42}}, {"business": {"id": 42}}]})
    assert _run(yc.yandex_client.resolve_business_id(token="k")) == "42"


def test_several_cabinets_are_never_guessed(monkeypatch):
    """Choosing one would risk publishing a seller's replies into a cabinet they did not mean."""
    _campaigns(monkeypatch, {"campaigns": [{"business": {"id": 1}}, {"business": {"id": 2}}]})
    with pytest.raises(ExecutionError) as e:
        _run(yc.yandex_client.resolve_business_id(token="k"))
    assert e.value.code == ExecutionError.VALIDATION


def test_no_cabinet_is_an_auth_error(monkeypatch):
    _campaigns(monkeypatch, {"campaigns": []})
    with pytest.raises(ExecutionError) as e:
        _run(yc.yandex_client.resolve_business_id(token="k"))
    assert e.value.code == ExecutionError.AUTH


# ── Cabinet caching ─────────────────────────────────────────────────────────
class _Resolver:
    """A provider that needs an account ref, counting how often it is asked to discover one."""

    def __init__(self, ref="BIZ7"):
        self.ref, self.calls = ref, 0

    async def resolve_account_ref(self, token):
        self.calls += 1
        return self.ref


async def _credential(db, **over):
    cred = ApiCredential(id=str(uuid.uuid4()), connection_id=str(uuid.uuid4()), scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), **over)
    db.add(cred)
    await db.flush()
    return cred


def test_the_cabinet_is_discovered_once_and_stored_on_the_credential():
    """The seller never types the cabinet id, and a restart must not re-ask for it: it is resolved
    from the key and kept in `ApiCredential.meta`."""
    async def _go():
        db = await _new_db()
        cred = await _credential(db)
        provider = _Resolver()

        first = await review_ingest.ensure_account_ref(db, cred, provider, "tok")
        second = await review_ingest.ensure_account_ref(db, cred, provider, "tok")

        stored = (await db.execute(
            select(ApiCredential).where(ApiCredential.id == cred.id))).scalars().first()
        meta = dict(stored.meta or {})
        await db.close()
        return first, second, provider.calls, meta

    first, second, calls, meta = _run(_go())
    assert first == "BIZ7" and second == "BIZ7"
    assert calls == 1                                       # the second answer came from the row
    assert meta.get(review_ingest.ACCOUNT_REF_KEY) == "BIZ7"


def test_an_already_cached_cabinet_is_never_re_resolved():
    async def _go():
        db = await _new_db()
        cred = await _credential(db, meta={review_ingest.ACCOUNT_REF_KEY: "BIZ1"})
        provider = _Resolver("BIZ9")
        ref = await review_ingest.ensure_account_ref(db, cred, provider, "tok")
        await db.close()
        return ref, provider.calls

    ref, calls = _run(_go())
    assert ref == "BIZ1" and calls == 0


def test_a_provider_that_needs_no_cabinet_is_never_asked():
    """Capability check, not a marketplace check — WB and Ozon must pass through untouched."""
    class _Plain:
        pass

    async def _go():
        db = await _new_db()
        cred = await _credential(db)
        ref = await review_ingest.ensure_account_ref(db, cred, _Plain(), "tok")
        meta = dict(cred.meta or {})
        await db.close()
        return ref, meta

    ref, meta = _run(_go())
    assert ref is None and meta == {}


# ── Publishing and moderation ───────────────────────────────────────────────
def test_publish_creates_a_comment_without_an_id(monkeypatch):
    seen = {}
    async def _publish(*, token, business_id, feedback_id, text):
        seen.update(business_id=business_id, feedback_id=feedback_id, text=text)
        return {"result": {"id": 5, "status": "PUBLISHED"}}
    monkeypatch.setattr(yc.yandex_client, "publish_feedback_answer", _publish)

    out = _run(YandexReviewProvider().publish_answer(CRED, "909", "спасибо"))

    assert seen == {"business_id": "BIZ1", "feedback_id": "909", "text": "спасибо"}
    assert out["result"]["status"] == "PUBLISHED"


def _apply(result):
    """Run the publish-result interpreter over a bare review row."""
    from tasks.auto_publish_reviews import _apply_publish_result
    review = ReviewResponse(id=str(uuid.uuid4()), product_id="p", status="approved")
    outcome = _apply_publish_result(review, result, "log-1")
    return review, outcome


def test_published_is_published():
    review, outcome = _apply({"comment_status": "PUBLISHED", "comment_id": 5})
    assert outcome == "published"
    assert review.status == "published" and review.published_at is not None


def test_unmoderated_is_not_published():
    review, outcome = _apply({"comment_status": "UNMODERATED", "comment_id": 5})
    assert outcome == "awaiting_moderation"
    assert review.status == "awaiting_moderation"
    assert review.published_at is None                 # nothing is visible — do not date it


def test_a_moderating_reply_is_not_publishable_again():
    """The guarantee against a double post: the row leaves the worker's candidate set."""
    from tasks.auto_publish_reviews import _PUBLISHABLE
    assert "awaiting_moderation" not in _PUBLISHABLE


def test_banned_and_deleted_are_failures():
    for status in ("BANNED", "DELETED"):
        review, outcome = _apply({"comment_status": status, "comment_id": 5})
        assert outcome == "rejected"
        assert review.status == "failed" and review.failure_reason
        assert review.published_at is None


def test_no_moderation_status_means_published():
    """WB and Ozon do not moderate — an absent status must keep their behaviour unchanged."""
    review, outcome = _apply({})
    assert outcome == "published" and review.status == "published"


def test_awaiting_moderation_has_its_own_workspace_state():
    assert derive_state("awaiting_moderation", "SAFE", None) == "AwaitingModeration"
    # …and it is not quietly folded into Published
    assert derive_state("awaiting_moderation", "SAFE", None) != "Published"


# ── Moderation re-check ─────────────────────────────────────────────────────
async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_awaiting(db):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="yandex",
                                 status="connected", scopes=["feedbacks"])
    db.add(conn)
    await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={"account_ref": "BIZ1"}))
    product = Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="SKU", marketplace="yandex")
    db.add(product)
    db.add(AutomationRule(id=str(uuid.uuid4()), user_id=uid, contour="reputation",
                          action_type="publish_review_response", mode="auto", enabled=True,
                          guard={}, connection_id=conn.id,
                          consent_at=datetime.utcnow(), consent_version=_CV))
    review = ReviewResponse(id=str(uuid.uuid4()), product_id=product.id, marketplace="yandex",
                            external_review_id="909", status="awaiting_moderation",
                            response_text="спасибо", safety_category="SAFE")
    db.add(review)
    await db.commit()
    return review


def _bind(monkeypatch, db):
    @asynccontextmanager
    async def _factory():
        yield db
    monkeypatch.setattr(pipe, "AsyncSessionLocal", _factory)


def _mock_status(monkeypatch, status):
    calls = {"n": 0}
    async def _answer_status(self, token, external_review_id, comment_id):
        calls["n"] += 1
        return status
    monkeypatch.setattr(YandexReviewProvider, "answer_status", _answer_status)
    return calls


def test_moderation_recheck_resolves_a_published_reply(monkeypatch):
    db = _run(_new_db()); review = _run(_seed_awaiting(db))
    _bind(monkeypatch, db); _mock_status(monkeypatch, "PUBLISHED")

    out = _run(pipe.run_reconcile_moderation())

    assert out["resolved"] == 1
    row = _run(db.execute(select(ReviewResponse))).scalars().first()
    assert row.status == "published" and row.published_at is not None
    del review


def test_moderation_recheck_records_a_rejection(monkeypatch):
    db = _run(_new_db()); _run(_seed_awaiting(db))
    _bind(monkeypatch, db); _mock_status(monkeypatch, "BANNED")

    out = _run(pipe.run_reconcile_moderation())

    assert out["rejected"] == 1
    row = _run(db.execute(select(ReviewResponse))).scalars().first()
    assert row.status == "failed" and row.failure_reason


def test_a_still_moderating_reply_is_left_alone(monkeypatch):
    db = _run(_new_db()); _run(_seed_awaiting(db))
    _bind(monkeypatch, db); _mock_status(monkeypatch, "UNMODERATED")

    out = _run(pipe.run_reconcile_moderation())

    assert out["pending"] == 1
    assert _run(db.execute(select(ReviewResponse))).scalars().first().status == "awaiting_moderation"


def test_an_unfindable_reply_is_never_re_sent(monkeypatch):
    """Not finding our answer is an UNKNOWN. The one thing that must not follow is a second copy."""
    db = _run(_new_db()); _run(_seed_awaiting(db))
    _bind(monkeypatch, db); _mock_status(monkeypatch, None)

    published = {"n": 0}
    async def _never(*a, **k):
        published["n"] += 1
        return {}
    monkeypatch.setattr(YandexReviewProvider, "publish_answer", _never)

    out = _run(pipe.run_reconcile_moderation())

    assert out["pending"] == 1 and published["n"] == 0
    assert _run(db.execute(select(ReviewResponse))).scalars().first().status == "awaiting_moderation"


def test_a_rate_limited_recheck_stops_that_connection(monkeypatch):
    db = _run(_new_db()); _run(_seed_awaiting(db))
    _bind(monkeypatch, db)
    calls = {"n": 0}
    async def _throttled(self, token, external_review_id, comment_id):
        calls["n"] += 1
        raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
    monkeypatch.setattr(YandexReviewProvider, "answer_status", _throttled)

    out = _run(pipe.run_reconcile_moderation())

    assert calls["n"] == 1 and out["errors"] >= 1
    assert _run(db.execute(select(ReviewResponse))).scalars().first().status == "awaiting_moderation"


def test_recheck_skips_marketplaces_that_do_not_moderate(monkeypatch):
    """WB and Ozon expose no answer_status, so the sweep must not touch them at all."""
    wb = get_review_provider("wildberries")
    assert getattr(wb, "answer_status", None) is None
