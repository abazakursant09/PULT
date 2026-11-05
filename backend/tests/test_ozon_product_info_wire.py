"""The supplied /v3/product/info/list schema requires strings for int64 identifiers."""
import asyncio

import httpx
import pytest

from services.marketplace.ozon_client import OzonClient


def transport(monkeypatch, response, statuses=(200,)):
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
            return httpx.Response(next(codes), json=response)

    monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
    return calls


@pytest.mark.parametrize("identifiers,expected", [
    ([701], ["701"]), (["000701"], ["701"]),
    ([9007199254740993], ["9007199254740993"]),
    (["9223372036854775807"], ["9223372036854775807"]),
    ([701, "000702", True, None, "not-an-id", -1, 3.14], ["701", "702"]),
    (["١٢"], ["12"]),
])
def test_int64_identifiers_are_strings_without_float_precision_loss(monkeypatch, identifiers, expected):
    rows = [{"id": 701, "offer_id": "SYNTHETIC-ARTICLE", "sku": 880001}]
    calls = transport(monkeypatch, {"items": rows})
    assert asyncio.run(OzonClient().product_info_list(
        token="synthetic-key", client_id="synthetic-client", product_ids=identifiers)) == rows
    assert len(calls) == 1
    method, url, request = calls[0]
    assert method == "POST" and url.endswith("/v3/product/info/list")
    assert request["json"] == {"product_id": expected}
    assert request["headers"]["Api-Key"] == "synthetic-key"
    assert request["headers"]["Client-Id"] == "synthetic-client"


@pytest.mark.parametrize("wrapped", [False, True])
def test_root_and_legacy_enveloped_response_compatibility_is_preserved(monkeypatch, wrapped):
    rows = [{"id": 701, "offer_id": "SYNTHETIC-ARTICLE", "sku": 880001}]
    response = {"result": {"items": rows}} if wrapped else {"items": rows}
    transport(monkeypatch, response)
    result = asyncio.run(OzonClient().product_info_list(
        token="synthetic", client_id="client", product_ids=[701]))
    assert result == rows


def test_safe_read_retry_preserves_string_identifiers_and_credentials(monkeypatch):
    calls = transport(monkeypatch, {"items": []}, (503, 200))
    client = OzonClient()

    async def no_wait(attempt):
        pass

    monkeypatch.setattr(client._seller, "_backoff", no_wait)
    assert asyncio.run(client.product_info_list(
        token="synthetic", client_id="client", product_ids=[9007199254740993])) == []
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0][2]["json"] == {"product_id": ["9007199254740993"]}
