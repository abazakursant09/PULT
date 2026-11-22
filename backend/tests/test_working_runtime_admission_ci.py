"""Exact additive CI and source pins; no native/provider/launch claim."""
from copy import deepcopy
import ast
import hashlib
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "ops/backup/working_runtime_admission.py"
entry = ModuleType("_working_runtime_ci_entry")
entry.__file__ = str(ENTRY)
exec(compile(ENTRY.read_bytes(), str(ENTRY), "exec", dont_inherit=True, optimize=0), entry.__dict__)
PORTABLE = "Working local runtime admission contracts"
NATIVE = "Native working local runtime ownership without dispatch"
PREVIOUS = "Guard dormant synthetic PG source-envelope contracts"
FOLLOWING = "Native synthetic PG source-envelope admission only"
RUN = "python -B -m pytest backend/tests/test_working_runtime_admission.py backend/tests/test_working_runtime_resources.py backend/tests/test_working_runtime_admission_ci.py -q"
NATIVE_RUN = "sudo -n -- unshare --net /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3.12 -I -S -B ops/backup/tests/working_runtime_admission_proof.py"
PATHS = {"ops/backup/**", "backend/tests/test_working_runtime_admission.py",
         "backend/tests/test_working_runtime_resources.py", "backend/tests/test_working_runtime_admission_ci.py",
         "docs/working-runtime-admission.md"}


def workflow():
    return yaml.load((ROOT/".github/workflows/backup_restore_synthetic.yml").read_text(), Loader=yaml.BaseLoader)


def check(value):
    assert value["permissions"] == {"contents": "read"}
    assert value["on"]["push"]["branches"] == ["master"]
    for event in ("push", "pull_request"):
        assert PATHS <= set(value["on"][event]["paths"])
    job = value["jobs"]["synthetic"]
    assert not {"if", "continue-on-error"}.intersection(job)
    names = [step.get("name") for step in job["steps"]]
    index = names.index(PREVIOUS)
    assert names[index + 1:index + 4] == [PORTABLE, NATIVE, FOLLOWING]
    assert job["steps"][index + 1:index + 3] == [
        {"name": PORTABLE, "run": RUN}, {"name": NATIVE, "run": NATIVE_RUN}]
    assert names.count(PORTABLE) == names.count(NATIVE) == 1
    assert names.index("Download seven hash-pinned custodian v2 proof wheels") < index
    assert names[-3:] == [FOLLOWING, "Working profile offline source and CI contracts",
                          "Native working profile capture and import preparation only"]


def test_both_new_layers_are_mandatory_without_changing_the_predecessor_tail():
    check(workflow())


@pytest.mark.parametrize("bad", ["missing", "skip", "continue", "wrong-run", "working-directory", "duplicate",
                                 "move", "job-skip", "job-continue", "push-path", "pr-path"])
def test_disabled_or_untriggered_admission_proof_is_rejected(bad):
    value = deepcopy(workflow())
    job = value["jobs"]["synthetic"]
    step = next(row for row in job["steps"] if row.get("name") == NATIVE)
    if bad == "missing": job["steps"].remove(step)
    elif bad == "skip": step["if"] = "false"
    elif bad == "continue": step["continue-on-error"] = "true"
    elif bad == "wrong-run": step["run"] += " || true"
    elif bad == "working-directory": step["working-directory"] = "backend"
    elif bad == "duplicate": job["steps"].append(dict(step))
    elif bad == "move": job["steps"].insert(0, job["steps"].pop(job["steps"].index(step)))
    elif bad == "job-skip": job["if"] = "false"
    elif bad == "job-continue": job["continue-on-error"] = "true"
    elif bad == "push-path": value["on"]["push"]["paths"].remove("docs/working-runtime-admission.md")
    else: value["on"]["pull_request"]["paths"].remove("backend/tests/test_working_runtime_resources.py")
    with pytest.raises((AssertionError, ValueError)):
        check(value)


def test_checker_and_all_fixed_payload_inputs_are_exact_accepted_lf_bytes():
    assert entry.ADDITIONS[entry.CHECKER] == entry.CHECKER_SHA
    assert entry.ADDITIONS[entry.PAYLOAD] != "0" * 64
    assert len(entry.ADDITIONS) == 7
    for name, wanted in entry.ADDITIONS.items():
        assert hashlib.sha256((ROOT/name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == wanted
    assert hashlib.sha256((ROOT/".gitattributes").read_bytes()).hexdigest() == "d8e7c02e359d7166b2846337616608bd1bf7e45d5540df004efe5d060fea2cec"


def test_no_new_live_operation_or_generic_callback_interface_is_exported():
    assert entry.ENVIRONMENT == {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    assert entry.MAX_SECONDS == 40
    public = {name for name, value in vars(entry.RuntimeAdmission).items() if callable(value) and not name.startswith("_")}
    assert public == {"check", "snapshot", "close"}


def test_native_proof_independently_pins_the_reviewed_new_executable_inputs():
    file = ROOT / "ops/backup/tests/working_runtime_admission_proof.py"
    assignments = {target.id: node.value for node in ast.parse(file.read_bytes()).body
                   if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)}
    for key, relative in (("ENTRY_SHA", "ops/backup/working_runtime_admission.py"),
                          ("PAYLOAD_SHA", "ops/backup/working_runtime.py"),
                          ("HELPER_SHA", "ops/backup/tests/working_launch_profile_proof.py")):
        wanted = ast.literal_eval(assignments[key])
        assert hashlib.sha256((ROOT/relative).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == wanted
    cases = ast.literal_eval(assignments["CASES"])
    assert type(cases) is tuple and len(cases) == len(set(cases)) == 30
    assert {"custodian_positive", "writer_positive", "checker_substitution", "credential_fifo",
            "credential_hardlink", "credential_oversize", "state_mode644", "state_hardlink"} <= set(cases)
