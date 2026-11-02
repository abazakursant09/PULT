"""Invocation-local throttle deadlines: actual transport, synthetic HTTP and clocks."""
import asyncio
from datetime import datetime, timezone
from email.utils import formatdate
from types import SimpleNamespace

import httpx
import pytest

from test_marketplace_retry_policy import no_external_io  # noqa: F401


class Clock:
    def __init__(self, *, early=False, cancel=False, oversleep=0):
        self.elapsed = 0.0
        self.sleeps = []
        self.early = early
        self.cancel = cancel
        self.oversleep = oversleep
        self.epoch = 1793610000.0

    async def sleep(self, seconds):
        assert 0 <= seconds <= 1.5, "no huge or unbounded timer"
        self.sleeps.append(seconds)
        assert len(self.sleeps) <= 10, "early wake must not spin indefinitely"
        if self.cancel:
            raise asyncio.CancelledError
        self.elapsed += seconds / 2 if self.early and len(self.sleeps) == 1 else seconds
        self.elapsed += self.oversleep

    def monotonic(self):
        return self.elapsed

    def time(self):
        return self.epoch + self.elapsed


def transport(monkeypatch, sequence, *, retries=2, policy=False, clock=None):
    import services.marketplace.base_client as bc

    clock = clock or Clock()
    sent = []
    clock.sent = sent

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout is not None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, headers, json, params):
            assert method == "POST" and url == "https://synthetic.invalid/operation"
            assert headers == {"Authorization": "SYNTHETIC", "Content-Type": "application/json"}
            assert json == {"synthetic": True} and params is None
            assert len(sent) < len(sequence), "unexpected additional HTTP attempt"
            sent.append(clock.elapsed)
            status, response_headers = sequence[len(sent) - 1]
            if status == "timeout":
                raise httpx.ReadTimeout("synthetic")
            return httpx.Response(status, headers=response_headers, json={"ok": status == 200})

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.fromtimestamp(clock.time(), timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    # Replace this module's references, never mutate shared asyncio/time modules.
    monkeypatch.setattr(bc, "asyncio", SimpleNamespace(sleep=clock.sleep, CancelledError=asyncio.CancelledError))
    monkeypatch.setattr(bc, "time", SimpleNamespace(monotonic=clock.monotonic, time=clock.time), raising=False)
    monkeypatch.setattr(bc, "monotonic", clock.monotonic, raising=False)
    monkeypatch.setattr(bc, "datetime", FakeDateTime, raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    client = bc.BaseMarketplaceClient("https://synthetic.invalid", max_retries=retries)
    result = error = None
    try:
        result = asyncio.run(client.request("POST", "/operation", token="SYNTHETIC",
                                          json={"synthetic": True}, retry_safe=policy))
    except bc.ExecutionError as exc:
        error = exc
    return sent, clock, result, error


@pytest.mark.parametrize("header", ["x-RaTeLiMiT-ReTrY", "rEtRy-AfTeR"], ids=["WB", "Ozon"])
def test_server_two_seconds_cannot_retry_early(monkeypatch, header):
    sent, clock, result, error = transport(monkeypatch, [(429, {header: "2"}), (200, {})])
    assert sent == [0.0], "2s exceeds 1.5s invocation budget: no early retry allowed"
    assert clock.sleeps == [] and result is None
    assert error.code == "RATE_LIMIT" and error.retryable is True


def assert_refused(outcome, *, attempts=1, sleeps=None):
    sent, clock, result, error = outcome
    assert len(sent) == attempts
    assert clock.sleeps == ([] if sleeps is None else sleeps)
    assert result is None and error.code == "RATE_LIMIT" and error.retryable is True
    assert not error.is_ambiguous_error(error.code)
    return error


@pytest.mark.parametrize("status", [420, 429])
@pytest.mark.parametrize("header", ["Retry-After", "X-Ratelimit-Retry"])
@pytest.mark.parametrize("value,delay", [("0", .5), ("  0\t", .5), ("1", 1.0), (" 1 ", 1.0), ("01", 1.0)])
def test_integer_hints_respect_minimum_and_fallback(monkeypatch, status, header, value, delay):
    sent, clock, result, error = transport(monkeypatch, [(status, {header: value}), (200, {})])
    assert sent == [0.0, delay] and clock.sleeps == [delay]
    assert result == {"ok": True} and error is None


@pytest.mark.parametrize("header", ["Retry-After", "X-Ratelimit-Retry"])
@pytest.mark.parametrize("value", ["", " ", "-1", "+1", "1.0", ".5", "1e0", "NaN", "Inf",
    "1, 2", "tomorrow", "1s", "2", "9999999999999999999999999999999999999999", "9" * 5000])
def test_invalid_or_overbudget_hint_fails_closed(monkeypatch, header, value):
    error = assert_refused(transport(monkeypatch, [(429, {header: value}), (200, {})]))
    assert "synthetic.invalid" not in str(error) and "SYNTHETIC" not in str(error)
    if len(value) > 10:
        assert value not in str(error)


@pytest.mark.parametrize("headers,delay", [
    ({"Retry-After": "1", "X-Ratelimit-Retry": "0"}, 1.0),
    ({"Retry-After": "0", "X-Ratelimit-Retry": "1"}, 1.0),
    ({"Retry-After": "0", "X-Ratelimit-Retry": "0"}, .5),
])
def test_two_valid_lower_bounds_choose_maximum(monkeypatch, headers, delay):
    sent, clock, result, error = transport(monkeypatch, [(429, headers), (200, {})])
    assert sent == [0.0, delay] and clock.sleeps == [delay]
    assert error is None and result == {"ok": True}


@pytest.mark.parametrize("headers", [
    [("Retry-After", "0"), ("retry-after", "1")],
    [("X-Ratelimit-Retry", "0"), ("x-ratelimit-retry", "1")],
    {"Retry-After": "2", "X-Ratelimit-Retry": "0"},
    {"Retry-After": "0", "X-Ratelimit-Retry": "2"},
    {"Retry-After": "1", "X-Ratelimit-Retry": "bad"},
    {"Retry-After": "bad", "X-Ratelimit-Retry": "1"},
])
def test_duplicate_invalid_or_conflicting_bounds_never_choose_shorter(monkeypatch, headers):
    assert_refused(transport(monkeypatch, [(429, headers), (200, {})]))


@pytest.mark.parametrize("offset,delay", [(-100, .5), (0, .5), (1, 1.0)])
@pytest.mark.parametrize("form", ["imf", "asctime"])
def test_standard_http_dates(monkeypatch, offset, delay, form):
    clock = Clock()
    moment = datetime.fromtimestamp(clock.epoch + offset, timezone.utc)
    value = {"imf": formatdate(clock.epoch + offset, usegmt=True),
             "asctime": moment.strftime("%a %b %d %H:%M:%S %Y")}[form]
    sent, clock, result, error = transport(monkeypatch, [(429, {"Retry-After": value}), (200, {})], clock=clock)
    assert sent == [0.0, delay] and clock.sleeps == [delay]
    assert error is None and result == {"ok": True}


@pytest.mark.parametrize("offset", [-100, 0, 1])
def test_rfc850_near_current_year_is_conservatively_unsupported(monkeypatch, offset):
    clock = Clock()
    moment = datetime.fromtimestamp(clock.epoch + offset, timezone.utc)
    value = moment.strftime("%A, %d-%b-%y %H:%M:%S GMT")
    assert_refused(transport(monkeypatch, [(429, {"Retry-After": value}), (200, {})], clock=clock))


def test_future_date_fractional_remaining_rounds_up(monkeypatch):
    clock = Clock()
    deadline = formatdate(clock.epoch + 1, usegmt=True)
    clock.epoch += .25
    sent, clock, result, error = transport(monkeypatch, [(429, {"Retry-After": deadline}), (200, {})], clock=clock)
    assert sent == [0.0, 1.0] and clock.sleeps == [1.0]
    assert result == {"ok": True} and error is None


def test_rfc850_century_pivot_cannot_authorize_early_retry(monkeypatch):
    # From 2026, year 70 denotes 2070 under HTTP's 50-year rule, not 1970.
    # Python's generic email-date parser pivots differently. Refuse this format.
    clock = Clock()
    assert datetime.fromtimestamp(clock.epoch, timezone.utc).year == 2026
    assert_refused(transport(monkeypatch, [(429, {"Retry-After": "Sunday, 02-Nov-70 09:00:00 GMT"}),
        (200, {})], clock=clock))


@pytest.mark.parametrize("value", ["Mon, 99 Nov 2026 12:00:00 GMT", "Mon, 02 Nov 2026 25:00:00 GMT",
    "Mon, 02 Nov 2026 12:00:00", "Mon, 02 Nov 2026 12:00:00 +0300"])
def test_non_http_date_is_not_guessed(monkeypatch, value):
    assert_refused(transport(monkeypatch, [(429, {"Retry-After": value}), (200, {})]))


def test_wb_extension_does_not_accept_http_date(monkeypatch):
    assert_refused(transport(monkeypatch, [(429, {"X-Ratelimit-Retry": formatdate(Clock().epoch, usegmt=True)}), (200, {})]))


def test_two_one_second_hints_exceed_cumulative_budget(monkeypatch):
    assert_refused(transport(monkeypatch, [(429, {"Retry-After": "1"}),
        (429, {"Retry-After": "1"}), (200, {})]), attempts=2, sleeps=[1.0])


def test_exact_cumulative_budget_boundary_fits(monkeypatch):
    sent, clock, result, error = transport(monkeypatch, [(429, {"Retry-After": "0"}),
        (429, {"Retry-After": "1"}), (200, {})])
    assert sent == [0.0, .5, 1.5] and clock.sleeps == [.5, 1.0]
    assert result == {"ok": True} and error is None


def test_scheduler_oversleep_does_not_remove_no_header_retry(monkeypatch):
    sent, clock, result, error = transport(monkeypatch, [(429, {}), (429, {}), (200, {})], clock=Clock(oversleep=10))
    assert sent == [0.0, 10.5, 21.5] and clock.sleeps == [.5, 1.0]
    assert result == {"ok": True} and error is None


@pytest.mark.parametrize("headers", [{"Retry-After": "1"}, {"X-Ratelimit-Retry": "2"}])
def test_zero_retries_never_sleeps_for_header(monkeypatch, headers):
    assert_refused(transport(monkeypatch, [(429, headers), (200, {})], retries=0))


def test_one_retry_budget_is_only_half_second(monkeypatch):
    assert_refused(transport(monkeypatch, [(429, {"Retry-After": "1"}), (200, {})], retries=1))


def test_reset_header_is_not_a_retry_deadline(monkeypatch):
    sent, clock, result, error = transport(monkeypatch, [(429, {"X-Ratelimit-Reset": "99999"}), (200, {})])
    assert sent == [0.0, .5] and clock.sleeps == [.5]
    assert result == {"ok": True} and error is None


def test_early_sleeper_cannot_dispatch_before_minimum(monkeypatch):
    sent, clock, result, error = transport(monkeypatch, [(429, {"Retry-After": "1"}), (200, {})], clock=Clock(early=True))
    assert clock.sleeps[0] == 1.0
    assert all(timestamp >= 1.0 for timestamp in sent[1:])
    if len(sent) == 1:
        assert result is None and error.code == "RATE_LIMIT"
    else:
        assert result == {"ok": True} and error is None


def test_sleep_cancellation_propagates_without_retry(monkeypatch):
    clock = Clock(cancel=True)
    with pytest.raises(asyncio.CancelledError):
        transport(monkeypatch, [(429, {"Retry-After": "1"}), (200, {})], clock=clock)
    assert clock.sleeps == [1.0] and clock.elapsed == 0 and clock.sent == [0.0]


@pytest.mark.parametrize("retries,expected", [(0, []), (1, [.5]), (2, [.5, 1.0])])
@pytest.mark.parametrize("status", [420, 429])
def test_no_headers_preserve_supported_backoff(monkeypatch, retries, expected, status):
    sent, clock, result, error = transport(monkeypatch, [(status, {})] * (retries + 1), retries=retries)
    assert len(sent) == retries + 1 and clock.sleeps == expected
    assert result is None and error.code == "RATE_LIMIT"


@pytest.mark.parametrize("retries", [True, False, -1, 3, 2.0, "2", None, float("inf"), float("nan"), 10 ** 100])
def test_unsupported_retry_count_refuses_throttle_without_constructor_validation(monkeypatch, retries):
    assert_refused(transport(monkeypatch, [(429, {}), (200, {})], retries=retries))


@pytest.mark.parametrize("policy", [False, None, 1, "true"])
def test_unsafe_503_header_never_authorizes_replay(monkeypatch, policy):
    sent, clock, result, error = transport(monkeypatch, [(503, {"Retry-After": "1"}), (200, {})], policy=policy)
    assert sent == [0.0] and clock.sleeps == [] and result is None
    assert error.code == "MARKETPLACE_5XX" and error.is_ambiguous_error(error.code)


@pytest.mark.parametrize("fault", ["timeout", 500, 502, 503, 504])
@pytest.mark.parametrize("status", [420, 429])
def test_throttle_then_ambiguous_write_stops_immediately(monkeypatch, status, fault):
    sent, clock, result, error = transport(monkeypatch, [(status, {"Retry-After": "1"}),
        (fault, {"Retry-After": "0"}), (200, {})])
    assert sent == [0.0, 1.0] and clock.sleeps == [1.0] and result is None
    assert error.code == ("TIMEOUT" if fault == "timeout" else "MARKETPLACE_5XX")
    assert error.is_ambiguous_error(error.code)


@pytest.mark.parametrize("fault", ["timeout", 503])
def test_safe_non_throttle_backoff_does_not_consume_throttle_ledger(monkeypatch, fault):
    sent, clock, result, error = transport(monkeypatch, [(fault, {}), (429, {"Retry-After": "1"}), (200, {})], policy=True)
    assert sent == [0.0, .5, 1.5] and clock.sleeps == [.5, 1.0]
    assert error is None and result == {"ok": True}


@pytest.mark.parametrize("status,code", [(401, "AUTH"), (403, "AUTH"), (400, "MARKETPLACE_4XX")])
def test_other_http_errors_ignore_wait_headers(monkeypatch, status, code):
    sent, clock, result, error = transport(monkeypatch, [(status, {"Retry-After": "1"})])
    assert sent == [0.0] and clock.sleeps == [] and result is None
    assert error.code == code


def test_api_sync_rate_limit_keeps_last_committed_cursor_and_pauses():
    from test_wb_api_ingest import _new_db, _seed, _state
    from services.marketplace.errors import ExecutionError
    import tasks.api_sync as api_sync

    async def scenario():
        db = await _new_db()
        engine = db.bind
        try:
            uid, account, store, connection = await _seed(db)
            state = await _state(db, connection, store, "prices")
            state._owner_user_id = uid
            calls = []

            async def fetch(session, current, token, client_id):
                assert session is db and current is state and token == "SYNTHETIC"
                calls.append(current.cursor)
                if len(calls) == 1:
                    current.cursor = "committed-page-one"
                    return {"done": False}
                current.cursor = "uncommitted-failed-page"
                raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")

            now = datetime(2026, 11, 2, 9)
            await api_sync._sync_state(db, state, SimpleNamespace(fetch_and_persist_page=fetch),
                                       "SYNTHETIC", None, now)
            await db.refresh(state)
            assert calls == [None, "committed-page-one"]
            assert state.cursor == "committed-page-one" and state.status == "paused"
            assert state.last_safe_error_code == "RATE_LIMIT" and state.fail_count == 1
            assert state.next_run_at > now
        finally:
            await db.close()
            await engine.dispose()

    asyncio.run(scenario())
