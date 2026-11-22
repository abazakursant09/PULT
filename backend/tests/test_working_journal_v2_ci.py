"""Focused CI wiring; no source loading, credentials, processes or network."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/backup_restore_synthetic.yml"
STEP = "Working v2 namespace contract (socket-free, no admission)"
RUN = "python -B -m pytest backend/tests/test_working_journal_v2.py backend/tests/test_working_journal_v2_ci.py -q"
PATHS = {"ops/backup/**", "backend/tests/test_working_journal_v2.py",
         "backend/tests/test_working_journal_v2_ci.py", "docs/working-journal-v2-namespace.md"}


def workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def check(value):
    assert value["permissions"] == {"contents": "read"}
    assert value["on"]["push"]["branches"] == ["master"]
    for event in ("push", "pull_request"):
        assert PATHS <= set(value["on"][event]["paths"])
    job = value["jobs"]["synthetic"]
    assert not {"if", "continue-on-error"}.intersection(job)
    steps = job["steps"]
    matches = [s for s in steps if s.get("name") == STEP]
    assert matches == [{"name": STEP, "run": RUN}]
    assert next(i for i,s in enumerate(steps) if s.get("name") == STEP) > next(
        i for i,s in enumerate(steps) if s.get("name") == "Install backend deps + offline contract guard")


def test_working_namespace_suite_is_mandatory_in_existing_backup_workflow():
    check(workflow())


@pytest.mark.parametrize("change", ["missing", "skip", "continue", "wrong-run", "wrong-directory",
                                    "duplicate", "job-skip", "job-continue", "pr-path", "push-path"])
def test_guard_rejects_suppressed_or_untriggered_suite(change):
    value = deepcopy(workflow())
    job = value["jobs"]["synthetic"]
    step = next(s for s in job["steps"] if s.get("name") == STEP)
    if change == "missing":
        job["steps"].remove(step)
    elif change == "skip":
        step["if"] = "false"
    elif change == "continue":
        step["continue-on-error"] = "true"
    elif change == "wrong-run":
        step["run"] = RUN + " || true"
    elif change == "wrong-directory":
        step["working-directory"] = "backend"
    elif change == "duplicate":
        job["steps"].append(dict(step))
    elif change == "job-skip":
        job["if"] = "false"
    elif change == "job-continue":
        job["continue-on-error"] = "true"
    elif change == "pr-path":
        value["on"]["pull_request"]["paths"].remove("backend/tests/test_working_journal_v2.py")
    elif change == "push-path":
        value["on"]["push"]["paths"].remove("docs/working-journal-v2-namespace.md")
    with pytest.raises(AssertionError):
        check(value)
