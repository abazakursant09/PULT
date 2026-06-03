"""Runtime orchestration contract tests (Sprint 70).

The orchestration entry points `_compute_insights`, `_process_user` and
`run_intelligence_loop` are DB- and time-bound (they read datetime.utcnow,
query the database, and send Telegram messages). Their full output cannot be
frozen as a deterministic golden master without modifying the code, which
Sprint 70 forbids.

Instead we freeze their *contract*: existence, async nature, and the parameter
names the rest of the system depends on. A signature change (a renamed/removed
parameter — the most common silent breakage) fails here. The deterministic
behavior they orchestrate is frozen separately in
test_runtime_characterization.py via their pure helpers.

See the Risk Map in docs/governance/runtime_characterization.md for what remains
unprotected at the orchestration level.
"""
from __future__ import annotations

import inspect


def test_compute_insights_contract() -> None:
    from routers.action_engine import _compute_insights
    assert inspect.iscoroutinefunction(_compute_insights)
    params = list(inspect.signature(_compute_insights).parameters)
    assert params == [
        "uid", "db", "statuses", "resolved_history", "notif_counts", "rebuild_outcomes",
    ], f"_compute_insights signature changed: {params}"


def test_process_user_contract() -> None:
    from tasks.intelligence_loop import _process_user
    assert inspect.iscoroutinefunction(_process_user)
    params = list(inspect.signature(_process_user).parameters)
    assert params == ["user", "tg_settings", "db"], f"_process_user signature changed: {params}"


def test_run_intelligence_loop_is_coroutine() -> None:
    from tasks.intelligence_loop import run_intelligence_loop
    assert inspect.iscoroutinefunction(run_intelligence_loop)
