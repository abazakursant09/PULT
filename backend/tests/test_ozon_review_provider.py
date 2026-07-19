"""R-OZ1 — Ozon review provider (fetch mapping, publish, registration, error handling).

The provider is real and unit-tested here, but it is not yet wired into the live executor publish
path and its `supports_reviews()` stays False until R-OZ3 — so these tests exercise the provider
methods directly. WB behaviour is asserted unchanged.
"""
import asyncio

import pytest

from services.marketplace.reviews import get_review_provider, REVIEW_PROVIDERS
from services.marketplace.reviews.ozon import OzonReviewProvider
from services.marketplace.reviews.wildberries import WildberriesReviewProvider
from services.marketplace.reviews.base import NormalizedReview
from services.marketplace.errors import ExecutionError
from services.marketplace import ozon_client as ozon_client_mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── registration ─────────────────────────────────────────────────────────────

def test_ozon_provider_registered():
    p = get_review_provider("ozon")
    assert isinstance(p, OzonReviewProvider)


def test_wb_provider_unchanged():
    p = get_review_provider("wildberries")
    assert isinstance(p, WildberriesReviewProvider)
    assert p.supports_reviews() is True          # WB still live


def test_yandex_now_supported_megamarket_still_is_not():
    # Yandex gained a real provider (fetch + publish against the Partner API). Megamarket has none,
    # so it still resolves to None and the router answers an honest "unsupported".
    assert get_review_provider("yandex") is not None
    assert get_review_provider("yandex").supports_reviews() is True
    assert get_review_provider("megamarket") is None


def test_ozon_enabled_r_oz3():
    # R-OZ3 enabled Ozon: supports_reviews() True, in lockstep with capability_registry
    # pult_supported=true. The /sync router now accepts Ozon (given a connection).
    assert get_review_provider("ozon").supports_reviews() is True


# ── fetch mapping ────────────────────────────────────────────────────────────

def test_ozon_fetch_maps_reviews(monkeypatch):
    async def fake_list(*, token, client_id, product_ref, limit=100):
        assert (client_id, token) == ("CID", "APIKEY")     # composite split correctly
        assert product_ref == "SKU-1"
        return [
            {"id": "r1", "text": "отлично", "rating": 5, "author_name": "Иван",
             "published_at": "2026-07-14T10:00:00Z", "status": "UNPROCESSED"},
            {"review_id": "r2", "comment": "плохо", "mark": 2},   # alt keys, no date/author
            {"text": "no id"},                                    # dropped: no external id
        ]
    monkeypatch.setattr(ozon_client_mod.ozon_client, "list_reviews", fake_list)

    reviews = _run(OzonReviewProvider().fetch_reviews("CID:APIKEY", "SKU-1"))
    assert len(reviews) == 2
    a, b = reviews
    assert isinstance(a, NormalizedReview)
    assert (a.marketplace, a.external_review_id, a.rating, a.text, a.author) == \
        ("ozon", "r1", 5, "отлично", "Иван")
    assert a.review_created_at is not None
    assert a.metadata == {"status": "UNPROCESSED"}
    # alt-key row maps too, with defensive None where fields are absent
    assert (b.external_review_id, b.rating, b.text, b.author, b.review_created_at) == \
        ("r2", 2, "плохо", None, None)


# ── publish ──────────────────────────────────────────────────────────────────

def test_ozon_publish_routes_credentials(monkeypatch):
    seen = {}
    async def fake_publish(*, token, client_id, review_id, text):
        seen.update(token=token, client_id=client_id, review_id=review_id, text=text)
        return {"comment_id": 123}
    monkeypatch.setattr(ozon_client_mod.ozon_client, "publish_feedback_answer", fake_publish)

    res = _run(OzonReviewProvider().publish_answer("CID:APIKEY", "r1", "Спасибо!"))
    assert res == {"comment_id": 123}
    assert seen == {"token": "APIKEY", "client_id": "CID", "review_id": "r1", "text": "Спасибо!"}


# ── error handling ───────────────────────────────────────────────────────────

def test_bad_credential_raises_auth():
    for bad in ("", "no-colon", ":apikey", "cid:"):
        with pytest.raises(ExecutionError) as e:
            _run(OzonReviewProvider().fetch_reviews(bad, "SKU-1"))
        assert e.value.code == ExecutionError.AUTH


def test_client_error_propagates_ambiguous(monkeypatch):
    # A TIMEOUT from the client must propagate unchanged so AR5 classifies it as ambiguous.
    async def fake_publish(*, token, client_id, review_id, text):
        raise ExecutionError(ExecutionError.TIMEOUT, "marketplace timeout")
    monkeypatch.setattr(ozon_client_mod.ozon_client, "publish_feedback_answer", fake_publish)

    with pytest.raises(ExecutionError) as e:
        _run(OzonReviewProvider().publish_answer("CID:APIKEY", "r1", "x"))
    assert e.value.code == ExecutionError.TIMEOUT
    assert ExecutionError.is_ambiguous_error(e.value.code) is True


# ── ozon_client request shape (real endpoints, taxonomy via base_client) ──────

def test_ozon_client_list_reviews_request(monkeypatch):
    calls = {}
    async def fake_request(method, path, *, token, auth_header, extra_headers, json):
        calls.update(method=method, path=path, auth_header=auth_header,
                     client_id=extra_headers.get("Client-Id"), body=json)
        return {"reviews": [{"id": "r1"}]}
    monkeypatch.setattr(ozon_client_mod.ozon_client._seller, "request", fake_request)

    rows = _run(ozon_client_mod.ozon_client.list_reviews(token="APIKEY", client_id="CID", product_ref="SKU-1"))
    assert rows == [{"id": "r1"}]
    assert calls["method"] == "POST" and calls["path"] == "/v1/review/list"
    assert calls["auth_header"] == "Api-Key" and calls["client_id"] == "CID"
    assert calls["body"]["product_id"] == "SKU-1"


def test_ozon_client_publish_request(monkeypatch):
    calls = {}
    async def fake_request(method, path, *, token, auth_header, extra_headers, json):
        calls.update(path=path, body=json, client_id=extra_headers.get("Client-Id"))
        return {"comment_id": 1}
    monkeypatch.setattr(ozon_client_mod.ozon_client._seller, "request", fake_request)

    _run(ozon_client_mod.ozon_client.publish_feedback_answer(
        token="APIKEY", client_id="CID", review_id="r1", text="Спасибо!"))
    assert calls["path"] == "/v1/review/comment/create"
    assert calls["body"] == {"review_id": "r1", "text": "Спасибо!", "mark_review_as_processed": False}
    assert calls["client_id"] == "CID"


def test_ozon_client_requires_client_id():
    with pytest.raises(ExecutionError) as e:
        _run(ozon_client_mod.ozon_client.list_reviews(token="APIKEY", client_id=None))
    assert e.value.code == ExecutionError.AUTH
