"""Static dormant source/CI contracts; never import or execute v2 candidates."""

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shlex
import textwrap

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "ops/backup/custodian_v2"
WORKFLOW = ROOT / ".github/workflows/backup_restore_synthetic.yml"
GUARD_PATH = "backend/tests/test_custodian_v2_source_ci.py"
SECURITY_INPUT_PATHS = {".gitattributes", "docs/erasure-custodian-v2-source-ci.md"}
WHEEL_DIR = "ops/backup/tests/custodian_v2/wheels"
LOCK = "ops/backup/custodian/requirements.lock"
GATES = tuple(f"ops/backup/tests/custodian_v2/{suite}/run_gate.py" for suite in ("durable", "cold"))
PREFIX = ["sudo", "-n", "--", "unshare", "--pid", "--fork", "--mount-proc", "--net",
          "/usr/bin/python3.12", "-I", "-S", "-B"]
GUARD_STEP = "Guard dormant custodian v2 source and CI contracts"
WHEEL_STEP = "Download seven hash-pinned custodian v2 proof wheels"
NATIVE_STEP = "Native isolated custodian v2 durable and cold proofs"
OPTIMIZED_STEP = "Reject optimized custodian v2 gate runtimes with exact reason"
HOST_STEP = "Synthetic custodian host preflight (minimal pinned runtime, no external IO)"
BUILD_STEP = "Build backup runner image (pinned base + pinned rclone/age, hash-verified)"
LEGACY_PINS = {
    "ops/backup/erasure_anchor.py": "b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8",
    "ops/backup/erasure_custodian.py": "3eb793a1ef1002c6def97b52c579b2ff051e834b44b802a75aca09d640a691d3",
    "ops/backup/erasure_custodian_host.py": "d7bdd8a858417ea20666bdb6fe5840ead124dec2a98d98ebb6e0d74eedfcdb24",
    "ops/backup/erasure_journal.py": "94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98",
    "ops/backup/erasure_journal_s3.py": "41c8da982e016730c740f826fa0ff1ebf00923d8f067fb86757783fc0773ca1d",
    "ops/canary/canary.py": "aa4b357acc6e908341c5523db8f93a32fdf3c0305a7ddc36c4c68842fbe3ee4f",
    LOCK: "cd1f0a681aae91a132427b88897782b0f2085c8a415cd39812f8e7a059361f08",
}
REVIEWED_PINS = LEGACY_PINS | {
    "ops/backup/erasure_journal_s3.py": "2886ec08fe17eb409d8d4a8c597015454c21f3ef703c674a7848f30b63bc544f",
}
CANDIDATES = {"candidate/anchor_v2.py", "candidate/protocol_v2.py", "codec/event_codec.py",
              "store/event_store.py", "ownership/owned_host.py", "ownership/host_core.py"}
WHEELS = {
    "anyio-4.14.2-py3-none-any.whl": "9f505dda5ac9f0c8309b5e8bd445a8c2bf7246f3ce950121e45ea15bc41d1494",
    "certifi-2026.10.19-py3-none-any.whl": "42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b",
    "h11-0.16.0-py3-none-any.whl": "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86",
    "httpcore-1.0.9-py3-none-any.whl": "2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55",
    "httpx-0.28.1-py3-none-any.whl": "d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad",
    "idna-3.18-py3-none-any.whl": "7f952cbe720b688055e3f87de14f5c3e5fdaa8bc3928985c4077ca689de849a2",
    "typing_extensions-4.16.0-py3-none-any.whl": "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
}
OPTIMIZED_DRIVER = textwrap.dedent('''\
    import json
    import subprocess

    gates = (
        "ops/backup/tests/custodian_v2/durable/run_gate.py",
        "ops/backup/tests/custodian_v2/cold/run_gate.py",
    )
    for gate in gates:
        result = subprocess.run(
            ["sudo", "-n", "--", "unshare", "--pid", "--fork", "--mount-proc", "--net",
             "/usr/bin/python3.12", "-I", "-S", "-B", "-O", gate, "--reviewed-by-main"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        try:
            report = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise SystemExit("optimized gate did not return refusal JSON") from None
        if (result.returncode != 2 or result.stderr
                or report != {"outcome": "NOT_RUN", "reason": "optimized_test_runtime_refused"}):
            raise SystemExit("optimized gate did not return the exact refusal")
''')


def _workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _assert_ci(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["master"]
    for event in ("pull_request", "push"):
        paths = workflow["on"][event]["paths"]
        assert {GUARD_PATH, "ops/backup/**"} | SECURITY_INPUT_PATHS <= set(paths)
        assert not {"*", "**", "backend/**", "ops/**", "docs/**"}.intersection(paths)
    assert set(workflow["jobs"]) == {"synthetic"}
    job = workflow["jobs"]["synthetic"]
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == "25"
    assert not {"if", "continue-on-error", "defaults", "container", "services", "strategy"}.intersection(job)
    steps = job["steps"]
    names = [step["name"] for step in steps]
    assert len(names) == len(set(names))
    by_name = {step["name"]: step for step in steps}
    assert by_name["Set up Python"]["with"]["python-version"] == "3.13"
    added = [GUARD_STEP, WHEEL_STEP, NATIVE_STEP, OPTIMIZED_STEP]
    start = names.index(HOST_STEP) + 1
    assert names[start:start + len(added) + 1] == added + [BUILD_STEP]
    for name in added:
        assert set(by_name[name]) == {"name", "run"}
    assert shlex.split(by_name[GUARD_STEP]["run"]) == ["python", "-m", "pytest", GUARD_PATH, "-q"]
    assert by_name[WHEEL_STEP]["run"].splitlines() == [
        "set -euo pipefail", "test -x /usr/bin/python3.12",
        f"python -m pip download --only-binary=:all: --no-deps --require-hashes -r {LOCK} -d {WHEEL_DIR}",
    ]
    native = by_name[NATIVE_STEP]["run"].splitlines()
    assert native[0] == "set -euo pipefail" and len(native) == 3
    assert [shlex.split(line) for line in native[1:]] == [PREFIX + [gate, "--reviewed-by-main"] for gate in GATES]
    negative = by_name[OPTIMIZED_STEP]["run"].splitlines()
    assert negative[:2] == ["set -euo pipefail", "python - <<'PY'"] and negative[-1] == "PY"
    actual = ast.parse("\n".join(negative[2:-1]))
    assert ast.dump(actual) == ast.dump(ast.parse(OPTIMIZED_DRIVER))
    for step in steps:
        if "uses" in step:
            assert re.search(r"@[0-9a-f]{40}$", step["uses"])


def _literal_assignment(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    assert len(values) == 1
    return ast.literal_eval(values[0])


def _assert_draft(profile):
    assert profile["format"] == "pult-schema2-cold-source-profile-preparation-v1"
    assert profile["status"] == "DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED"
    assert profile["admitted_operations"] == [] and profile["operator_approval"] is None
    for key in ("working_credentials_included", "database_or_pending_included", "installation_service_or_network_actions"):
        assert profile[key] is False
    runtime = profile["runtime"]
    assert (runtime["implementation"], runtime["major_minor"], runtime["platform"]) == ("cpython", [3, 12], "linux")
    assert set(profile["sources"]) == CANDIDATES | {"frozen/" + name for name in REVIEWED_PINS}
    for name, digest in REVIEWED_PINS.items():
        assert profile["sources"]["frozen/" + name] == digest


def test_source_profile_is_draft_and_every_listed_source_matches_its_pin():
    body = (V2 / "source-profile.json").read_bytes()
    profile = json.loads(body)
    _assert_draft(profile)
    for name, digest in profile["sources"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        if name.startswith("frozen/") and name != "frozen/ops/backup/erasure_journal_s3.py":
            path = ROOT / name.removeprefix("frozen/")
        else:
            path = V2 / name
        source = path.read_bytes()
        assert hashlib.sha256(source).hexdigest() == digest, name
        if name.endswith(".py"):
            ast.parse(source, filename=name)
    cold = V2 / "cold/cold_host.py"
    assert _literal_assignment(cold, "CATALOG_SHA") == hashlib.sha256(body).hexdigest()
    assert _literal_assignment(cold, "WHEELS") == WHEELS
    assert _literal_assignment(V2 / "ownership/owned_host.py", "GUARD_SHA") == profile["sources"]["ownership/host_core.py"]
    lock = (ROOT / LOCK).read_text(encoding="ascii")
    for digest in WHEELS.values():
        assert "--hash=sha256:" + digest in lock


def test_legacy_seven_sources_remain_byte_identical_to_origin_baseline():
    for name, digest in LEGACY_PINS.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name


def test_workflow_enforces_native_namespaces_pinned_wheels_and_exact_optimized_refusals():
    _assert_ci(_workflow())


def test_gate_sources_preserve_root_pid1_loopback_numeric_identities_and_optimization_refusal():
    def shape(source):
        return ast.dump(ast.parse(source, mode="eval").body)

    for gate in GATES:
        path = ROOT / gate
        tree = ast.parse(path.read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        conditions = set()

        def conjuncts(node):
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
                for value in node.values:
                    conjuncts(value)
            else:
                conditions.add(ast.dump(node))

        for node in ast.walk(main):
            if isinstance(node, ast.Assert):
                conjuncts(node.test)
        for required in ("sys.platform == 'linux'", "sys.version_info[:2] == (3, 12)",
                         "os.geteuid() == 0", "os.getpid() == 1",
                         "[line.split(':', 1)[0].strip() for line in "
                         "Path('/proc/self/net/dev').read_text().splitlines()[2:]] == ['lo']"):
            assert shape(required) in conditions, gate
        refusal = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                   and ast.dump(node.test) == shape("sys.flags.optimize")]
        assert len(refusal) == 1
        messages = [node.args[0].value for statement in refusal[0].body for node in ast.walk(statement)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
                    and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)]
        assert len(messages) == 1
        assert json.loads(messages[0]) == {"outcome": "NOT_RUN", "reason": "optimized_test_runtime_refused"}
        exits = [node for statement in refusal[0].body for node in ast.walk(statement)
                 if isinstance(node, (ast.Return, ast.Raise))]
        assert len(exits) == 1
        if isinstance(exits[0], ast.Return):
            assert ast.literal_eval(exits[0].value) == 2
        else:
            assert ast.dump(exits[0].exc) == shape("SystemExit(2)")
        if "/durable/" in gate:
            assert _literal_assignment(path, "UID") == 47003
            assert _literal_assignment(path, "GID") == 47004
        else:
            identities = shape("((pwd.getpwuid, 47003), (grp.getgrgid, 47004))")
            assert identities in {ast.dump(node) for node in ast.walk(main)}


@pytest.mark.parametrize("step,old,new", [
    (WHEEL_STEP, "--require-hashes ", ""),
    (WHEEL_STEP, "--only-binary=:all: ", ""),
    (WHEEL_STEP, "--no-deps ", ""),
    (WHEEL_STEP, WHEEL_DIR, "/tmp/wheels"),
    (NATIVE_STEP, "sudo -n -- ", ""),
    (NATIVE_STEP, "--pid ", ""),
    (NATIVE_STEP, "--fork ", ""),
    (NATIVE_STEP, "--mount-proc ", ""),
    (NATIVE_STEP, "--net ", ""),
    (NATIVE_STEP, "/usr/bin/python3.12", "python"),
    (NATIVE_STEP, "-S ", ""),
    (NATIVE_STEP, "--reviewed-by-main", ""),
    (NATIVE_STEP, GATES[1], GATES[0]),
    (OPTIMIZED_STEP, '"-O", ', ""),
    (OPTIMIZED_STEP, "result.returncode != 2", "False"),
    (OPTIMIZED_STEP, "result.stderr", "False"),
    (OPTIMIZED_STEP,
     'report != {"outcome": "NOT_RUN", "reason": "optimized_test_runtime_refused"}',
     'report.get("reason") != "optimized_test_runtime_refused"'),
    (OPTIMIZED_STEP, "optimized_test_runtime_refused", "arbitrary_crash"),
    (OPTIMIZED_STEP, GATES[1], GATES[0]),
])
def test_ci_guard_rejects_command_and_refusal_mutations(step, old, new):
    workflow = _workflow()
    target = next(item for item in workflow["jobs"]["synthetic"]["steps"] if item["name"] == step)
    assert old in target["run"]
    target["run"] = target["run"].replace(old, new)
    with pytest.raises(AssertionError):
        _assert_ci(workflow)


@pytest.mark.parametrize("event", ["pull_request", "push"])
@pytest.mark.parametrize("path", sorted(SECURITY_INPUT_PATHS))
@pytest.mark.parametrize("replacement", [None, "**"])
def test_ci_guard_requires_exact_security_input_trigger_paths(event, path, replacement):
    workflow = _workflow()
    paths = workflow["on"][event]["paths"]
    paths.remove(path)
    if replacement is not None:
        paths.append(replacement)
    with pytest.raises(AssertionError):
        _assert_ci(workflow)


@pytest.mark.parametrize("key,value", [("status", "APPROVED"), ("operator_approval", "yes"),
    ("admitted_operations", ["serve"]), ("working_credentials_included", True),
    ("database_or_pending_included", True), ("installation_service_or_network_actions", True)])
def test_draft_guard_rejects_activation_claims(key, value):
    profile = deepcopy(json.loads((V2 / "source-profile.json").read_bytes()))
    profile[key] = value
    with pytest.raises(AssertionError):
        _assert_draft(profile)
