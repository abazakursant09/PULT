"""PULT-LAUNCH-2.5E-3B — scheduler wiring of the observation-retention sweep (feature OFF).

Drives the scheduler's tick/task machinery directly (the sweep's own DB behaviour is covered by
test_observation_retention_sweep.py). `_retention_run` is monkeypatched to a controllable coroutine so
these tests exercise: feature-OFF (no task), the 60-minute interval, non-blocking single-task spawning,
the done-callback lifecycle (result/exception consumed, reference cleared, no unretrieved-exception
warning), shutdown cancel+await, the consecutive-failure counter + single Sentry alert, and safe logs.
"""
from __future__ import annotations

import asyncio
import logging
import time

import tasks.scheduler as sched
from services.marketplace.retention.observation_sweep import RetentionResult

_LOOP = asyncio.new_event_loop()
_SECRETS = ("SECRET-ACCT", "SECRET-STORE", "SECRET-SKU", "SECRET-EXT", "SECRET-PROMO")


def _run(coro):
    return _LOOP.run_until_complete(coro)


def _enable_logs(monkeypatch):
    # Another test's alembic fileConfig can disable app loggers mid-suite; re-enable + propagate so
    # caplog (root handler) actually captures scheduler records. Passes in isolation without this;
    # only the full suite exposes the disabled logger.
    monkeypatch.setattr(sched.logger, "disabled", False)
    monkeypatch.setattr(sched.logger, "propagate", True)


def _reset(monkeypatch, *, enabled=True, dry=True, elapsed=True):
    monkeypatch.setattr(sched.settings, "observation_retention_enabled", enabled)
    monkeypatch.setattr(sched.settings, "observation_retention_dry_run", dry)
    monkeypatch.setattr(sched, "_retention_task", None)
    monkeypatch.setattr(sched, "_retention_consecutive_failures", 0)
    # elapsed=True -> the hour has passed (tick may spawn); False -> just started (must wait)
    monkeypatch.setattr(sched, "_last_retention_at",
                        time.monotonic() - (sched._RETENTION_INTERVAL_SECONDS + 1 if elapsed else 0))


def _result(**over):
    base = dict(enabled=True, lock_acquired=True, dry_run=False)
    base.update(over)
    return RetentionResult(**base)


def _fake_returning(res):
    async def _r():
        return res
    return _r


def _drive(monkeypatch, res_or_exc, *, dry=False):
    """Run one tick, await the spawned task + let the done-callback fire; return the (cleared) task."""
    if isinstance(res_or_exc, BaseException):
        async def _r():
            raise res_or_exc
    else:
        async def _r():
            return res_or_exc
    monkeypatch.setattr(sched, "_retention_run", _r)

    async def _go():
        sched._observation_retention_tick()
        t = sched._retention_task
        if t is not None:
            try:
                await t
            except BaseException:
                pass
            await asyncio.sleep(0)     # let add_done_callback fire
        return t
    return _run(_go())


# ── feature OFF ──────────────────────────────────────────────────────────────────
def test_feature_off_spawns_no_task(monkeypatch):
    _reset(monkeypatch, enabled=False)
    async def _go():
        sched._observation_retention_tick()
        return sched._retention_task
    assert _run(_go()) is None


# ── interval ─────────────────────────────────────────────────────────────────────
def test_within_hour_does_not_spawn(monkeypatch):
    _reset(monkeypatch, elapsed=False)                # just "started"
    async def _go():
        sched._observation_retention_tick()
        return sched._retention_task
    assert _run(_go()) is None                         # first run waits a full hour


def test_after_hour_spawns(monkeypatch):
    _reset(monkeypatch)
    t = _drive(monkeypatch, _result(dry_run=True, price_candidates=3))
    assert t is not None and sched._retention_task is None   # spawned, then reference cleared


def test_first_run_clock_set_in_run_scheduler_not_import():
    src = (sched.__file__ and open(sched.__file__, encoding="utf-8").read())
    assert "_last_retention_at = time.monotonic()" in src   # set at run_scheduler start
    # the module-level default must be None (clock starts only when run_scheduler runs)
    import importlib
    m = importlib.import_module("tasks.scheduler")
    assert m._RETENTION_INTERVAL_SECONDS == 3600


# ── dry-run vs real ────────────────────────────────────────────────────────────
def test_dry_run_default_counts_only(monkeypatch, caplog):
    _reset(monkeypatch, dry=True)
    _enable_logs(monkeypatch)
    with caplog.at_level(logging.INFO, logger=sched.logger.name):
        _drive(monkeypatch, _result(dry_run=True, price_candidates=5, promotion_candidates=2))
    assert "dry-run" in caplog.text and "candidates 5 price" in caplog.text
    assert "removed" not in caplog.text.split("dry-run")[-1]


def test_real_mode_logs_removed(monkeypatch, caplog):
    _reset(monkeypatch, dry=False)
    _enable_logs(monkeypatch)
    with caplog.at_level(logging.INFO, logger=sched.logger.name):
        _drive(monkeypatch, _result(dry_run=False, price_removed=7, promotion_removed=1, batches=3))
    assert "removed 7 price, 1 promotion" in caplog.text


# ── non-blocking / no second task ────────────────────────────────────────────────
def test_running_task_blocks_second_and_tick_is_nonblocking(monkeypatch):
    _reset(monkeypatch)
    ev = asyncio.Event()

    async def _hang():
        await ev.wait()
        return _result(dry_run=True)
    monkeypatch.setattr(sched, "_retention_run", _hang)

    async def _go():
        t0 = time.monotonic()
        sched._observation_retention_tick()            # spawns task 1
        first = sched._retention_task
        sched._observation_retention_tick()            # task 1 still running -> no second task
        second = sched._retention_task
        tick_ms = (time.monotonic() - t0) * 1000
        ev.set()
        await first
        await asyncio.sleep(0)
        return first, second, tick_ms
    first, second, tick_ms = _run(_go())
    assert first is second                             # same single task, no second created
    assert tick_ms < 100                               # tick returned immediately (non-blocking)
    assert sched._retention_task is None               # cleared after completion


# ── lifecycle: exception consumed, no unretrieved warning ────────────────────────
def test_task_exception_is_consumed_safely(monkeypatch, caplog):
    _reset(monkeypatch)
    _enable_logs(monkeypatch)
    boom = RuntimeError("boom " + " ".join(_SECRETS) + " [SQL: DELETE ...] [parameters: {'acc': 'x'}]")
    with caplog.at_level(logging.WARNING, logger=sched.logger.name):
        _drive(monkeypatch, boom)
    assert sched._retention_task is None
    assert "run error" in caplog.text
    for s in _SECRETS:
        assert s not in caplog.text
    assert "Traceback" not in caplog.text and "[SQL:" not in caplog.text and "parameters" not in caplog.text
    assert sched._retention_consecutive_failures == 1   # unhandled exc = a failed run


# ── shutdown cancels + awaits the running task ───────────────────────────────────
def test_shutdown_cancels_and_awaits(monkeypatch):
    _reset(monkeypatch)
    ev = asyncio.Event()

    async def _hang():
        try:
            await ev.wait()
        except asyncio.CancelledError:
            raise
        return _result()
    monkeypatch.setattr(sched, "_retention_run", _hang)

    async def _go():
        sched._observation_retention_tick()
        t = sched._retention_task
        await sched._shutdown_retention()
        await asyncio.sleep(0)
        return t
    t = _run(_go())
    assert t.cancelled()                               # running task was cancelled + awaited
    assert sched._retention_consecutive_failures == 0  # shutdown cancellation is NOT a failed run


# ── consecutive-failure counter + single Sentry alert ────────────────────────────
def test_failure_counter_and_single_alert(monkeypatch):
    _reset(monkeypatch)
    alerts = []
    monkeypatch.setattr(sched, "_retention_sentry_alert", lambda n: alerts.append(n))

    def _one(res_or_exc):
        # allow re-spawn each drive (reference cleared + clock reset)
        monkeypatch.setattr(sched, "_last_retention_at",
                            time.monotonic() - (sched._RETENTION_INTERVAL_SECONDS + 1))
        _drive(monkeypatch, res_or_exc)

    _one(_result(failed_batches=1)); assert sched._retention_consecutive_failures == 1 and alerts == []
    _one(_result(timed_out=True));   assert sched._retention_consecutive_failures == 2 and alerts == []
    _one(_result(failed_batches=2)); assert sched._retention_consecutive_failures == 3 and alerts == [3]
    _one(_result(failed_batches=1)); assert sched._retention_consecutive_failures == 4 and alerts == [3]  # no 2nd alert
    _one(_result(price_removed=1));  assert sched._retention_consecutive_failures == 0                    # success resets
    # advisory-lock busy / disabled must NOT change the counter and must NOT alert
    _one(_result(lock_acquired=False)); assert sched._retention_consecutive_failures == 0
    _one(_result(enabled=False, lock_acquired=False)); assert sched._retention_consecutive_failures == 0
    # a fresh streak of three can alert again
    _one(_result(failed_batches=1)); _one(_result(failed_batches=1)); _one(_result(timed_out=True))
    assert alerts == [3, 3]
