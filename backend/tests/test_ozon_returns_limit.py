"""Ozon /v1/returns/list default respects the documented maximum of 500."""
import asyncio

import httpx
import pytest

from services.marketplace.ozon_client import OzonClient


def _transport(monkeypatch, statuses=(200,)):
    calls = []
    remaining = iter(statuses)

    class SyntheticHTTP:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return httpx.Response(next(remaining), json={"returns": [], "has_next": False})

    monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
    return calls


def test_default_returns_page_limit_is_500(monkeypatch):
    calls = _transport(monkeypatch)
    result = asyncio.run(OzonClient().returns_list(
        token="synthetic-api-key", client_id="synthetic-client", last_id=321))
    assert result == {"returns": [], "has_next": False}
    assert len(calls) == 1
    method, url, request = calls[0]
    assert method == "POST" and url.endswith("/v1/returns/list")
    assert request["json"] == {"filter": {}, "limit": 500, "last_id": 321}
    assert request["headers"]["Api-Key"] == "synthetic-api-key"
    assert request["headers"]["Client-Id"] == "synthetic-client"


@pytest.mark.parametrize("limit", [20, 100, 500])
def test_explicit_supported_returns_page_limit_is_preserved(monkeypatch, limit):
    calls = _transport(monkeypatch)
    asyncio.run(OzonClient().returns_list(
        token="synthetic-api-key", client_id="synthetic-client", last_id=654, limit=limit))
    assert calls[0][2]["json"] == {"filter": {}, "limit": limit, "last_id": 654}


def test_default_limit_and_cursor_survive_safe_read_retry(monkeypatch):
    calls = _transport(monkeypatch, (503, 200))
    client = OzonClient()

    async def no_wait(attempt):
        pass

    monkeypatch.setattr(client._seller, "_backoff", no_wait)
    asyncio.run(client.returns_list(token="synthetic-api-key", client_id="synthetic-client",
                                   last_id=987))
    assert len(calls) == 2
    assert calls[0][2] == calls[1][2]
    assert calls[0][2]["json"] == {"filter": {}, "limit": 500, "last_id": 987}
