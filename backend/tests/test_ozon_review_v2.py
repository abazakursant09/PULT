"""Synthetic wire contracts; no seller credentials or provider traffic."""
import asyncio

import httpx
import pytest

from services.marketplace.ozon_client import OzonClient


def wire(monkeypatch, responses):
    calls = []
    pending = iter(responses)

    class SyntheticHTTP:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            response = next(pending)
            if isinstance(response, BaseException):
                raise response
            if isinstance(response, httpx.Response):
                return response
            if callable(response):
                return await response(method, url, kwargs)
            return httpx.Response(200, json=response)

    monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
    return calls


def detail(review_id="review-1", sku=701, status="NEW"):
    return {"id": review_id, "sku": sku, "status": status,
            "published_at": "2026-11-01T10:00:00Z", "rating": 4,
            "text": "Synthetic review"}


def test_v2_page_uses_documented_root_and_omits_initial_optional_fields(monkeypatch):
    calls = wire(monkeypatch, [{"reviews": [], "has_next": False, "last_id": ""}])
    page = asyncio.run(OzonClient().review_list_page_v2(
        token="synthetic-key", client_id="synthetic-client"))
    assert page.reviews == () and page.has_next is False and page.last_id == ""
    assert len(calls) == 1
    assert calls[0][0] == "POST" and calls[0][1].endswith("/v2/review/list")
    assert calls[0][2]["json"] == {"limit": 100, "sort_dir": "ASC"}
    assert calls[0][2]["headers"]["Api-Key"] == "synthetic-key"
    assert calls[0][2]["headers"]["Client-Id"] == "synthetic-client"


def test_info_rejects_wrong_identity_instead_of_relabelling(monkeypatch):
    wire(monkeypatch, [detail("other-review")])
    from services.marketplace.errors import ExecutionError
    with pytest.raises(ExecutionError):
        asyncio.run(OzonClient().review_info_v2(
            token="synthetic", client_id="client", review_id="review-1", expected_sku=701))


def test_late_malformed_page_does_not_return_partial_selection(monkeypatch):
    calls = wire(monkeypatch, [
        {"reviews": [{"id": "review-1"}], "has_next": True, "last_id": "opaque-next"},
        {"reviews": None, "has_next": False, "last_id": ""},
    ])
    from services.marketplace.errors import ExecutionError
    with pytest.raises(ExecutionError):
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client"))
    assert len(calls) == 2


@pytest.mark.parametrize("payload", [None, [], {}, {"reviews": []},
    {"reviews": [], "has_next": 0, "last_id": ""},
    {"reviews": [], "has_next": "false", "last_id": ""},
    {"reviews": {}, "has_next": False, "last_id": ""},
    {"reviews": [None], "has_next": False, "last_id": ""},
    {"reviews": [{}], "has_next": False, "last_id": ""},
    {"reviews": [{"id": 1}], "has_next": False, "last_id": ""},
    {"reviews": [{"id": " "}], "has_next": False, "last_id": ""},
    {"reviews": [], "has_next": True, "last_id": ""},
    {"reviews": [], "has_next": True, "last_id": "   "},
    {"reviews": [], "has_next": False, "last_id": None},
    {"result": {"reviews": [], "has_next": False, "last_id": ""}},
    {"reviews": [{"id": "x"}] * 21, "has_next": False, "last_id": ""}])
def test_malformed_pages_fail_closed(monkeypatch, payload):
    from services.marketplace.ozon_review_read import OzonReviewReadIncomplete
    wire(monkeypatch, [payload])
    with pytest.raises(OzonReviewReadIncomplete):
        asyncio.run(OzonClient().review_list_page_v2(
            token="synthetic", client_id="client", limit=20))


@pytest.mark.parametrize("options", [
    {"limit": True}, {"limit": 19}, {"limit": 101}, {"limit": "20"},
    {"limit": 20.0}, {"sort_dir": "asc"}, {"sort_dir": None},
    {"last_id": 4}, {"last_id": "x" * 4097}])
def test_invalid_page_options_never_dispatch(monkeypatch, options):
    from services.marketplace.errors import ExecutionError
    calls = wire(monkeypatch, [])
    with pytest.raises(ExecutionError) as caught:
        asyncio.run(OzonClient().review_list_page_v2(
            token="synthetic", client_id="client", **options))
    assert caught.value.code == ExecutionError.VALIDATION
    assert calls == []


@pytest.mark.parametrize("cursor", ["", "opaque: вход / ="])
def test_explicit_cursor_is_preserved_not_normalized(monkeypatch, cursor):
    calls = wire(monkeypatch, [{"reviews": [], "has_next": False, "last_id": cursor}])
    asyncio.run(OzonClient().review_list_page_v2(token="synthetic", client_id="client",
                                              last_id=cursor, sort_dir="DESC", limit=20))
    assert calls[0][2]["json"] == {"limit": 20, "sort_dir": "DESC", "last_id": cursor}


@pytest.mark.parametrize("field,value", [
    ("id", None), ("id", 1), ("sku", True), ("sku", "701"), ("sku", 701.0),
    ("sku", 2**63), ("sku", -(2**63)-1), ("rating", True), ("rating", "4"),
    ("rating", 2**31), ("rating", -(2**31)-1), ("status", None), ("status", []),
    ("status", "UNPROCESSED"), ("status", "ALL"), ("text", None),
    ("published_at", "2026-11-01"), ("published_at", "2026-11-01T10:00:00"),
    ("published_at", "2026-11-01T10:00:00+01:60"),
    ("published_at", "2026-11-01T10:00:00-00:99"),
    ("published_at", "2026-02-30T10:00:00Z"), ("published_at", 1)])
def test_info_core_fields_are_strict(monkeypatch, field, value):
    from services.marketplace.ozon_review_read import OzonReviewReadIncomplete
    payload = detail()
    payload[field] = value
    wire(monkeypatch, [payload])
    with pytest.raises(OzonReviewReadIncomplete):
        asyncio.run(OzonClient().review_info_v2(
            token="synthetic", client_id="client", review_id="review-1"))


@pytest.mark.parametrize("missing", ["id", "sku", "rating", "status", "text", "published_at"])
def test_missing_local_result_fields_are_not_defaults(monkeypatch, missing):
    from services.marketplace.ozon_review_read import OzonReviewReadIncomplete
    payload = detail()
    del payload[missing]
    wire(monkeypatch, [payload])
    with pytest.raises(OzonReviewReadIncomplete):
        asyncio.run(OzonClient().review_info_v2(
            token="synthetic", client_id="client", review_id="review-1"))


@pytest.mark.parametrize("status", ["NEW", "VIEWED", "PROCESSED"])
@pytest.mark.parametrize("sku,rating", [(2**63-1, 2**31-1), (-(2**63), -(2**31))])
def test_info_preserves_exact_documented_types_without_business_guesses(monkeypatch, status, sku, rating):
    payload = detail("1", sku, status)
    payload.update(rating=rating, text="", comments_amount=12, unknown_future={"x": 1})
    calls = wire(monkeypatch, [payload])
    info = asyncio.run(OzonClient().review_info_v2(token="synthetic", client_id="client",
                                                 review_id="1", expected_sku=sku))
    assert (info.id, info.sku, info.rating, info.status, info.text) == ("1", sku, rating, status, "")
    assert not hasattr(info, "unanswered") and not hasattr(info, "product_ref")
    assert calls[0][1].endswith("/v2/review/info")
    assert calls[0][2]["json"] == {"review_id": "1"}


def test_expected_sku_mismatch_is_not_overwritten(monkeypatch):
    from services.marketplace.ozon_review_read import OzonReviewReadIncomplete
    wire(monkeypatch, [detail(sku=702)])
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().review_info_v2(token="synthetic", client_id="client",
                                              review_id="review-1", expected_sku=701))
    assert caught.value.reason == "identity"


@pytest.mark.parametrize("options", [{"review_id": ""}, {"review_id": 1},
    {"review_id": "x", "expected_sku": True}, {"review_id": "x", "expected_sku": "701"}])
def test_invalid_detail_options_never_dispatch(monkeypatch, options):
    from services.marketplace.errors import ExecutionError
    calls = wire(monkeypatch, [])
    with pytest.raises(ExecutionError) as caught:
        asyncio.run(OzonClient().review_info_v2(token="synthetic", client_id="client", **options))
    assert caught.value.code == ExecutionError.VALIDATION and calls == []


@pytest.mark.parametrize("status,code", [(420, "RATE_LIMIT"), (429, "RATE_LIMIT"),
    (503, "MARKETPLACE_5XX"), (401, "AUTH"), (403, "AUTH"), (400, "MARKETPLACE_4XX")])
def test_v2_makes_one_attempt_and_sanitizes_errors(monkeypatch, status, code):
    from services.marketplace.errors import ExecutionError
    calls = wire(monkeypatch, [httpx.Response(status, text="UNTRUSTED-REVIEW-OR-SECRET")])

    async def no_sleep(*args):
        pytest.fail("v2 must not sleep or retry")

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    with pytest.raises(ExecutionError) as caught:
        asyncio.run(OzonClient().review_list_page_v2(token="secret-key", client_id="secret-client"))
    assert caught.value.code == code and len(calls) == 1
    assert "UNTRUSTED" not in str(caught.value) and "secret" not in str(caught.value)


def test_timeout_is_one_attempt_and_keeps_error_taxonomy(monkeypatch):
    from services.marketplace.errors import ExecutionError
    calls = wire(monkeypatch, [httpx.ReadTimeout("secret-url-and-token")])
    with pytest.raises(ExecutionError) as caught:
        asyncio.run(OzonClient().review_info_v2(token="synthetic", client_id="client", review_id="1"))
    assert caught.value.code == ExecutionError.TIMEOUT and len(calls) == 1
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("response", [httpx.Response(200, content=b""),
    httpx.Response(200, text="not json"), httpx.Response(204)])
def test_shared_parser_fallback_is_not_empty_v2_success(monkeypatch, response):
    from services.marketplace.ozon_review_read import OzonReviewReadIncomplete
    wire(monkeypatch, [response])
    with pytest.raises(OzonReviewReadIncomplete):
        asyncio.run(OzonClient().review_list_page_v2(token="synthetic", client_id="client"))


def test_deep_raw_json_parse_error_is_controlled_incomplete(monkeypatch):
    import sys
    from services.marketplace.ozon_review_read import OzonReviewReadIncomplete
    depth = sys.getrecursionlimit() + 100
    raw = '{"reviews":[],"has_next":false,"last_id":"","extra":' + "[" * depth + "0" + "]" * depth + "}"
    calls = wire(monkeypatch, [httpx.Response(200, text=raw)])
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().review_list_page_v2(token="synthetic", client_id="client"))
    assert caught.value.reason == "schema" and len(calls) == 1


@pytest.mark.parametrize("error", [RecursionError("untrusted parse detail"),
                                   UnicodeError("untrusted decoding detail")])
def test_shared_decoder_exception_is_controlled_incomplete(monkeypatch, error):
    from services.marketplace.base_client import BaseMarketplaceClient
    from services.marketplace.ozon_review_read import OzonReviewReadIncomplete
    calls = wire(monkeypatch, [httpx.Response(200, text="synthetic")])

    def failed_decode(response):
        raise error

    monkeypatch.setattr(BaseMarketplaceClient, "_handle_response", staticmethod(failed_decode))
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().review_list_page_v2(token="synthetic", client_id="client"))
    assert caught.value.reason == "schema" and len(calls) == 1
    assert "untrusted" not in str(caught.value)
