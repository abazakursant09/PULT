"""Bounded buffered selection is not business import/unanswered acceptance."""
import asyncio

import httpx
import pytest

from services.marketplace.ozon_client import OzonClient
from services.marketplace.ozon_review_read import OzonReviewReadIncomplete, ReviewReadLimits
from test_ozon_review_v2 import detail, wire


def page(ids=(), more=False, cursor=""):
    return {"reviews": [{"id": value} for value in ids], "has_next": more, "last_id": cursor}


def test_empty_continuation_uses_response_cursor_and_deduplicates_ids(monkeypatch):
    calls = wire(monkeypatch, [page(["one"], True, "cursor-A"), page([], True, "cursor-B"),
        page(["one", "two"]), detail("one", status="PROCESSED"), detail("two", status="VIEWED")])
    result = asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client"))
    assert [info.id for info in result] == ["one", "two"]
    assert [info.status for info in result] == ["PROCESSED", "VIEWED"]
    assert len(calls) == 5
    assert "last_id" not in calls[0][2]["json"]
    assert calls[1][2]["json"]["last_id"] == "cursor-A"
    assert calls[2][2]["json"]["last_id"] == "cursor-B"
    assert all("filters" not in call[2]["json"] for call in calls)


@pytest.mark.parametrize("pages", [
    [page([], True, "A"), page([], True, "A")],
    [page([], True, "A"), page([], True, "B"), page([], True, "A")]])
def test_cursor_cycles_fail_without_detail_dispatch(monkeypatch, pages):
    calls = wire(monkeypatch, pages)
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client"))
    assert caught.value.reason == "cycle" and len(calls) == len(pages)
    assert all(call[1].endswith("/v2/review/list") for call in calls)


def test_terminal_cursor_may_repeat_because_has_next_is_false(monkeypatch):
    wire(monkeypatch, [page([], True, "A"), page([], False, "A")])
    assert asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client")) == ()


@pytest.mark.parametrize("limits,responses,reason,expected_calls", [
    (ReviewReadLimits(max_pages=1), [page(["one"], True, "next")], "page_limit", 1),
    (ReviewReadLimits(max_details=1), [page(["one", "two"])], "detail_limit", 1),
    (ReviewReadLimits(max_attempts=1), [page(["one"])], "attempt_limit", 1),
    (ReviewReadLimits(max_attempts=2), [page(["one", "two"]), detail("one")], "attempt_limit", 2),
    (ReviewReadLimits(max_decoded_bytes=1), [page()], "byte_limit", 1),
])
def test_budgets_stop_before_excess_dispatch(monkeypatch, limits, responses, reason, expected_calls):
    calls = wire(monkeypatch, responses)
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().read_review_selection_v2(
            token="synthetic", client_id="client", limits=limits))
    assert caught.value.reason == reason and len(calls) == expected_calls
    assert caught.value.retryable is False


def test_decoded_byte_budget_is_cumulative_across_list_and_details(monkeypatch):
    import json
    first, second = page(["one"]), detail("one")
    size = lambda data: len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    bound = size(first) + size(second) - 1
    assert size(first) < bound and size(second) < bound
    calls = wire(monkeypatch, [first, second])
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client",
            limits=ReviewReadLimits(max_decoded_bytes=bound)))
    assert caught.value.reason == "byte_limit" and len(calls) == 2


def test_ignored_fields_also_consume_byte_budget(monkeypatch):
    payload = page()
    payload["unknown"] = "x" * 1000
    wire(monkeypatch, [payload])
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client",
            limits=ReviewReadLimits(max_decoded_bytes=500)))
    assert caught.value.reason == "byte_limit"


@pytest.mark.parametrize("failure", [httpx.Response(503), detail("wrong-id"),
                                    {"id": "two", "sku": "701"}])
def test_late_detail_failure_is_not_partial_success(monkeypatch, failure):
    from services.marketplace.errors import ExecutionError
    calls = wire(monkeypatch, [page(["one", "two"]), detail("one"), failure])
    with pytest.raises(ExecutionError):
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client"))
    assert len(calls) == 3


def test_late_page_http_error_does_not_start_details(monkeypatch):
    from services.marketplace.errors import ExecutionError
    calls = wire(monkeypatch, [page(["one"], True, "cursor"), httpx.Response(503)])
    with pytest.raises(ExecutionError):
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client"))
    assert len(calls) == 2


def test_sku_assertion_checks_every_detail_without_filtering_or_relabelling(monkeypatch):
    wire(monkeypatch, [page(["one", "two"]), detail("one", sku=701), detail("two", sku=702)])
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client", expected_sku=701))
    assert caught.value.reason == "identity"


def test_absolute_deadline_covers_inflight_detail(monkeypatch):
    cancelled = []

    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    calls = wire(monkeypatch, [page(["one"]), slow])
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client",
            limits=ReviewReadLimits(deadline_seconds=0.03)))
    assert caught.value.reason == "deadline" and len(calls) == 2 and cancelled == [True]


def test_expiry_after_synchronous_decode_is_not_success(monkeypatch):
    import services.marketplace.ozon_review_read as module
    clock = [0.0]
    original = module._page

    def late_decode(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = 2.0
        return result

    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "_page", late_decode)
    wire(monkeypatch, [page()])
    with pytest.raises(OzonReviewReadIncomplete) as caught:
        asyncio.run(OzonClient().read_review_selection_v2(token="synthetic", client_id="client",
            limits=ReviewReadLimits(deadline_seconds=1)))
    assert caught.value.reason == "deadline"


@pytest.mark.parametrize("options", [{"max_pages": 0}, {"max_details": True},
    {"max_attempts": -1}, {"max_decoded_bytes": 0}, {"deadline_seconds": False},
    {"deadline_seconds": float("nan")}, {"deadline_seconds": float("inf")},
    {"deadline_seconds": 10**400}])
def test_invalid_limits_are_validation_errors(options):
    from services.marketplace.errors import ExecutionError
    with pytest.raises(ExecutionError) as caught:
        ReviewReadLimits(**options)
    assert caught.value.code == ExecutionError.VALIDATION


def test_same_client_does_not_share_budget_or_cursor_between_cabinets(monkeypatch):
    calls = wire(monkeypatch, [page(["one"]), detail("one"), page(["two"]), detail("two")])
    client = OzonClient()

    async def read_both():
        a = await client.read_review_selection_v2(token="key-A", client_id="cabinet-A")
        b = await client.read_review_selection_v2(token="key-B", client_id="cabinet-B")
        return a, b

    a, b = asyncio.run(read_both())
    assert a[0].id == "one" and b[0].id == "two"
    assert [call[2]["headers"]["Client-Id"] for call in calls] == ["cabinet-A"] * 2 + ["cabinet-B"] * 2
    assert "last_id" not in calls[0][2]["json"] and "last_id" not in calls[2][2]["json"]


def test_existing_provider_never_calls_additive_v2_reader(monkeypatch):
    from services.marketplace.ozon_client import ozon_client
    from services.marketplace.reviews.ozon import OzonReviewProvider
    legacy_calls = []

    async def forbidden(**kwargs):
        pytest.fail("transport-only v2 must not activate the business provider")

    async def legacy(**kwargs):
        legacy_calls.append(kwargs)
        return []

    monkeypatch.setattr(ozon_client, "list_reviews", legacy)
    for name in ("review_list_page_v2", "review_info_v2", "read_review_selection_v2"):
        monkeypatch.setattr(ozon_client, name, forbidden)
    assert asyncio.run(OzonReviewProvider().fetch_reviews("CID:KEY", "legacy-product")) == []
    assert legacy_calls == [{"token": "KEY", "client_id": "CID", "product_ref": "legacy-product"}]


def test_caller_cancellation_is_not_converted_to_empty_success(monkeypatch):
    started = asyncio.Event()
    cancelled = []

    async def pending(*args):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.append(True)

    calls = wire(monkeypatch, [page(["one"]), pending])

    async def cancel_read():
        task = asyncio.create_task(OzonClient().read_review_selection_v2(token="synthetic", client_id="client"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_read())
    assert len(calls) == 2 and cancelled == [True]


def test_concurrent_cabinets_have_independent_attempt_budgets_and_cursors(monkeypatch):
    client = OzonClient()

    async def interleaved():
        a_started, b_started = asyncio.Event(), asyncio.Event()

        async def respond(method, url, kwargs):
            cabinet = kwargs["headers"]["Client-Id"]
            assert kwargs["headers"]["Api-Key"] == "key-" + cabinet
            if cabinet == "A":
                a_started.set()
                await b_started.wait()
                return httpx.Response(200, json=page(["a-review"], True, "cursor-A"))
            await a_started.wait()
            b_started.set()
            if url.endswith("/v2/review/info"):
                assert kwargs["json"] == {"review_id": "b-review"}
                return httpx.Response(200, json=detail("b-review", sku=702))
            if "last_id" not in kwargs["json"]:
                return httpx.Response(200, json=page([], True, "cursor-B"))
            assert kwargs["json"]["last_id"] == "cursor-B"
            return httpx.Response(200, json=page(["b-review"]))

        calls = wire(monkeypatch, [respond] * 4)
        a, b = await asyncio.gather(
            client.read_review_selection_v2(token="key-A", client_id="A",
                limits=ReviewReadLimits(max_attempts=1)),
            client.read_review_selection_v2(token="key-B", client_id="B", expected_sku=702,
                limits=ReviewReadLimits(max_attempts=3)),
            return_exceptions=True)
        assert isinstance(a, OzonReviewReadIncomplete) and a.reason == "attempt_limit"
        assert len(b) == 1 and b[0].id == "b-review" and b[0].sku == 702
        assert len(calls) == 4

    asyncio.run(interleaved())
