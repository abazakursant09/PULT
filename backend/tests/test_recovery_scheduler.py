"""SECURITY-2D-1C-D — wiring the OFF-by-default recovery reconciliation + safe re-own sweeps into the
SINGLE scheduler.

These tests exercise ONLY the scheduler wiring (gate, monotonic cadence with a short initial delay,
one-tracked-task-per-type, done-callback result/exception consumption, shutdown cancel+await) with the
sweeps stubbed — the sweeps' own DB/provider/CAS behaviour is proved by their own suites. A real-PG test
(test_recovery_scheduler_pg.py) proves advisory-lock independence + disjoint allowlist columns.

Hard safety boundary asserted by source/AST guards: the scheduler contour never imports or calls the
executor, the operator resume contour, or a provider write; the recovery helpers own no DB session and
issue no SQL of their own — they only spawn the existing sweeps.
"""
from __future__ import annotations

import ast
import asyncio
import os

import pytest

import tasks.scheduler as sched
from services.marketplace.recovery import recovery_sweep as rsweep
from services.marketplace.recovery import reown_sweep as osweep

_HERE = os.path.dirname(__file__)
_SCHED_SRC = os.path.join(_HERE, "..", "tasks", "scheduler.py")
_MAIN_SRC = os.path.join(_HERE, "..", "main.py")


# ── helpers ──────────────────────────────────────────────────────────────────────

class _Clock:
    """A monotonic shim installed in place of scheduler.time so patching cannot disturb asyncio's own
    loop clock. Only .monotonic() is used by the scheduler."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def monotonic(self) -> float:
        return self.t


def _install_clock(monkeypatch, t: float = 1000.0) -> _Clock:
    clk = _Clock(t)
    monkeypatch.setattr(sched, "time", clk)
    return clk


def _reset() -> None:
    sched._reconcile_task = None
    sched._reown_task = None
    sched._reconcile_next_due_at = None
    sched._reown_next_due_at = None
    sched._reconcile_consecutive_failures = 0
    sched._reown_consecutive_failures = 0


def _rres(**o):
    return rsweep.RecoverySweepResult(
        enabled=o.get("enabled", True), lock_acquired=o.get("lock_acquired", True),
        dry_run=o.get("dry_run", False), candidates=o.get("candidates", 0),
        reconciled=o.get("reconciled", 0), failed_users=o.get("failed_users", 0),
        timed_out=o.get("timed_out", False), duration_ms=o.get("duration_ms", 1))


def _ores(**o):
    return osweep.ReownSweepResult(
        enabled=o.get("enabled", True), lock_acquired=o.get("lock_acquired", True),
        dry_run=o.get("dry_run", False), candidates=o.get("candidates", 0),
        eligible=o.get("eligible", 0), reowned=o.get("reowned", 0),
        skipped_invalid=o.get("skipped_invalid", 0), skipped_race=o.get("skipped_race", 0),
        failed_batches=o.get("failed_batches", 0), timed_out=o.get("timed_out", False),
        duration_ms=o.get("duration_ms", 1))


def _stub_reconcile(monkeypatch, *, result=None, exc=None, capture=None, block: "asyncio.Event | None" = None):
    async def fake(**kw):
        if capture is not None:
            capture.update(kw)
        if block is not None:
            await block.wait()
        if exc is not None:
            raise exc
        return result if result is not None else _rres()
    monkeypatch.setattr(rsweep, "run_recovery_sweep", fake)


def _stub_reown(monkeypatch, *, result=None, exc=None, capture=None, block: "asyncio.Event | None" = None):
    async def fake(**kw):
        if capture is not None:
            capture.update(kw)
        if block is not None:
            await block.wait()
        if exc is not None:
            raise exc
        return result if result is not None else _ores()
    monkeypatch.setattr(osweep, "run_reown_sweep", fake)


def _enable(monkeypatch, *, reaper=False, reown=False, reaper_dry=True, reown_dry=True):
    monkeypatch.setattr(sched.settings, "recovery_reaper_enabled", reaper)
    monkeypatch.setattr(sched.settings, "recovery_reown_enabled", reown)
    monkeypatch.setattr(sched.settings, "recovery_reaper_dry_run", reaper_dry)
    monkeypatch.setattr(sched.settings, "recovery_reown_dry_run", reown_dry)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _drain(task) -> None:
    """Wait for a task to finish AND for its add_done_callback to run — never re-raising a failed task."""
    if task is None:
        return
    while not task.done():
        await asyncio.sleep(0)
    await asyncio.sleep(0)


# ── 1. both flags OFF → nothing, over many ticks ──────────────────────────────────

def test_both_off_no_task_no_spawn():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=False, reown=False)
        # even past any conceivable deadline, an OFF feature spawns nothing
        sched._reconcile_next_due_at = 0.0
        sched._reown_next_due_at = 0.0
        spawned = {"n": 0}
        orig = sched.asyncio.create_task
        monkeypatch.setattr(sched.asyncio, "create_task",
                            lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1) or orig(*a, **k))
        for _ in range(10):
            sched._recovery_reconcile_tick()
            sched._reown_tick()
        assert spawned["n"] == 0
        assert sched._reconcile_task is None and sched._reown_task is None
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 2/3. exactly the enabled sweep spawns ─────────────────────────────────────────

def test_only_reconcile_when_only_reaper_on():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=False)
        _stub_reconcile(monkeypatch, result=_rres())
        _stub_reown(monkeypatch, result=_ores())
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        rt = sched._reconcile_task
        assert rt is not None
        assert sched._reown_task is None
        await _drain(rt)
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


def test_only_reown_when_only_reown_on():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=False, reown=True)
        _stub_reconcile(monkeypatch, result=_rres())
        _stub_reown(monkeypatch, result=_ores())
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        ot = sched._reown_task
        assert ot is not None
        assert sched._reconcile_task is None
        await _drain(ot)
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 4. both ON → two independent tasks ────────────────────────────────────────────

def test_both_on_two_independent_tasks():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=True)
        _stub_reconcile(monkeypatch, result=_rres())
        _stub_reown(monkeypatch, result=_ores())
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        rt, ot = sched._reconcile_task, sched._reown_task
        assert rt is not None and ot is not None and rt is not ot
        await _drain(rt)
        await _drain(ot)
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 5. a live task is never duplicated ────────────────────────────────────────────

def test_live_task_not_duplicated():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=True)
        ev_r, ev_o = asyncio.Event(), asyncio.Event()
        _stub_reconcile(monkeypatch, result=_rres(), block=ev_r)
        _stub_reown(monkeypatch, result=_ores(), block=ev_o)
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        first_r, first_o = sched._reconcile_task, sched._reown_task
        # advance the clock well past the interval — the still-running task must NOT be replaced
        sched.time.t = 5000.0 + sched.settings.recovery_reconcile_interval_seconds + 10
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        assert sched._reconcile_task is first_r
        assert sched._reown_task is first_o
        ev_r.set()
        ev_o.set()
        await _drain(first_r)
        await _drain(first_o)
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 6. a task exception is retrieved; the counter advances; no crash ──────────────

def test_task_exception_retrieved_and_counted():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=True)
        _stub_reconcile(monkeypatch, exc=RuntimeError("boom"))
        _stub_reown(monkeypatch, exc=RuntimeError("boom"))
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        rt, ot = sched._reconcile_task, sched._reown_task
        await _drain(rt)
        await _drain(ot)
        # exception consumed by the done-callback (no 'never retrieved'); counters advanced; refs cleared
        assert sched._reconcile_consecutive_failures == 1
        assert sched._reown_consecutive_failures == 1
        assert sched._reconcile_task is None and sched._reown_task is None
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


def test_sentry_alert_once_at_threshold_then_reset():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=False)
        alerts = {"n": 0}
        monkeypatch.setattr(sched, "_recovery_sentry_alert",
                            lambda label, count: alerts.__setitem__("n", alerts["n"] + 1))
        # 3 consecutive failing runs -> exactly ONE alert at N==3
        for _ in range(sched._RECOVERY_ALERT_AFTER):
            _stub_reconcile(monkeypatch, exc=RuntimeError("boom"))
            sched._reconcile_next_due_at = sched.time.t
            sched._recovery_reconcile_tick()
            await _drain(sched._reconcile_task)
            sched.time.t += 1
        assert alerts["n"] == 1
        assert sched._reconcile_consecutive_failures == sched._RECOVERY_ALERT_AFTER
        # a fully successful run resets the streak
        _stub_reconcile(monkeypatch, result=_rres(failed_users=0, timed_out=False))
        sched._reconcile_next_due_at = sched.time.t
        sched._recovery_reconcile_tick()
        await _drain(sched._reconcile_task)
        assert sched._reconcile_consecutive_failures == 0
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


def test_disabled_or_lock_skip_does_not_count_failure():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=True)
        # sweep returned "not lock_acquired" (another run active) -> neither success nor failure
        _stub_reconcile(monkeypatch, result=_rres(lock_acquired=False))
        _stub_reown(monkeypatch, result=_ores(enabled=True, lock_acquired=False))
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        await _drain(sched._reconcile_task)
        await _drain(sched._reown_task)
        assert sched._reconcile_consecutive_failures == 0
        assert sched._reown_consecutive_failures == 0
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 7. shutdown cancels + awaits a live task ──────────────────────────────────────

def test_shutdown_cancels_and_awaits_both():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=True)
        ev_r, ev_o = asyncio.Event(), asyncio.Event()   # never set -> tasks would run forever
        _stub_reconcile(monkeypatch, block=ev_r)
        _stub_reown(monkeypatch, block=ev_o)
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        rt, ot = sched._reconcile_task, sched._reown_task
        await sched._shutdown_reconcile()
        await sched._shutdown_reown()
        await asyncio.sleep(0)                            # let done-callbacks run
        assert rt.cancelled() and ot.cancelled()
        assert sched._reconcile_task is None and sched._reown_task is None
        assert sched._reconcile_consecutive_failures == 0   # cancel is not a failure
        assert sched._reown_consecutive_failures == 0
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 8/9/11. initial delay is set from monotonic at EACH run_scheduler start ───────

def test_initial_delay_set_at_each_start():
    async def body(monkeypatch):
        _reset()
        clk = _install_clock(monkeypatch, 5000.0)

        async def _noop():
            return None

        for name in ("_send_daily_reports", "_send_weekly_reports", "_review_ingest_tick",
                     "_automation_tick", "_measurement_close_tick", "_advisory_runtime_tick",
                     "_uploads_cleanup_tick", "_shutdown_retention", "_shutdown_reconcile",
                     "_shutdown_reown"):
            monkeypatch.setattr(sched, name, _noop)
        monkeypatch.setattr(sched, "_observation_retention_tick", lambda: None)
        monkeypatch.setattr(sched, "_recovery_reconcile_tick", lambda: None)
        monkeypatch.setattr(sched, "_reown_tick", lambda: None)

        class _Stop(Exception):
            pass

        async def _sleep_stop(_):
            raise _Stop()
        monkeypatch.setattr(sched.asyncio, "sleep", _sleep_stop)

        # first start at t=5000 -> reconcile due at +300, reown at +600
        try:
            await sched.run_scheduler()
        except _Stop:
            pass
        assert sched._reconcile_next_due_at == 5000.0 + sched.settings.recovery_reconcile_initial_delay_seconds
        assert sched._reown_next_due_at == 5000.0 + sched.settings.recovery_reown_initial_delay_seconds

        # restart at a later monotonic -> the full initial delay is re-applied from the NEW start
        clk.t = 9000.0
        try:
            await sched.run_scheduler()
        except _Stop:
            pass
        assert sched._reconcile_next_due_at == 9000.0 + sched.settings.recovery_reconcile_initial_delay_seconds
        assert sched._reown_next_due_at == 9000.0 + sched.settings.recovery_reown_initial_delay_seconds
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 10/12/13. cadence: not-due skips; on start the next deadline is NOW+interval ──

def test_not_due_before_deadline_then_fires_at_and_after():
    async def body(monkeypatch):
        _reset()
        clk = _install_clock(monkeypatch, 1000.0)
        _enable(monkeypatch, reaper=True, reown=False)
        _stub_reconcile(monkeypatch, result=_rres())
        sched._reconcile_next_due_at = 1300.0            # due 300s out

        clk.t = 1299.999                                  # BEFORE the deadline -> no task
        sched._recovery_reconcile_tick()
        assert sched._reconcile_task is None

        clk.t = 1300.0                                    # exactly AT the deadline -> fire
        sched._recovery_reconcile_tick()
        t1 = sched._reconcile_task
        assert t1 is not None
        # on start, the next deadline was pushed to NOW + steady-state interval (independent of initial delay)
        assert sched._reconcile_next_due_at == 1300.0 + sched.settings.recovery_reconcile_interval_seconds
        await _drain(t1)

        # a lock-skipped run still advanced the deadline -> no tight loop: an immediate re-tick does nothing
        clk.t = 1300.0
        sched._recovery_reconcile_tick()
        assert sched._reconcile_task is None
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


def test_next_run_only_after_own_interval():
    async def body(monkeypatch):
        _reset()
        clk = _install_clock(monkeypatch, 1000.0)
        _enable(monkeypatch, reaper=True, reown=False)
        _stub_reconcile(monkeypatch, result=_rres())
        sched._reconcile_next_due_at = 1000.0
        sched._recovery_reconcile_tick()
        await _drain(sched._reconcile_task)
        due = sched._reconcile_next_due_at
        assert due == 1000.0 + sched.settings.recovery_reconcile_interval_seconds
        # just before the next interval -> nothing
        clk.t = due - 1
        sched._recovery_reconcile_tick()
        assert sched._reconcile_task is None
        # at the next interval -> fire again
        clk.t = due
        sched._recovery_reconcile_tick()
        assert sched._reconcile_task is not None
        await _drain(sched._reconcile_task)
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 14. run-wrappers pass the config dry-run + safe args; independent cadence ─────

def test_run_wrappers_pass_dry_run_and_safe_args():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=True, reaper_dry=True, reown_dry=True)
        cap_r, cap_o = {}, {}
        _stub_reconcile(monkeypatch, result=_rres(), capture=cap_r)
        _stub_reown(monkeypatch, result=_ores(), capture=cap_o)
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        await _drain(sched._reconcile_task)
        await _drain(sched._reown_task)
        for cap in (cap_r, cap_o):
            assert cap["dry_run"] is True          # dry-run comes straight from config
            assert cap["now"] is None              # production UTC
            assert cap["max_duration"] == rsweep.DEFAULT_MAX_DURATION_SECONDS
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


def test_counters_and_deadlines_are_independent():
    async def body(monkeypatch):
        _reset()
        _install_clock(monkeypatch, 5000.0)
        _enable(monkeypatch, reaper=True, reown=True)
        _stub_reconcile(monkeypatch, exc=RuntimeError("boom"))   # reconcile fails
        _stub_reown(monkeypatch, result=_ores())                 # reown succeeds
        sched._reconcile_next_due_at = 5000.0
        sched._reown_next_due_at = 5000.0
        sched._recovery_reconcile_tick()
        sched._reown_tick()
        await _drain(sched._reconcile_task)
        await _drain(sched._reown_task)
        assert sched._reconcile_consecutive_failures == 1        # only reconcile counted a failure
        assert sched._reown_consecutive_failures == 0            # reown counter untouched
    mp = pytest.MonkeyPatch()
    try:
        _run(body(mp))
    finally:
        mp.undo()


# ── 15/16/17/19/21. source + AST safety guards on the scheduler wiring ────────────

def _sched_src() -> str:
    with open(_SCHED_SRC, encoding="utf-8") as f:
        return f.read()


_RECOVERY_FUNCS = (
    "_recovery_reconcile_tick", "_reconcile_run", "_reconcile_done", "_shutdown_reconcile",
    "_reown_tick", "_reown_run", "_reown_done", "_shutdown_reown", "_recovery_sentry_alert",
)


def _recovery_region(src: str) -> str:
    """Exact source of ONLY the C1-D recovery helper functions (via AST) — never the unrelated report /
    advisory / retention helpers that legitimately own DB sessions."""
    tree = ast.parse(src)
    parts = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _RECOVERY_FUNCS:
            parts.append(ast.get_source_segment(src, node))
    assert len(parts) == len(_RECOVERY_FUNCS), f"found {len(parts)} of {len(_RECOVERY_FUNCS)} recovery funcs"
    return "\n".join(parts)


def test_exactly_one_scheduler_loop():
    assert _sched_src().count("while True") == 1


def test_scheduler_never_imports_executor_or_operator_contour():
    src = _sched_src()
    tree = ast.parse(src)
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
    for m in mods:
        assert not m.endswith("marketplace.executor"), m
        assert "operator_resume" not in m, m
    # the recovery contour reaches ONLY the two read-only/ownership sweeps
    assert "recovery.recovery_sweep" in " ".join(mods)
    assert "recovery.reown_sweep" in " ".join(mods)


def test_recovery_region_reaches_no_provider_write_and_owns_no_session():
    region = _recovery_region(_sched_src())
    for forbidden in ("spec.dispatch", "_dispatch_and_finalize", "operator_resume", "authorize_retry",
                      "authorize-retry", "retry_authorized", "executor.execute", "executor.revert",
                      "AsyncSessionLocal", ".commit(", "UPDATE ", "credential_vault", "decrypt"):
        assert forbidden not in region, forbidden
    # it DOES delegate to the two existing sweeps and nothing else
    assert "run_recovery_sweep" in region
    assert "run_reown_sweep" in region


def test_three_sweeps_have_distinct_advisory_ops():
    """Retention / reconciliation / re-own share one advisory namespace but use three DISTINCT operation
    codes, so wiring all three into one scheduler can never make them block each other."""
    from services.marketplace.retention import observation_sweep as obs
    ns = {rsweep._LOCK_NAMESPACE, osweep._LOCK_NAMESPACE, obs._LOCK_NAMESPACE}
    ops = [rsweep._LOCK_OPERATION, osweep._LOCK_OPERATION, obs._LOCK_OPERATION]
    assert ns == {0x50554C54}
    assert len(set(ops)) == 3


def test_main_gains_no_extra_scheduler_task():
    with open(_MAIN_SRC, encoding="utf-8") as f:
        main = f.read()
    assert "run_recovery_sweep" not in main
    assert "run_reown_sweep" not in main
    assert main.count("run_scheduler(") == 1
    assert main.count("asyncio.create_task(") == 4     # monitor, scheduler, ai_worker, intel_loop — unchanged
