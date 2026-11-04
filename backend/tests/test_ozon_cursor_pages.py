"""Strict synthetic HTTP contract for Ozon prices/stocks cursor pages."""
import asyncio

import httpx
import pytest

from services.marketplace.ozon_client import OzonClient
from services.marketplace.errors import ExecutionError, MarketplacePaginationIncomplete

METHODS = [("product_prices", "/v5/product/info/prices"),
           ("product_stocks", "/v4/product/info/stocks")]


def transport(monkeypatch, payload, statuses=(200,)):
    calls = []
    codes = iter(statuses)

    class SyntheticHTTP:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return httpx.Response(next(codes), json=payload)

    monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
    return calls


@pytest.mark.parametrize("operation,path", METHODS)
def test_wire_cursor_and_normalized_internal_page(monkeypatch, operation, path):
    items = [{"product_id": 701, "offer_id": "SYNTHETIC-OFFER", "price": {"price": "10.00"}}]
    calls = transport(monkeypatch, {"items": items, "cursor": "next-token", "total_items": 1})
    page = asyncio.run(getattr(OzonClient(), operation)(
        token="synthetic-key", client_id="synthetic-client", last_id="opaque: вход / =", limit=50))
    assert page == {"result": {"items": items, "last_id": "next-token"}}
    assert len(calls) == 1
    method, url, request = calls[0]
    assert method == "POST" and url.endswith(path)
    assert request["json"] == {"filter": {"visibility": "ALL"},
                               "cursor": "opaque: вход / =", "limit": 50}
    assert request["headers"]["Api-Key"] == "synthetic-key"
    assert request["headers"]["Client-Id"] == "synthetic-client"


@pytest.mark.parametrize("operation,path", METHODS)
@pytest.mark.parametrize("items,cursor", [([], ""), ([], "next"),
                                        ([{"product_id": 701}], "")])
def test_empty_and_terminal_pages_keep_the_actual_cursor(monkeypatch, operation, path, items, cursor):
    transport(monkeypatch, {"items": items, "cursor": cursor})
    page = asyncio.run(getattr(OzonClient(), operation)(token="synthetic", client_id="client"))
    assert page == {"result": {"items": items, "last_id": cursor}}


@pytest.mark.parametrize("operation,path", METHODS)
@pytest.mark.parametrize("payload", [None, [], {}, {"items": [], "cursor": None},
                                     {"items": [], "cursor": 3}, {"items": []},
                                     {"items": {}, "cursor": ""},
                                     {"items": [None], "cursor": ""},
                                     {"items": [{}], "cursor": ""},
                                     {"result": {"items": [], "last_id": ""}}])
def test_malformed_wire_page_is_not_empty_success(monkeypatch, operation, path, payload):
    transport(monkeypatch, payload)
    with pytest.raises(MarketplacePaginationIncomplete) as caught:
        asyncio.run(getattr(OzonClient(), operation)(token="synthetic", client_id="client"))
    assert caught.value.reason == "schema"


@pytest.mark.parametrize("operation,path", METHODS)
def test_repeated_nonempty_cursor_is_incomplete(monkeypatch, operation, path):
    transport(monkeypatch, {"items": [{"product_id": 701}], "cursor": "same-token"})
    with pytest.raises(MarketplacePaginationIncomplete) as caught:
        asyncio.run(getattr(OzonClient(), operation)(
            token="synthetic", client_id="client", last_id="same-token"))
    assert caught.value.reason == "cycle"
    assert "same-token" not in str(caught.value)


@pytest.mark.parametrize("operation,path", METHODS)
def test_read_retry_reuses_exact_cursor_body(monkeypatch, operation, path):
    calls = transport(monkeypatch, {"items": [], "cursor": ""}, statuses=(503, 200))
    client = OzonClient()

    async def no_wait(attempt):
        pass

    monkeypatch.setattr(client._seller, "_backoff", no_wait)
    asyncio.run(getattr(client, operation)(token="synthetic", client_id="client", last_id="resume"))
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][2]["json"]["cursor"] == "resume"


@pytest.mark.parametrize("operation,path", METHODS)
@pytest.mark.parametrize("code", [ExecutionError.AUTH, ExecutionError.RATE_LIMIT,
                                  ExecutionError.TIMEOUT, ExecutionError.MARKETPLACE_5XX])
def test_original_errors_are_preserved(monkeypatch, operation, path, code):
    marker = ExecutionError(code, "synthetic failure")
    client = OzonClient()

    async def response(*args, **kwargs):
        raise marker

    monkeypatch.setattr(client._seller, "request", response)
    with pytest.raises(ExecutionError) as caught:
        asyncio.run(getattr(client, operation)(token="synthetic", client_id="client"))
    assert caught.value is marker
