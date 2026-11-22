"""CI and immutable-parent wiring, not native admission or a service installer."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ops.backup import working_launch_profile as entry

PORTABLE = "Working profile offline source and CI contracts"
NATIVE = "Native working profile capture and import preparation only"
PREVIOUS = "Native synthetic PG source-envelope admission only"
RUN = "python -B -m pytest backend/tests/test_working_launch_profile.py backend/tests/test_working_launch_profile_ci.py -q"
NATIVE_RUN = "sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B ops/backup/tests/working_launch_profile_proof.py"
TRIGGERS = {"ops/backup/**", ".gitattributes", "backend/tests/test_working_launch_profile.py",
            "backend/tests/test_working_launch_profile_ci.py", "docs/working-launch-profile.md"}


def workflow():
    return yaml.load((ROOT/".github/workflows/backup_restore_synthetic.yml").read_text(), Loader=yaml.BaseLoader)


def check(value):
    assert value["permissions"] == {"contents": "read"}
    assert value["on"]["push"]["branches"] == ["master"]
    for event in ("pull_request", "push"):
        assert TRIGGERS <= set(value["on"][event]["paths"])
    job = value["jobs"]["synthetic"]
    assert not {"if", "continue-on-error"}.intersection(job)
    names = [s.get("name") for s in job["steps"]]
    start = names.index(PREVIOUS)
    assert names[start + 1:] == [PORTABLE, NATIVE]
    assert job["steps"][start + 1:] == [{"name": PORTABLE, "run": RUN}, {"name": NATIVE, "run": NATIVE_RUN}]
    assert names.index("Download seven hash-pinned custodian v2 proof wheels") < start


def test_existing_workflow_requires_both_new_proof_layers():
    check(workflow())


@pytest.mark.parametrize("change", ["skip", "continue", "wrong-command", "wrong-directory", "missing-native",
                                    "move-native", "job-skip", "pr-filter", "push-filter"])
def test_suppressed_or_untriggered_gate_is_rejected(change):
    value = deepcopy(workflow())
    job = value["jobs"]["synthetic"]
    step = next(s for s in job["steps"] if s.get("name") == NATIVE)
    if change == "skip":
        step["if"] = "false"
    elif change == "continue":
        step["continue-on-error"] = "true"
    elif change == "wrong-command":
        step["run"] += " || true"
    elif change == "wrong-directory":
        step["working-directory"] = "backend"
    elif change == "missing-native":
        job["steps"].remove(step)
    elif change == "move-native":
        job["steps"].insert(0, job["steps"].pop())
    elif change == "job-skip":
        job["if"] = "false"
    elif change == "pr-filter":
        value["on"]["pull_request"]["paths"].remove("backend/tests/test_working_launch_profile.py")
    else:
        value["on"]["push"]["paths"].remove("docs/working-launch-profile.md")
    with pytest.raises(AssertionError):
        check(value)


def test_parent_pins_are_unchanged_exact_LF_git_bytes():
    assert len(entry.PINS) == 8
    for name, wanted in entry.PINS.items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == wanted


def test_frozen_global_attributes_are_not_changed_for_the_new_release():
    # This metadata is itself captured by the accepted SSH/S3 source catalogue.
    # Release assembly, not a global attributes mutation, supplies exact LF bytes.
    assert hashlib.sha256((ROOT/".gitattributes").read_bytes()).hexdigest() == (
        "d8e7c02e359d7166b2846337616608bd1bf7e45d5540df004efe5d060fea2cec")


def test_example_is_not_an_approved_or_runnable_profile():
    body = (ROOT/"ops/backup/working_launch_profile.example.json").read_bytes()
    value = json.loads(body)
    assert value["entry_sha256"] is value["actor"]["uid"] is value["actor"]["gid"] is None
    assert set(value) == entry.FIELDS
    with pytest.raises((entry.Refused, ValueError, TypeError)):
        entry.shape(value)
    with pytest.raises(entry.Refused):
        entry.document(body)
