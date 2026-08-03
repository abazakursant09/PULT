"""SECURITY-2D-1C-C2 — source guards: the re-own sweep only transfers ownership, never dispatches.

Import/AST/SET-clause guards (not broad substring matching): the re-own service must not import provider
clients or the executor's execute/revert, must not name any provider write / dispatch, and its single
UPDATE must write ONLY claim_generation / reown_count / last_reowned_at — never status /
dispatch_started_at / attempt_count / reconciliation. Flag defaults False; no scheduler/router wiring.
"""
import ast
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BE = _ROOT
_SRC = (_BE / "services" / "marketplace" / "recovery" / "reown_sweep.py").read_text(encoding="utf-8")


def _imported_modules():
    tree = ast.parse(_SRC)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            mods.update(f"{node.module}.{a.name}" for a in node.names)
    return mods


def test_no_provider_client_or_executor_imports():
    mods = _imported_modules()
    banned_substr = ("wb_client", "ozon_client", "yandex", "reviews", "ozon_performance",
                     "executor", "action_catalog", "credential_vault")
    for m in mods:
        assert not any(b in m for b in banned_substr), f"re-own must not import {m}"


def test_no_provider_or_dispatch_or_execute_calls():
    for name in ("spec.dispatch", "publish_feedback_answer", "publish_", "set_price", "set_bid",
                 "set_campaign_state", "update_card", ".execute(db=", "executor.execute", ".revert(",
                 "run_one", "dispatch("):
        assert name not in _SRC, f"re-own must not reference {name}"


def test_single_update_writes_only_c2_fields():
    # exactly one SQL UPDATE (the re-own CAS); its SET clause touches ONLY the three C2 columns.
    lowered = _SRC.lower()
    assert lowered.count("update execution_logs") == 1
    set_clause = _SRC.split("SET ", 1)[1].split("WHERE", 1)[0]
    assert "claim_generation = claim_generation + 1" in set_clause
    assert "reown_count = reown_count + 1" in set_clause
    assert "last_reowned_at = :now" in set_clause
    for forbidden in ("attempt_count", "last_attempt_at", "reconciliation_status",
                      "reconciliation_attempts", "dispatch_started_at"):
        assert forbidden not in set_clause, f"re-own CAS must not write {forbidden}"
    # 'status' appears only as a read predicate (status = 'pending') in the WHERE, never in SET.
    assert "status" not in set_clause


def test_flag_defaults_off():
    from config import Settings
    s = Settings()
    assert s.recovery_reown_enabled is False
    assert s.recovery_reown_dry_run is True


def test_scheduler_and_routers_do_not_call_reown_sweep():
    for rel in ("tasks/scheduler.py",):
        p = _BE / rel
        if p.exists():
            assert "reown_sweep" not in p.read_text(encoding="utf-8") and \
                   "run_reown_sweep" not in p.read_text(encoding="utf-8")
    for p in (_BE / "routers").glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "reown_sweep" not in txt and "run_reown_sweep" not in txt, p.name


def test_uses_canonical_validators():
    # eligibility is decided by the canonical helpers, not a bespoke re-implementation.
    assert "is_valid_v1_key" in _SRC and "is_valid_fingerprint" in _SRC
