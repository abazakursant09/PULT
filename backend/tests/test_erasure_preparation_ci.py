"""Offline contract of the mandatory owned-PG gate; no CI/service execution."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT/'.github/workflows/constitutional_verification.yml'
NAME = 'Real PG16 erasure DRAFT concurrency (owned isolated database, no skips)'
IMAGE = 'postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777'


def gate():
    document = yaml.load(WORKFLOW.read_text(encoding='utf8'), Loader=yaml.BaseLoader)
    job = document['jobs']['postgres-explain']
    matches = [s for s in job['steps'] if s.get('name') == NAME]
    assert len(matches) == 1
    return document, job, matches[0]


def program():
    step = gate()[2]
    lines = step['run'].splitlines()
    assert lines[0] == "python - <<'PY'" and lines[-1] == 'PY'
    return '\n'.join(lines[1:-1])


def test_existing_read_only_job_service_and_locked_dependencies_preserved():
    document, job, step = gate()
    assert document['permissions'] == {'contents': 'read'}
    assert job['services']['postgres']['image'] == IMAGE
    assert job['timeout-minutes'] == '15'
    assert step['env'] == {'APP_ENV': 'test'}
    assert not {'if', 'continue-on-error'} & step.keys()
    assert any(s.get('run') == 'pip install --require-hashes -r requirements-ci.lock' for s in job['steps'])


def test_ci_setup_is_only_the_pinned_service_and_new_nonce_database():
    code = program()
    ast.parse(code)
    assert "host='127.0.0.1', port=5432, user='postgres'" in code
    assert "database='pult_explain'" in code
    assert "data == '/var/lib/postgresql/data'" in code
    assert "'erasure_prep_test_' + uuid.uuid4().hex" in code
    assert 'CREATE ROLE erasure_prep_owner' in code and 'CREATE DATABASE ' in code
    assert 'PULT_ERASURE_PREP_TEST_URL=' in code and 'PULT_ERASURE_PREP_TEST_DATA=data' in code
    assert 'PULT_ERASURE_PREP_TEST_DATABASE_OID=str(oid)' in code
    assert 'PULT_TEST_PG_URL' not in code and 'DATABASE_URL' not in code
    assert not any(token in code for token in ('DROP ', 'secrets.', 'continue-on-error', 'shell=True', '|| true'))


def test_exact_three_tests_use_the_same_interpreter_and_bounded_subprocess():
    tree = ast.parse(program())
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == 'subprocess' and n.func.attr == 'run')
    assert isinstance(call.args[0], ast.List)
    items = call.args[0].elts
    assert ast.unparse(items[0]) == 'sys.executable'
    constants = [n.value for n in items[1:] if isinstance(n, ast.Constant)]
    assert constants == ['-B', '-m', 'pytest', '-p', 'no:cacheprovider', '-q', 'tests/test_erasure_preparation_pg.py']
    assert next(k.value.value for k in call.keywords if k.arg == 'timeout') == 90
    assert '--junitxml=' in program()


def enforce_result(exit_code, counts):
    tree = ast.parse(program())
    # Execute only the actual final verdict assertions, never provisioning/code.
    assertions = [n for n in tree.body if isinstance(n, ast.Assert)]
    assert len(assertions) == 3
    code = compile(ast.Module(body=deepcopy(assertions), type_ignores=[]), '<ci-verdict-assertions-only>', 'exec')
    suites = [ElementTree.Element('testsuite', {k: str(v) for k, v in counts.items()})]
    exec(code, {'result': SimpleNamespace(returncode=exit_code), 'suites': suites})


def test_ci_verdict_accepts_only_complete_zero_skip_three_case_proof():
    enforce_result(0, dict(tests=3, errors=0, failures=0, skipped=0))


@pytest.mark.parametrize('field,value', [('tests', 0), ('tests', 2), ('tests', 4), ('errors', 1), ('failures', 1), ('skipped', 1)])
def test_partial_error_or_skip_cannot_satisfy_ci_verdict(field, value):
    counts = dict(tests=3, errors=0, failures=0, skipped=0)
    counts[field] = value
    with pytest.raises(AssertionError):
        enforce_result(0, counts)


def test_process_failure_cannot_satisfy_ci_verdict():
    with pytest.raises(AssertionError):
        enforce_result(1, dict(tests=3, errors=0, failures=0, skipped=0))
