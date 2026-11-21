"""Exact additive proof gates; failures, skips and partial runs cannot be PASS."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OFFLINE = "Operator review protocol and mandatory proof contracts (no admission)"
LINUX = "Synthetic Linux operator review (real kernel peer UIDs)"
PG = "Real PG16 committed DRAFT operator review (read-only role, no skips)"
CASES = 8


def load(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf8"), Loader=yaml.BaseLoader)


def one(job, name):
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def backup_check(value):
    assert value["permissions"] == {"contents": "read"}
    assert set(value["jobs"]) == {"synthetic"}
    job = value["jobs"]["synthetic"]
    assert not {"if", "continue-on-error"} & job.keys()
    assert one(job, OFFLINE) == dict(name=OFFLINE, **{"working-directory": "backend"},
        run="python -B -m pytest tests/test_erasure_operator.py tests/test_erasure_operator_ci.py -q")
    assert one(job, LINUX) == dict(name=LINUX,
        run='sudo -n -- "$(command -v python)" -I -B ops/backup/tests/erasure_operator_isolation_proof.py')
    names = [step["name"] for step in job["steps"]]
    assert names.index("Synthetic independent identity registry (no dispatch)") < names.index(OFFLINE)
    assert names.index(OFFLINE) < names.index(LINUX) < names.index(
        "Synthetic Linux custodian isolation (real distinct unprivileged UIDs)")
    required = {"backend/services/erasure_operator_review.py", "backend/tests/test_erasure_operator.py",
                "backend/tests/test_erasure_operator_review_pg.py", "backend/tests/test_erasure_operator_ci.py",
                "docs/erasure-operator-review.md", "ops/backup/**"}
    for event in ("push", "pull_request"):
        paths = set(value["on"][event]["paths"])
        assert required <= paths
        assert not {"*", "**", "backend/**", "ops/**", "docs/**"} & paths


def pg_program(value):
    assert value["permissions"] == {"contents": "read"}
    job = value["jobs"]["postgres-explain"]
    assert not {"if", "continue-on-error"} & job.keys()
    assert job["services"]["postgres"]["image"] == (
        "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777")
    assert job["timeout-minutes"] == "15"
    step = one(job, PG)
    assert set(step) == {"name", "env", "run"} and step["env"] == {"APP_ENV": "test"}
    lines = step["run"].splitlines()
    assert lines[0] == "python - <<'PY'" and lines[-1] == "PY"
    code = "\n".join(lines[1:-1])
    tree = ast.parse(code)
    assert "host='127.0.0.1', port=5432, user='postgres'" in code
    assert "database='pult_explain'" in code and "data == '/var/lib/postgresql/data'" in code
    assert "('erasure_operator_owner', 'erasure_operator_reader')" in code
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS" in code
    assert "'erasure_operator_test_' + uuid.uuid4().hex" in code
    assert "PULT_ERASURE_OPERATOR_TEST_URL=" in code and "PULT_ERASURE_OPERATOR_TEST_DATA=data" in code
    assert "PULT_ERASURE_OPERATOR_TEST_DATABASE_OID=str(oid)" in code
    assert "assert not report.exists()" in code
    assert not any(token in code for token in ("DROP ", "DATABASE_URL", "PULT_TEST_PG_URL", "shell=True", "|| true"))
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and ast.unparse(node.func) == "subprocess.run")
    assert isinstance(call.args[0], ast.List)
    items = call.args[0].elts
    assert ast.unparse(items[0]) == "sys.executable"
    assert [item.value for item in items[1:] if isinstance(item, ast.Constant)] == [
        "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/test_erasure_operator_review_pg.py"]
    assert next(key.value.value for key in call.keywords if key.arg == "timeout") == 90
    return tree


def verdict(exit_code, counts):
    tree = pg_program(load("constitutional_verification.yml"))
    assertions = [node for node in tree.body if isinstance(node, ast.Assert)]
    assert len(assertions) == 4
    # Only execute the actual three final verdict assertions, never CI bootstrap.
    code = compile(ast.Module(body=deepcopy(assertions[1:]), type_ignores=[]), "<operator-ci-verdict-only>", "exec")
    suites = [ElementTree.Element("testsuite", {key: str(value) for key, value in counts.items()})]
    exec(code, {"result": SimpleNamespace(returncode=exit_code), "suites": suites})


def test_exact_additive_mandatory_offline_linux_and_pg_gates():
    backup_check(load("backup_restore_synthetic.yml"))
    pg_program(load("constitutional_verification.yml"))
    verdict(0, dict(tests=CASES, errors=0, failures=0, skipped=0))


@pytest.mark.parametrize("name", [OFFLINE, LINUX])
@pytest.mark.parametrize("key,value", [("if", "false"), ("continue-on-error", "true"), ("run", "true")])
def test_missing_skipped_tolerated_or_substituted_proof_rejected(name, key, value):
    altered = load("backup_restore_synthetic.yml")
    one(altered["jobs"]["synthetic"], name)[key] = value
    with pytest.raises(AssertionError):
        backup_check(altered)


@pytest.mark.parametrize("key,value", [("if", "false"), ("continue-on-error", "true"), ("env", {})])
def test_pg_gate_cannot_be_conditional_or_lose_explicit_test_environment(key, value):
    altered = load("constitutional_verification.yml")
    one(altered["jobs"]["postgres-explain"], PG)[key] = value
    with pytest.raises(AssertionError):
        pg_program(altered)


@pytest.mark.parametrize("field,value", [("tests", 0), ("tests", CASES - 1), ("tests", CASES + 1),
                                         ("errors", 1), ("failures", 1), ("skipped", 1)])
def test_pg_partial_failed_or_skipped_result_rejected(field, value):
    counts = dict(tests=CASES, errors=0, failures=0, skipped=0)
    counts[field] = value
    with pytest.raises(AssertionError):
        verdict(0, counts)


def test_nonzero_process_result_rejected_even_with_complete_xml():
    with pytest.raises(AssertionError):
        verdict(1, dict(tests=CASES, errors=0, failures=0, skipped=0))
