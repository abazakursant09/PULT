"""SECURITY-2D-1B-B — guard: the rollout contract is documented and the alias guard lives in the
executor claim path (not only in a UI reader). A doc/source guard, mirroring the project's existing
source-guard pattern."""
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_rollout_doc_lists_the_mandatory_steps():
    doc = (_ROOT / "docs" / "2D-1B-B-ROLLOUT.md").read_text(encoding="utf-8").lower()
    for needle in ("stop", "drain", "backup", "migrat", "deploy backend and frontend",
                   "cdn", "automation_enabled", "live-smoke"):
        assert needle in doc, needle


def test_alias_guard_is_in_the_executor():
    src = (_ROOT / "backend" / "services" / "marketplace" / "executor.py").read_text(encoding="utf-8")
    # the legacy guard runs inside the executor claim path and returns a non-dispatching reconcile
    assert "async def _legacy_alias_hit" in src
    assert "await _legacy_alias_hit(" in src
    assert "LEGACY_OPERATION_NEEDS_RECONCILE" in src


def test_partial_unique_is_scoped_to_v1():
    mig = (_ROOT / "backend" / "alembic" / "versions"
           / "uqc1a2b3c4d01_execlog_op_claim_unique.py").read_text(encoding="utf-8")
    assert "LIKE 'v1:%'" in mig and "preflight" in mig.lower()
