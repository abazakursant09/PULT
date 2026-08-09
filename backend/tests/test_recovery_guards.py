"""SECURITY-2D-1C-B — source guards: the recovery layer is read-only and unwired from the write path."""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_BE = _ROOT / "backend"


def _src(rel):
    return (_BE / rel).read_text(encoding="utf-8")


# provider WRITE method names that must NEVER be CALLED from the recovery layer
_WRITE_CALLS = ("set_price(", "publish_feedback_answer(", "set_bid(", "set_campaign_state(",
                "update_card(", "set_discount(", "activate", "deactivate")


def test_reconcile_read_calls_no_provider_write():
    src = _src("services/marketplace/recovery/reconcile_read.py")
    # strip the _Provider-agnostic parts: any occurrence of a write-call pattern is forbidden.
    for w in _WRITE_CALLS:
        assert w not in src, f"recovery reconcile must not call provider write: {w}"


def test_sweep_never_writes_status_generation_or_dispatch():
    src = _src("services/marketplace/recovery/recovery_sweep.py")
    # the only UPDATE ... SET touches reconciliation_* / last_reconciled_at / next_reconcile_at — never
    # the bare status column, claim_generation or dispatch_started_at. Match a BARE `status=` assignment
    # (a char other than '_' before it, so reconciliation_status= is excluded).
    assert re.search(r"[^_a-z]status\s*=[^=]", src) is None       # no bare status= assignment
    # no ASSIGNMENT / mutation of the fencing token or the dispatch stamp (docstring mentions are fine)
    assert "claim_generation =" not in src and "claim_generation=" not in src
    assert "dispatch_started_at =" not in src and "dispatch_started_at=" not in src
    assert "reconciliation_status=" in src


def test_executor_does_not_reference_recovery():
    # the executor must not import/wire the recovery layer (needs_reconcile/_reconcile are 1B-B internals)
    src = _src("services/marketplace/executor.py")
    assert "recovery" not in src and "recovery_sweep" not in src and "reconcile_read" not in src


def test_scheduler_wires_recovery_sweep_flag_gated():
    # SECURITY-2D-1C-D: the read-only reconciliation sweep is now wired into the single scheduler, but ONLY
    # behind its fail-closed master flag and never onto a provider-write path. (The pre-C1-D "not wired at
    # all" assertion is superseded by this gated wiring.)
    src = _src("tasks/scheduler.py")
    assert "run_recovery_sweep" in src                                   # wired
    assert "settings.recovery_reaper_enabled is not True" in src          # fail-closed gate before create_task
    for forbidden in ("spec.dispatch", "_dispatch_and_finalize", "operator_resume",
                      "executor.execute", "executor.revert"):
        assert forbidden not in src, forbidden                            # never reaches a provider write


def test_recovery_flags_default_off():
    from config import Settings
    s = Settings()
    assert s.recovery_reaper_enabled is False        # OFF
    assert s.recovery_reaper_dry_run is True          # fail-safe


def test_no_recovery_sweep_wired_to_http():
    # 1C-B invariant retained under C3A: no recovery SWEEP (reaper / reconcile / re-own) is ever exposed
    # over HTTP. The 1C-C3A read-only operator VIEW (routers/internal_recovery.py) is a SEPARATE, strictly
    # read-only surface behind its own machine-to-machine key perimeter and its own guards
    # (test_operator_recovery_readonly / _pg) — it lists/reads disputed rows and calls no sweep.
    routers = (_BE / "routers")
    for p in routers.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        assert "recovery_sweep" not in src and "run_recovery" not in src \
            and "run_reown_sweep" not in src, p.name


def test_target_not_observed_semantic_lock():
    # SECURITY-2D-1C-B final safety review: the dangerous "not_observed" value is renamed to the neutral
    # "target_not_observed", and its meaning is locked in code — a current-state mismatch is NOT proof the
    # operation was never applied and NEVER authorises a retry.
    from models.execution_log import _RECON_STATUSES
    from services.marketplace.recovery import reconcile_read
    assert "target_not_observed" in _RECON_STATUSES
    assert "not_observed" not in _RECON_STATUSES               # bare dangerous name gone from the enum
    assert reconcile_read.TARGET_NOT_OBSERVED == "target_not_observed"
    assert not hasattr(reconcile_read, "NOT_OBSERVED")         # old constant removed
    lock = "NOT proof the original operation was never applied"
    assert lock in _src("services/marketplace/recovery/reconcile_read.py")
    assert lock in _src("models/execution_log.py")


def test_recovery_reads_only_read_client_methods():
    # reconcile_read imports must be read helpers / clients, never the executor's execute/revert
    src = _src("services/marketplace/recovery/reconcile_read.py")
    assert "executor.execute" not in src and ".revert(" not in src and "spec.dispatch" not in src
