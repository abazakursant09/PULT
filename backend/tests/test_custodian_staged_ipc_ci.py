"""Offline staged packaging contracts; never import or execute product candidates."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / 'ops/backup/tests/custodian_v2/staged'
SOURCE = ROOT / 'ops/backup/custodian_v2/staged'
WORKFLOW = '.github/workflows/custodian_staged_ipc.yml'
GUARD = 'backend/tests/test_custodian_staged_ipc_ci.py'
ENTRY = 'ops/backup/tests/custodian_v2/staged/'
SOURCE_SHA = 'f8010c39d6162dcac44b5e20cef1e79323c0e88a2e8a01eac34e8fe9163dbf02'
LIBRARIES = {'ci_gate.py': 'd30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b',
             'scope_control.py': 'd46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'}
FILES = ('ci_gate.py', 'run_gate.py', 'verify_receipt.py', 'worker.py')
PATHS = ['ops/backup/**', 'ops/canary/canary.py', GUARD, WORKFLOW, '.gitattributes']
REFUSALS = {'ci_gate.py': 'optimized_staged_ci_refused', 'run_gate.py': 'optimized_ipc_gate_refused',
            'verify_receipt.py': 'optimized_staged_verifier_refused'}
PREFIX = 'sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B '
NAMES = ['Checkout repository', 'Set up Python', 'Install locked CI dependencies',
         'Guard dormant staged IPC sources and workflow', 'Download seven hash-pinned synthetic wheels',
         'Native staged IPC and independent postcheck', 'Reject optimized staged runtimes']
COMMANDS = {2: 'python -m pip install --require-hashes -r backend/requirements-ci.lock',
            3: 'python -m pytest ' + GUARD + ' -q',
            4: 'set -euo pipefail\ntest -x /usr/bin/python3.12\npython -m pip download --only-binary=:all: --no-deps --require-hashes -r ops/backup/custodian/requirements.lock -d ops/backup/tests/custodian_v2/wheels',
            5: PREFIX + ENTRY + 'ci_gate.py --reviewed-by-main'}
OPTIMIZED = '''\
import json
import subprocess
root = "ops/backup/tests/custodian_v2/staged/"
for file, reason in (("ci_gate.py", "optimized_staged_ci_refused"), ("run_gate.py", "optimized_ipc_gate_refused"), ("verify_receipt.py", "optimized_staged_verifier_refused")):
    result = subprocess.run(["sudo", "-n", "--", "unshare", "--net", "/usr/bin/python3.12", "-I", "-S", "-B", "-O", root + file, "--reviewed-by-main"], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 2 or result.stderr or json.loads(result.stdout) != {"outcome": "NOT_RUN", "reason": reason}:
        raise SystemExit("optimized staged runtime did not return exact refusal")
'''


def shape(source):
    return ast.dump(ast.parse(source))


def literal(tree, name):
    nodes = [n.value for n in tree.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    assert len(nodes) == 1
    return ast.literal_eval(nodes[0])


def require_statements(tree, *statements):
    nodes = {ast.dump(n) for n in ast.walk(tree)}
    for statement in statements:
        assert ast.dump(ast.parse(statement).body[0]) in nodes, statement


def assert_workflow(value):
    assert set(value) == {'name', 'on', 'permissions', 'jobs'} and value['name'] == 'Custodian Staged IPC'
    assert value['permissions'] == {'contents': 'read'}
    assert value['on'] == {'pull_request': {'paths': PATHS}, 'push': {'branches': ['master'], 'paths': PATHS}, 'workflow_dispatch': ''}
    assert set(value['jobs']) == {'staged-ipc'}
    job = value['jobs']['staged-ipc']
    assert set(job) == {'runs-on', 'timeout-minutes', 'steps'}
    assert job['runs-on'] == 'ubuntu-24.04' and job['timeout-minutes'] == '20'
    steps = job['steps']
    assert [s['name'] for s in steps] == NAMES
    for index, action, settings in [(0, 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262', {'persist-credentials': 'false'}),
                                    (1, 'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065', {'python-version': '3.13'})]:
        assert steps[index] == {'name': NAMES[index], 'uses': action, 'with': settings}
    for index in range(2, 7):
        assert set(steps[index]) == {'name', 'run'}  # No skip, continue, services, credentials or uploads.
        if index in COMMANDS:
            assert steps[index]['run'].strip() == COMMANDS[index]
    lines = steps[6]['run'].splitlines()
    assert lines[:2] == ['set -euo pipefail', "python - <<'PY'"] and lines[-1] == 'PY'
    assert shape('\n'.join(lines[2:-1])) == shape(OPTIMIZED)


def assert_draft(value):
    assert value == dict(format='pult-staged-ipc-source-preparation-v1', status='DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED',
        operator_approval=None, admitted_operations=[], working_credentials_included=False, database_or_pending_included=False,
        installation_service_or_network_actions=False, scope='LOCAL_SYNTHETIC_AUTHENTICATED_IPC_NOT_TRUSTED_WRITABLE_HOST_OR_LIVE_S3',
        runtime=dict(implementation='cpython', major_minor=[3, 12], platform='linux'), sources={'staged_ipc.py': SOURCE_SHA})
    for key in ('working_credentials_included', 'database_or_pending_included', 'installation_service_or_network_actions'):
        assert value[key] is False


def assert_entry(source, file):
    tree = ast.parse(source)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    if isinstance(main.body[0], ast.Global):
        main.body = main.body[1:]
    refusal = ast.parse('if sys.flags.optimize:\n    print(' + repr(json.dumps(
        dict(outcome='NOT_RUN', reason=REFUSALS[file]), separators=(',', ':'))) + ');return 2').body[0]
    assert ast.dump(main.body[0]) == ast.dump(refusal)
    return tree, main


def assert_ci(source):
    tree, main = assert_entry(source, 'ci_gate.py')
    assert literal(tree, 'PINS') == LIBRARIES
    assert {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} == {'captured', 'verifier', 'main'}
    capture = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'captured')
    required = ast.parse("raw=file.read_bytes()\nif hashlib.sha256(raw).hexdigest()!=wanted: raise RuntimeError('FIXED_CI_LIBRARY_REFUSED')").body
    assert [ast.dump(n) for n in capture.body[:2]] == [ast.dump(n) for n in required]
    require_statements(capture, "exec(compile(raw,str(file),'exec'),module.__dict__)")
    block = next(n for n in main.body if isinstance(n, ast.Try))
    expected = ast.parse("with supervisor.create_scope(control) as scope:\n    result,clean,reason=supervisor.supervised_run(scope,control,ROOT/'run_gate.py')").body[0]
    assert len(block.body) == 1 and ast.dump(block.body[0]) == ast.dump(expected)
    require_statements(main, "control=captured(library/'scope_control.py',PINS['scope_control.py'],'staged_fixed_scope')",
        "supervisor=captured(library/'ci_gate.py',PINS['ci_gate.py'],'staged_fixed_supervisor')",
        "if reason or not clean or result is None:\n    print(json.dumps(dict(outcome='FAIL',reason=reason or 'staged_run_incomplete',owned_resources_empty=clean)));return 1",
        "if result.returncode or result.stderr: return 1", "receipt_sha=verifier().verify(file)",
        "if before!={file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in ROOT.glob('*.py')}: return 1")


GATE_STATEMENTS = [
    "REPO=HERE.parents[4]", "assert sys.platform=='linux' and os.geteuid()==0 and os.getpid()==1 and sys.version_info[:2]==(3,12)",
    "os.execv('/usr/bin/unshare',['unshare','--pid','--fork','--mount-proc','--net','/usr/bin/python3.12','-I','-S','-B',str(HERE/'run_gate.py'),'--reviewed-by-main'])",
    "assert [row.split(':',1)[0].strip() for row in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']",
    "assert ROOT.parent==Path(scope['work']) and ROOT.name.startswith('p-')", "assert not loader.errors and count==30",
    "passed=(count==result.testsRun==30 and result.wasSuccessful() and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses and not issues and not remaining and before==after and not ROOT.exists())",
    "before={name:sha(file) for name,file in inputs.items()}", "after={name:sha(file) for name,file in inputs.items()}",
    "before.update(helper.input_hashes())", "after.update(helper.input_hashes())",
    "self.assertEqual(meta[1],expected_head.digest)", "if objects: self.assertEqual(objects,{'00000000000000000001.json':expected_body})",
    "self.assertIsNone(answer['head'])", "self.assertEqual(records['owner']['rights_fd_delta'],0)",
    "expected_body,target=FIXTURE.reference(FIXTURE.reference(),FIXTURE.event())",
    "self.assertEqual(json.dumps(answer['head'],sort_keys=True),json.dumps({'sequence':target.sequence,'digest':target.digest},sort_keys=True))",
    "self.assertIs(answer['purge_or_restore_admitted'],False)",
    "self.assertEqual(json.loads(os.read(result_read,1024)),{'accepted':False,'fd_delta':0})",
    "self.assertFalse(flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC))",
    "for fd in retained+[go_read,go_write,ready_read,ready_write,result_read,result_write]:\n    try: os.close(fd)\n    except OSError: pass",
    "self.assertTrue(writer['reader_denied'])", "self.assertTrue(writer['db_denied'])", "self.assertTrue(records['owner']['writer_denied'])"]


def assert_gate(source):
    tree, _ = assert_entry(source, 'run_gate.py')
    assert literal(tree, 'SUPERVISOR_SHA') == LIBRARIES['ci_gate.py'] and literal(tree, 'SCOPE_SHA') == LIBRARIES['scope_control.py']
    assert len(literal(tree, 'CASES')) == 28
    require_statements(tree, *GATE_STATEMENTS)
    contexts = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'TemporaryDirectory']
    assert len(contexts) == 1 and shape(ast.unparse(contexts[0])) == shape("tempfile.TemporaryDirectory(prefix='p-',dir=scope['work'])")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not re.search(r'custodian-[a-z0-9-]+-20[0-9]{6}|^[A-Za-z]:[\\/]|/mnt/c/|/home/', node.value)


def workflow():
    return yaml.load((ROOT / WORKFLOW).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def test_exact_dormant_sources_libraries_and_workflow():
    assert_draft(json.loads((SOURCE / 'source-profile.json').read_bytes()))
    assert hashlib.sha256((SOURCE / 'staged_ipc.py').read_bytes()).hexdigest() == SOURCE_SHA
    assert {p.name for p in SOURCE.iterdir()} == {'staged_ipc.py', 'source-profile.json'}
    assert {p.name for p in TESTS.glob('*.py')} == set(FILES)
    for name, wanted in LIBRARIES.items():
        assert hashlib.sha256((TESTS.parent / 'controller' / name).read_bytes()).hexdigest() == wanted
    wheels = literal(ast.parse((ROOT / 'ops/backup/custodian_v2/cold/cold_host.py').read_bytes()), 'WHEELS')
    assert len(wheels) == 7
    lock = (ROOT / 'ops/backup/custodian/requirements.lock').read_text(encoding='ascii')
    assert all('--hash=sha256:' + digest in lock for digest in wheels.values())
    assert_workflow(workflow())


def test_raw_source_pin_rejects_even_whitespace_changes():
    body = (SOURCE / 'staged_ipc.py').read_bytes()
    assert hashlib.sha256(body).hexdigest() == SOURCE_SHA
    assert hashlib.sha256(body + b'\n').hexdigest() != SOURCE_SHA


def assert_input_closure(gate_source, verifier_source):
    require_statements(ast.parse(gate_source), "inputs['catalogue']=SOURCE/'source-profile.json'",
                       "inputs['cold_metadata']=SOURCE/'cold/cold_host.py'")
    require_statements(ast.parse(verifier_source),
        "require(len(paths)==32 and set(paths)==set(value['source_hashes_before']))",
        "require(hashlib.sha256((source/'source-profile.json').read_bytes()).hexdigest()=='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3')",
        "require(hashlib.sha256((source/'cold/cold_host.py').read_bytes()).hexdigest()=='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c')",
        "paths.update({'staged_ipc.py':source/'staged/staged_ipc.py','draft':source/'staged/source-profile.json',"
        "'catalogue':source/'source-profile.json','cold_metadata':source/'cold/cold_host.py',"
        "'fixture':tests/'durable/crash_worker.py','helper':tests/'support.py',"
        "'supervisor':tests/'controller/ci_gate.py','scope':tests/'controller/scope_control.py'})",
        "require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and 0<info.st_size<1048576)")


def test_native_and_supervised_contracts():
    assert_ci((TESTS / 'ci_gate.py').read_text(encoding='utf-8'))
    gate_source = (TESTS / 'run_gate.py').read_text(encoding='utf-8')
    verifier_source = (TESTS / 'verify_receipt.py').read_text(encoding='utf-8')
    assert_gate(gate_source); assert_input_closure(gate_source, verifier_source)
    require_statements(ast.parse(verifier_source),
        "for name,path in paths.items(): require(hashlib.sha256(path.read_bytes()).hexdigest()==value['source_hashes_before'][name])")


@pytest.mark.parametrize('file,old,new', [('run_gate.py', "inputs['catalogue']=SOURCE/'source-profile.json'", 'pass'),
    ('run_gate.py', "inputs['cold_metadata']=SOURCE/'cold/cold_host.py'", 'pass'),
    ('verify_receipt.py', 'len(paths)==32', 'len(paths)==30'), ('verify_receipt.py', 'len(paths)==32', 'len(paths)==31'),
    ('verify_receipt.py', "'catalogue':source/'source-profile.json',", ''),
    ('verify_receipt.py', "'cold_metadata':source/'cold/cold_host.py',", ''),
    ('verify_receipt.py', '091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c', '0' * 64),
    ('verify_receipt.py', 'info.st_uid==0', 'info.st_uid==1000')])
def test_closure_cannot_drop_metadata_inputs_or_accept_drvfs_receipt_owner(file, old, new):
    sources = {name: (TESTS / name).read_text(encoding='utf-8') for name in ('run_gate.py', 'verify_receipt.py')}
    assert_input_closure(sources['run_gate.py'], sources['verify_receipt.py']); assert old in sources[file]
    sources[file] = sources[file].replace(old, new)
    with pytest.raises(AssertionError): assert_input_closure(sources['run_gate.py'], sources['verify_receipt.py'])


@pytest.mark.parametrize('file', REFUSALS)
def test_optimization_refusal_is_first_executable_action(file):
    source = (TESTS / file).read_text(encoding='utf-8'); assert_entry(source, file)
    with pytest.raises(AssertionError):
        assert_entry(source.replace('if sys.flags.optimize:', "print('work started')\n    if sys.flags.optimize:", 1), file)


@pytest.mark.parametrize('index,old,new', [(5, '--net ', ''), (5, '-I ', ''), (5, '-S ', ''), (5, '-B ', ''),
    (5, 'unshare --net', 'unshare --pid --net'), (5, 'ci_gate.py', 'run_gate.py'),
    (4, '--require-hashes ', ''), (4, '--no-deps ', ''), (4, '--only-binary=:all: ', ''),
    (2, '--require-hashes ', ''), (6, 'result.returncode != 2', 'False'), (6, 'result.stderr', 'False'),
    (6, '"-O", ', ''), (6, '"-S", ', ''), (6, 'optimized_staged_verifier_refused', 'arbitrary_failure')])
def test_workflow_command_mutations(index, old, new):
    value = workflow(); step = value['jobs']['staged-ipc']['steps'][index]; assert old in step['run']
    step['run'] = step['run'].replace(old, new)
    with pytest.raises(AssertionError): assert_workflow(value)


@pytest.mark.parametrize('mutation', ['paths', 'permissions', 'services', 'skip', 'continue', 'upload', 'credentials', 'unpinned', 'timeout'])
def test_workflow_boundary_mutations(mutation):
    value = workflow(); job = value['jobs']['staged-ipc']; steps = job['steps']
    if mutation == 'paths': value['on']['push']['paths'].remove(GUARD)
    elif mutation == 'permissions': value['permissions']['contents'] = 'write'
    elif mutation == 'services': job['services'] = {'cloud': {}}
    elif mutation in ('skip', 'continue'): steps[5]['if' if mutation == 'skip' else 'continue-on-error'] = 'true'
    elif mutation == 'upload': steps.append({'name': 'Upload', 'uses': 'actions/upload-artifact@' + '0' * 40})
    elif mutation == 'credentials': steps[0]['with']['persist-credentials'] = 'true'
    elif mutation == 'unpinned': steps[0]['uses'] = 'actions/checkout@v4'
    else: job['timeout-minutes'] = '21'
    with pytest.raises(AssertionError): assert_workflow(value)


@pytest.mark.parametrize('file,old,new', [('ci_gate.py', 'hashlib.sha256(raw).hexdigest()!=wanted', 'False'),
    ('ci_gate.py', 'raw=file.read_bytes()', 'raw=file.read_text()'), ('ci_gate.py', 'with supervisor.create_scope(control) as scope:', 'if True:'),
    ('ci_gate.py', 'supervisor.supervised_run(scope,control,', 'supervisor.unbounded_run(scope,control,'),
    ('ci_gate.py', 'receipt_sha=verifier().verify(file)', "receipt_sha='fake'"), ('ci_gate.py', 'reason or not clean or result is None', 'False'),
    ('run_gate.py', 'count==30', 'count==29'), ('run_gate.py', 'not result.skipped', 'True'),
    ('run_gate.py', 'not result.expectedFailures', 'True'), ('run_gate.py', 'not result.unexpectedSuccesses', 'True'),
    ('run_gate.py', 'before==after', 'True'), ('run_gate.py', "dir=scope['work']", "dir='/tmp'"),
    ('run_gate.py', 'os.getpid()==1', 'True'), ('run_gate.py', "==['lo']", "==['lo','eth0']"),
    ('run_gate.py', 'self.assertIsNone(answer[\'head\'])', 'pass'), ('run_gate.py', 'self.assertEqual(meta[1],expected_head.digest)', 'pass')])
def test_native_contract_mutations(file, old, new):
    source = (TESTS / file).read_text(encoding='utf-8'); check = assert_ci if file == 'ci_gate.py' else assert_gate
    check(source); assert old in source
    with pytest.raises(AssertionError): check(source.replace(old, new))


def report():
    hashes = {'staged_ipc.py': SOURCE_SHA}
    return dict(format='pult-staged-ipc-native-proof-v1', outcome='PASS', full_gate=True,
        scope='LOCAL_SYNTHETIC_AUTHENTICATED_IPC_NOT_LIVE_S3_HOST_ENTRY_OR_OUTER_WRITE_STOP', tests=30, discovered=30,
        errors=0, failures=0, skips=0, expected_failures=0, unexpected_successes=0, errors_detail=[], failures_detail=[], issues=[],
        source_hashes_before=hashes, source_hashes_after=dict(hashes), sources_unchanged=True, private_pid_namespace=True,
        remaining_owned_processes=[], native_test_tree_removed=True, elapsed_seconds=100.0, new_paid_resources=0, additional_spend_rub=0)


@pytest.fixture(scope='module')
def verifier():
    path = TESTS / 'verify_receipt.py'; tree = ast.parse(path.read_bytes())
    assert literal(tree, 'FIELDS') == set(report())
    spec = importlib.util.spec_from_file_location('offline_staged_receipt', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)  # Stdlib-only pure helper; never verify().
    return module


@pytest.mark.parametrize('elapsed', [0, 900, 100.5])
def test_exact_complete_summary(verifier, elapsed):
    value = report(); value['elapsed_seconds'] = elapsed; assert verifier.valid_summary(value) is True


@pytest.mark.parametrize('key,value', [(k, bad) for k in ('tests', 'discovered') for bad in (29, 31, True, 30.0)]
    + [(k, bad) for k in ('errors', 'failures', 'skips', 'expected_failures', 'unexpected_successes', 'new_paid_resources', 'additional_spend_rub') for bad in (1, False)]
    + [(k, 1) for k in ('full_gate', 'sources_unchanged', 'private_pid_namespace', 'native_test_tree_removed')]
    + [('elapsed_seconds', x) for x in (-1, 901, True, '100', float('nan'), float('inf'), 10**400)]
    + [('issues', ['failure']), ('remaining_owned_processes', [123]), ('source_hashes_before', {}), ('source_hashes_after', {}), ('scope', 'PRODUCTION')])
def test_invalid_summary(verifier, key, value):
    body = report(); body[key] = value; assert verifier.valid_summary(body) is False


@pytest.mark.parametrize('key', list(report()) + ['credential', 'payload', 'raw_exception'])
def test_summary_field_closure(verifier, key):
    value = report()
    if key in value: del value[key]
    else: value[key] = 'PRIVATE'
    assert verifier.valid_summary(value) is False


@pytest.mark.parametrize('key,value', [('status', 'APPROVED'), ('operator_approval', 'yes'), ('admitted_operations', ['append']),
    ('working_credentials_included', True), ('database_or_pending_included', True), ('installation_service_or_network_actions', True)])
def test_draft_cannot_activate(key, value):
    body = deepcopy(json.loads((SOURCE / 'source-profile.json').read_bytes())); body[key] = value
    with pytest.raises(AssertionError): assert_draft(body)
