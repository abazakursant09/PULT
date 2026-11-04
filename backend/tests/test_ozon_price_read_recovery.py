"""Ozon price recovery: real read transport, complete evidence, no authorisation/write.

Credential resolution and HTTP are synthetic, and backoff skips real waits.
The real OzonClient, BaseMarketplaceClient, page normalisation and observe()
remain under test; database/provider writes are forbidden.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from services.marketplace import ozon_client as ozon_module
from services.marketplace.ozon_client import OzonClient
from services.marketplace.recovery import reconcile_read
from test_marketplace_retry_policy import no_external_io  # noqa: F401


_OFFER = "synthetic-seller-article"
_TOKEN = "synthetic-not-a-real-api-key"
_CLIENT_ID = "synthetic-owned-client"
_READ_URL = "https://synthetic.invalid/v5/product/info/prices"


def _item(amount="100.00", *, offer_id=_OFFER, product_id=880001):
    return {"offer_id": offer_id, "product_id": product_id,
            "price": {"price": amount}}


def _page(items, cursor=""):
    # Raw provider envelope, deliberately NOT the internal result/last_id envelope.
    return {"items": items, "cursor": cursor, "total_items": len(items)}


@pytest.fixture
def price_read(monkeypatch):
    violations, writes, database_calls = [], [], []

    def require(condition, message):
        if not condition:
            # observe() catches Exception: keep failures outside that catch too.
            violations.append(message)
            raise AssertionError(message)

    class NoDatabase:
        def __getattr__(self, name):
            database_calls.append(name)
            raise AssertionError("Resolved price observation must not use a database")

    def run(pages, *, offer_id=_OFFER, intent="100.00"):
        calls = []
        db = NoDatabase()
        row = SimpleNamespace(
            id="synthetic-execution", user_id="synthetic-owner",
            connection_id="synthetic-connection", marketplace="ozon",
            action_type="set_price", mode="manual_l3", status="ambiguous",
            payload={"marketplace": "ozon", "offer_id": offer_id, "price": intent},
            claim_generation=7, dispatch_started_at="synthetic-dispatch-marker",
            reconciliation_status="still_unknown", request_fingerprint="synthetic-fp",
            metadata={"do_not_change": ["synthetic"]},
        )
        connection = SimpleNamespace(
            id=row.connection_id, user_id=row.user_id, marketplace="ozon",
            status="connected", ozon_client_id=_CLIENT_ID,
        )
        before_row, before_connection = deepcopy(vars(row)), deepcopy(vars(connection))

        async def resolve(actual_db, actual_row):
            require(actual_db is db and actual_row is row, "Unexpected resolution subject")
            require(connection.user_id == row.user_id, "Connection is not owned")
            return "ozon", _TOKEN, None, connection

        async def forbidden_write(*args, **kwargs):
            writes.append("provider-write")
            raise AssertionError("An observation never authorises a provider write")

        class SyntheticHTTP:
            def __init__(self, *, timeout):
                require(timeout is not None, "Transport timeout disappeared")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def request(self, method, url, **kwargs):
                require(method == "POST" and url == _READ_URL,
                        "Unexpected endpoint or provider write")
                require(set(kwargs) == {"headers", "json", "params"},
                        "Unexpected transport arguments")
                require(kwargs["params"] is None, "Price read must use the JSON body")
                require(kwargs["headers"] == {
                    "Api-Key": _TOKEN, "Client-Id": _CLIENT_ID,
                    "Content-Type": "application/json",
                }, "Read credentials/header shape changed")
                body = kwargs["json"]
                require(isinstance(body, dict), "Price request body is not an object")
                cursor = body.get("cursor")
                calls.append(cursor)
                require(len(calls) <= 24, "Recovery exceeded the synthetic read budget")
                require(body == {"filter": {"visibility": "ALL"},
                                 "cursor": cursor, "limit": 1000},
                        "Wire body must use cursor, not the public last_id argument")
                require(isinstance(cursor, str) and cursor in pages,
                        "Unexpected, missing or excessive cursor request")
                response = pages[cursor]
                if isinstance(response, Exception):
                    raise response
                if isinstance(response, tuple):
                    status, payload = response
                    return httpx.Response(status, json=deepcopy(payload))
                return httpx.Response(200, json=deepcopy(response))

        client = OzonClient()
        client._seller._base_url = "https://synthetic.invalid"

        async def no_wait(attempt):
            # Preserve the real retry decisions, without real waiting.
            return None

        monkeypatch.setattr(client._seller, "_backoff", no_wait)
        monkeypatch.setattr(ozon_module, "ozon_client", client)
        monkeypatch.setattr(reconcile_read, "_resolve", resolve)
        monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
        for name in ("set_price", "set_auto_promotion", "set_campaign_state",
                     "publish_feedback_answer"):
            monkeypatch.setattr(OzonClient, name, forbidden_write)
        try:
            # asyncio.run closes its event loop, including on assertion/error paths.
            verdict = asyncio.run(reconcile_read.observe(db, row))
        finally:
            assert vars(row) == before_row, "observe mutated the execution row or payload"
            assert vars(connection) == before_connection, "observe mutated the connection"
            assert writes == []
            assert database_calls == []
        assert verdict in {"intent_observed", "target_not_observed", "still_unknown"}
        return verdict, calls

    yield run
    assert violations == [], "Strict HTTP assertions were swallowed by observe: " + repr(violations)
    assert writes == []
    assert database_calls == []


@pytest.mark.parametrize("amount", [100, 100.0, "100", "100.000"])
def test_later_page_numeric_equal_price_is_intent_observed(price_read, amount):
    verdict, calls = price_read({
        "": _page([_item("999", offer_id="other-article")], "P2"),
        "P2": _page([_item(amount)]),
    }, intent="100.00")
    assert verdict == "intent_observed"
    assert calls == ["", "P2"]


@pytest.mark.parametrize("amount", [101.0, "99.00"])
def test_complete_unique_price_mismatch_is_only_target_not_observed(price_read, amount):
    verdict, calls = price_read({"": _page([_item(amount)], "P2"),
                                 "P2": _page([])})
    assert verdict == "target_not_observed"
    assert calls == ["", "P2"]


def test_empty_intermediate_page_does_not_hide_later_match(price_read):
    verdict, calls = price_read({"": _page([], "P2"),
                                 "P2": _page([], "P3"),
                                 "P3": _page([_item()])})
    assert verdict == "intent_observed"
    assert calls == ["", "P2", "P3"]


def test_absent_offer_after_complete_scan_is_still_unknown(price_read):
    verdict, calls = price_read({"": _page([], "P2"),
                                 "P2": _page([_item(offer_id="other-article")])})
    assert verdict == "still_unknown"
    assert calls == ["", "P2"]


@pytest.mark.parametrize("bad_item", [{"product_id": 880009},
                                     {"product_id": 880009, "offer_id": None},
                                     {"product_id": 880009, "offer_id": 7}])
def test_unproved_later_article_identity_invalidates_uniqueness(price_read, bad_item):
    verdict, calls = price_read({"": _page([_item()], "P2"), "P2": _page([bad_item])})
    assert verdict == "still_unknown" and calls == ["", "P2"]


@pytest.mark.parametrize("bad_page", [
    None, [], {}, {"cursor": ""}, {"items": None, "cursor": ""},
    {"items": "not-an-array", "cursor": ""},
    {"items": [None], "cursor": ""},
    {"items": [], "cursor": 7}, {"items": [], "cursor": True},
    {"items": [], "cursor": None},
])
def test_malformed_later_page_invalidates_an_early_match(price_read, bad_page):
    verdict, calls = price_read({"": _page([_item()], "P2"), "P2": bad_page})
    assert verdict == "still_unknown"
    assert calls == ["", "P2"]


@pytest.mark.parametrize("price_node", [
    None, 100.0, "100.00", {}, {"price": None}, {"price": True},
    {"price": "not-money"}, {"price": "NaN"}, {"price": "Infinity"},
    {"price": "-Infinity"}, {"price": -1}, {"price": []}, {"price": {}},
])
def test_invalid_or_non_nested_observed_price_is_still_unknown(price_read, price_node):
    item = _item()
    item["price"] = price_node
    verdict, calls = price_read({"": _page([item])})
    assert verdict == "still_unknown"
    assert calls == [""]


@pytest.mark.parametrize("intent", [None, True, "not-money", "NaN", "Infinity", -1])
def test_invalid_intent_never_becomes_a_positive_or_mismatch_verdict(price_read, intent):
    verdict, _ = price_read({"": _page([_item()])}, intent=intent)
    assert verdict == "still_unknown"


@pytest.mark.parametrize("later_amount", ["100.00", "101.00"])
def test_duplicate_offer_across_pages_is_ambiguous_even_when_prices_agree(price_read, later_amount):
    verdict, calls = price_read({"": _page([_item()], "P2"),
                                 "P2": _page([_item(later_amount, product_id=880002)])})
    assert verdict == "still_unknown"
    assert calls == ["", "P2"]


def test_duplicate_offer_on_one_page_is_not_a_unique_match(price_read):
    verdict, calls = price_read({"": _page([_item(), _item()])})
    assert verdict == "still_unknown"
    assert calls == [""]


def test_numeric_seller_article_never_matches_product_id_alias(price_read):
    verdict, calls = price_read({"": _page([
        _item(offer_id="different-article", product_id=700001),
    ])}, offer_id="700001")
    assert verdict == "still_unknown"
    assert calls == [""]


def test_exact_numeric_article_wins_only_its_own_offer_id_not_an_alias(price_read):
    verdict, calls = price_read({"": _page([
        _item("9.00", offer_id="different-article", product_id=700001),
        _item(100.0, offer_id="700001", product_id=880003),
    ])}, offer_id="700001", intent="100.00")
    assert verdict == "intent_observed"
    assert calls == [""]


@pytest.mark.parametrize("error", [
    (403, {"message": "synthetic-auth"}),
    (429, {"message": "synthetic-rate-limit"}),
    (503, {"message": "synthetic-unavailable"}),
    httpx.ReadTimeout("synthetic-read-timeout"),
])
def test_later_page_error_never_classifies_an_early_match(price_read, error):
    verdict, calls = price_read({"": _page([_item()], "P2"), "P2": error})
    assert verdict == "still_unknown"
    assert calls[0] == "" and 2 <= len(calls) <= 4
    assert all(cursor == "P2" for cursor in calls[1:])


@pytest.mark.parametrize("cycle", [False, True])
def test_cursor_no_progress_or_cycle_never_returns_early_match(price_read, cycle):
    pages = {"": _page([_item()], "A"), "A": _page([], "A")}
    expected = ["", "A"]
    if cycle:
        pages["A"] = _page([], "B")
        pages["B"] = _page([], "A")
        expected.append("B")
    verdict, calls = price_read(pages)
    assert verdict == "still_unknown"
    assert calls == expected


def test_twenty_nonterminal_pages_are_incomplete_even_with_an_early_match(price_read):
    assert getattr(reconcile_read, "_OZON_PRICE_MAX_PAGES", None) == 20
    cursors = [""] + [f"P{i}" for i in range(1, 20)]
    pages = {cursor: _page([_item()] if i == 0 else [], f"P{i + 1}")
             for i, cursor in enumerate(cursors)}
    verdict, calls = price_read(pages)
    assert verdict == "still_unknown"
    assert calls == cursors


def test_terminal_twentieth_page_is_complete_not_an_off_by_one_cap(price_read):
    cursors = [""] + [f"P{i}" for i in range(1, 20)]
    pages = {cursor: _page([], f"P{i + 1}") for i, cursor in enumerate(cursors)}
    pages["P19"] = _page([_item(100.0)])
    verdict, calls = price_read(pages, intent="100.00")
    assert verdict == "intent_observed"
    assert calls == cursors
