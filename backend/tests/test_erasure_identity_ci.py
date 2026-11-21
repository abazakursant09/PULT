"""Exact additive CI gate, not an alteration of the existing custodian proofs."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
NAME = "Synthetic independent identity registry (no dispatch)"
TESTS = ["tests/test_erasure_identity.py", "tests/test_erasure_identity_process.py", "tests/test_erasure_identity_ci.py"]


def document():
    return yaml.load((ROOT / ".github/workflows/backup_restore_synthetic.yml").read_text(encoding="utf8"),
                     Loader=yaml.BaseLoader)


def check(value):
    assert value["permissions"] == {"contents": "read"}
    assert set(value["jobs"]) == {"synthetic"}
    job = value["jobs"]["synthetic"]
    assert not {"if", "continue-on-error"} & job.keys()
    matches = [step for step in job["steps"] if step.get("name") == NAME]
    assert matches == [dict(name=NAME, **{"working-directory": "backend"},
                            run="python -B -m pytest " + " ".join(TESTS) + " -q")]
    names = [step["name"] for step in job["steps"]]
    assert names.index("Install backend deps + offline contract guard") < names.index(NAME)
    assert names.index(NAME) < names.index("Synthetic Linux custodian isolation (real distinct unprivileged UIDs)")
    for event in ("push", "pull_request"):
        paths = value["on"][event]["paths"]
        assert {"backend/" + name for name in TESTS} <= set(paths)
        assert not {"*", "**", "backend/**", "ops/**", "docs/**"} & set(paths)


def test_exact_additive_no_skip_identity_ci_gate():
    check(document())


@pytest.mark.parametrize("key,value", [("if", "false"), ("continue-on-error", "true"),
                                      ("run", "python -m pytest tests/test_erasure_identity.py -q")])
def test_skipped_partial_or_tolerated_proof_is_rejected(key, value):
    altered = deepcopy(document())
    step = next(step for step in altered["jobs"]["synthetic"]["steps"] if step["name"] == NAME)
    step[key] = value
    with pytest.raises(AssertionError):
        check(altered)
