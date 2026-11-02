"""Semantic replay policy: real transport loop, synthetic HTTP only, no providers."""
import asyncio
import ast
import inspect
from pathlib import Path
import socket
import threading

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    from sqlalchemy import event
    import database

    attempts = []
    local = threading.local()
    original_pair, original_connect = socket.socketpair, socket.socket.connect

    def deny(*args, **kwargs):
        attempts.append("external-or-default-db")
        raise AssertionError("Retry-policy test forbids network/default DB")

    def pair(*args, **kwargs):
        previous = getattr(local, "selfpipe", False)
        local.selfpipe = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            local.selfpipe = previous

    def connect(sock, address):
        if (getattr(local, "selfpipe", False) and isinstance(address, tuple)
                and address[0] in {"127.0.0.1", "::1"}):
            return original_connect(sock, address)
        return deny(sock, address)

    monkeypatch.setattr(socket, "socketpair", pair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    event.listen(database.engine.sync_engine, "do_connect", deny)
    try:
        yield attempts
        assert attempts == []
    finally:
        event.remove(database.engine.sync_engine, "do_connect", deny)


def run_transport(monkeypatch, *, fault, method="POST", policy=None, recover=False, max_retries=2):
    from services.marketplace.base_client import BaseMarketplaceClient
    from services.marketplace.errors import ExecutionError

    calls, waits = [], []

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout is not None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, sent_method, url, *, headers, json, params):
            assert sent_method == method
            assert url == "https://synthetic.invalid/audit-operation"
            assert json == {"synthetic": True} and params is None
            calls.append(sent_method)  # never retain headers or tokens
            sequence = fault if isinstance(fault, list) else [fault]
            current = sequence[min(len(calls)-1, len(sequence)-1)]
            if current == 200 or recover and len(calls) > 1:
                return httpx.Response(200, json={"ok": True})
            if current == "timeout":
                raise httpx.ReadTimeout("synthetic timeout")
            return httpx.Response(current, json={"synthetic_error": True})

    async def no_wait(attempt):
        waits.append(attempt)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(BaseMarketplaceClient, "_backoff", staticmethod(no_wait))
    client = BaseMarketplaceClient("https://synthetic.invalid", max_retries=max_retries)
    result = error = None
    try:
        result = asyncio.run(client.request(
            method, "/audit-operation", token="SYNTHETIC-NOT-A-REAL-TOKEN",
            json={"synthetic": True}, **(policy or {})))
    except ExecutionError as exc:
        error = exc
    return calls, waits, result, error


@pytest.mark.parametrize("fault", ["timeout", 500])
def test_default_post_write_is_single_attempt(monkeypatch, fault):
    from services.marketplace.errors import ExecutionError

    calls, waits, result, error = run_transport(monkeypatch, fault=fault)
    assert len(calls) == 1
    assert waits == [] and result is None
    assert error.code == (ExecutionError.TIMEOUT if fault == "timeout" else ExecutionError.MARKETPLACE_5XX)
    assert ExecutionError.is_ambiguous_error(error.code)


AMBIGUOUS = ["timeout", 500, 502, 503, 504]
UNSAFE_POLICIES = [
    pytest.param({}, id="default"),
    pytest.param({"retry_safe": False}, id="false"),
    pytest.param({"retry_safe": None}, id="none"),
    pytest.param({"retry_safe": 0}, id="zero"),
    pytest.param({"retry_safe": 1}, id="truthy-int"),
    pytest.param({"retry_safe": "true"}, id="truthy-string"),
    pytest.param({"retry_safe": [True]}, id="truthy-list"),
]
BASIC_POLICIES = [{}, {"retry_safe": False}, {"retry_safe": True}]


def assert_ambiguous(error, fault):
    from services.marketplace.errors import ExecutionError
    assert error is not None
    assert error.code == (ExecutionError.TIMEOUT if fault == "timeout" else ExecutionError.MARKETPLACE_5XX)
    assert error.retryable is True  # taxonomy is not permission to replay a write
    assert ExecutionError.is_ambiguous_error(error.code) is True


@pytest.mark.parametrize("policy", UNSAFE_POLICIES)
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("fault", AMBIGUOUS)
def test_only_literal_true_allows_ambiguous_replay(monkeypatch, policy, method, fault):
    calls, waits, result, error = run_transport(monkeypatch, fault=fault, method=method, policy=policy)
    assert len(calls) == 1 and waits == [] and result is None
    assert_ambiguous(error, fault)


@pytest.mark.parametrize("policy", [{}, {"retry_safe": False}])
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("fault", AMBIGUOUS)
def test_write_never_reaches_a_second_success_after_uncertainty(monkeypatch, policy, method, fault):
    calls, waits, result, error = run_transport(
        monkeypatch, fault=fault, method=method, policy=policy, recover=True)
    assert len(calls) == 1 and waits == [] and result is None
    assert_ambiguous(error, fault)


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("fault", AMBIGUOUS)
@pytest.mark.parametrize("recover", [False, True])
def test_explicit_safe_reads_keep_bounded_retries(monkeypatch, method, fault, recover):
    calls, waits, result, error = run_transport(
        monkeypatch, fault=fault, method=method, policy={"retry_safe": True}, recover=recover)
    assert len(calls) == (2 if recover else 3)
    assert waits == ([1] if recover else [1, 2])
    if recover:
        assert result == {"ok": True} and error is None
    else:
        assert result is None
        assert_ambiguous(error, fault)


@pytest.mark.parametrize("policy", BASIC_POLICIES)
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("fault", AMBIGUOUS)
def test_zero_retries_remains_single_attempt(monkeypatch, policy, method, fault):
    calls, waits, result, error = run_transport(
        monkeypatch, fault=fault, method=method, policy=policy, max_retries=0)
    assert len(calls) == 1 and waits == [] and result is None
    assert_ambiguous(error, fault)


@pytest.mark.parametrize("policy", BASIC_POLICIES)
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status", [420, 429])
@pytest.mark.parametrize("recover", [False, True])
def test_clean_throttle_refusals_keep_existing_bounded_behavior(monkeypatch, policy, method, status, recover):
    from services.marketplace.errors import ExecutionError
    calls, waits, result, error = run_transport(
        monkeypatch, fault=status, method=method, policy=policy, recover=recover)
    assert len(calls) == (2 if recover else 3)
    assert waits == ([1] if recover else [1, 2])
    if recover:
        assert result == {"ok": True} and error is None
    else:
        assert result is None and error.code == ExecutionError.RATE_LIMIT
        assert error.retryable is True
        assert not ExecutionError.is_ambiguous_error(error.code)


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status", [420, 429])
@pytest.mark.parametrize("fault", AMBIGUOUS)
@pytest.mark.parametrize("policy", [{}, {"retry_safe": False}])
def test_throttle_then_write_ambiguity_stops_before_eventual_success(monkeypatch, method, status, fault, policy):
    calls, waits, result, error = run_transport(
        monkeypatch, fault=[status, fault, 200], method=method, policy=policy)
    assert len(calls) == 2 and waits == [1] and result is None
    assert_ambiguous(error, fault)


@pytest.mark.parametrize("policy", BASIC_POLICIES)
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_auth_and_plain_4xx_taxonomy_and_no_replay_unchanged(monkeypatch, policy, method, status):
    from services.marketplace.errors import ExecutionError
    calls, waits, result, error = run_transport(monkeypatch, fault=status, method=method, policy=policy)
    assert len(calls) == 1 and waits == [] and result is None
    assert error.code == (ExecutionError.AUTH if status in (401, 403) else ExecutionError.MARKETPLACE_4XX)
    assert not ExecutionError.is_ambiguous_error(error.code)


# Audited concrete operation sites at af52. True=read; False=mutation/unknown.
# GET start/pause is a WRITE; POST warehouse report submits a task (unknown replay).
SITES = [
    ("wb_client.py", "list_unanswered_feedbacks", "request", True),
    ("wb_client.py", "publish_feedback_answer", "request", False),
    ("wb_client.py", "set_price", "request", False),
    ("wb_client.py", "set_discount", "request", False),
    ("wb_client.py", "set_auto_promotion", "request", False),
    ("wb_client.py", "set_bid", "request", False),
    ("wb_client.py", "set_campaign_state", "request", False),
    ("wb_client.py", "list_adverts_for_nm", "request", True),
    ("wb_client.py", "update_card", "request", False),
    ("wb_client.py", "list_cards", "request", True),
    ("wb_client.py", "list_prices", "request", True),
    ("wb_client.py", "list_promotions", "request", True),
    ("wb_client.py", "promotion_details", "request", True),
    ("wb_client.py", "promotion_nomenclatures", "request", True),
    ("wb_client.py", "get_sales", "request", True),
    ("wb_client.py", "list_orders", "request", True),
    ("wb_client.py", "create_warehouse_remains_report", "request", False),
    ("wb_client.py", "warehouse_remains_status", "request", True),
    ("wb_client.py", "download_warehouse_remains", "request", True),
    ("wb_client.py", "finance_sales_report_detailed", "request", True),
    ("ozon_client.py", "set_price", "request", False),
    ("ozon_client.py", "set_auto_promotion", "request", False),
    ("ozon_client.py", "set_campaign_state", "request", False),
    ("ozon_client.py", "list_campaigns_for_sku", "request", True),
    ("ozon_client.py", "list_actions", "request", True),
    ("ozon_client.py", "list_reviews", "request", True),
    ("ozon_client.py", "publish_feedback_answer", "request", False),
    *[("ozon_client.py", name, "_seller_get", True) for name in (
        "list_products", "product_info_list", "product_prices", "product_stocks",
        "posting_fbo_list", "posting_fbs_list", "finance_transactions", "returns_list",
        "action_products", "action_candidates")],
    *[("yandex_client.py", name, "_get", True) for name in (
        "resolve_business_id", "list_campaigns", "campaign_prices", "campaign_orders", "campaign_returns")],
    *[("yandex_client.py", name, "_post", True) for name in (
        "list_reviews", "list_comments", "offer_mappings", "campaign_stocks", "list_promos", "list_promo_offers")],
    ("yandex_client.py", "publish_feedback_answer", "_post", False),
]
WRAPPERS = [("ozon_client.py", "_seller_get"), ("yandex_client.py", "_post"), ("yandex_client.py", "_get")]
CLIENT_ROOT = Path(__file__).resolve().parents[1] / "services/marketplace"


def function_tree(filename, function):
    tree = ast.parse((CLIENT_ROOT / filename).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == function)


@pytest.mark.parametrize("filename,function,callee,safe", SITES, ids=[s[0]+":"+s[1] for s in SITES])
def test_every_operation_has_explicit_semantic_marker(filename, function, callee, safe):
    calls = [n for n in ast.walk(function_tree(filename, function)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == callee]
    assert len(calls) == 1
    marker = next((k.value for k in calls[0].keywords if k.arg == "retry_safe"), None)
    assert isinstance(marker, ast.Constant) and marker.value is safe


@pytest.mark.parametrize("filename,function", WRAPPERS)
def test_wrappers_forward_explicit_policy_with_safe_default_false(filename, function):
    tree = function_tree(filename, function)
    defaults = dict(zip((a.arg for a in tree.args.kwonlyargs), tree.args.kw_defaults))
    assert isinstance(defaults.get("retry_safe"), ast.Constant)
    assert defaults["retry_safe"].value is False
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "request"]
    assert len(calls) == 1
    marker = next((k.value for k in calls[0].keywords if k.arg == "retry_safe"), None)
    assert isinstance(marker, ast.Name) and marker.id == "retry_safe"


def test_inventory_is_exactly_49_operations_36_reads_13_unsafe():
    assert len(SITES) == 49
    assert sum(s[3] is True for s in SITES) == 36
    assert sum(s[3] is False for s in SITES) == 13
    actual = set()
    for filename in {s[0] for s in SITES}:
        tree = ast.parse((CLIENT_ROOT / filename).read_text(encoding="utf-8"))
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)):
            if (filename, function.name) in WRAPPERS:
                continue
            for n in ast.walk(function):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {
                    "request", "_post", "_get", "_seller_get"
                }:
                    actual.add((filename, function.name, n.func.attr))
    assert actual == {(s[0], s[1], s[2]) for s in SITES}


def test_base_request_policy_is_keyword_only_false_by_default():
    from services.marketplace.base_client import BaseMarketplaceClient
    parameter = inspect.signature(BaseMarketplaceClient.request).parameters["retry_safe"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is False


@pytest.mark.parametrize("filename,wrapper", WRAPPERS)
@pytest.mark.parametrize("policy,expected", [({}, False), ({"retry_safe": True}, True), ({"retry_safe": False}, False)])
def test_real_wrappers_forward_policy_dynamically(monkeypatch, filename, wrapper, policy, expected):
    from services.marketplace.ozon_client import OzonClient
    from services.marketplace.yandex_client import YandexClient
    client = OzonClient() if filename == "ozon_client.py" else YandexClient()
    transport = client._seller if filename == "ozon_client.py" else client._api
    seen = []

    async def sink(method, path, *, token, auth_header, retry_safe, json=None, params=None, extra_headers=None):
        assert retry_safe is expected
        assert method == ("GET" if wrapper == "_get" else "POST")
        assert path == "/audit/read"
        seen.append(retry_safe)
        return {"ok": True}

    monkeypatch.setattr(transport, "request", sink)
    kwargs = {"token": "SYNTHETIC-NOT-A-REAL-TOKEN", **policy}
    if wrapper == "_seller_get":
        kwargs.update(client_id="synthetic", body={"synthetic": True})
    else:
        kwargs["params"] = {"synthetic": "1"}
        if wrapper == "_post":
            kwargs["json"] = {"synthetic": True}
    assert asyncio.run(getattr(client, wrapper)("/audit/read", **kwargs)) == {"ok": True}
    assert seen == [expected]
