"""Offline construction-only contracts; native Linux proof remains mandatory."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / 'ops/backup/custodian_v2/host'
TESTS = REPO / 'ops/backup/tests/custodian_v2/host'
WORKFLOW = REPO / '.github/workflows/custodian_host_construction.yml'


def load(file, name):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verifier():
    return load(TESTS / 'verify_receipt.py', 'independent_construction_contract')


def summary():
    v = verifier()
    rows = []
    for name in sorted(v.CASES):
        bootstrap = dict(intake=0, cold=0, staged=0) if name in v.NO_BOOTSTRAP else (
            dict(intake=1, cold=0, staged=0) if name in v.INTAKE_ONLY else (
            dict(intake=1, cold=1, staged=0) if name in v.COLD_CAPTURE else dict(intake=1, cold=1, staged=1)))
        m = dict(constructed=name in v.CONSTRUCTED, checked=False, closed=False,
                 refused=name not in v.POSITIVE, error_type=None if name in v.POSITIVE else 'Refused',
                 sql=0, key_opens=0, key_reads=0, payload_exec=0 if name in v.NO_BOOTSTRAP | v.INTAKE_ONLY | v.COLD_CAPTURE else 1,
                 bootstrap_exec=bootstrap, reader_constructor=0, server_constructor=0,
                 network=0, listeners=0, owner_before_sql=True, owner_before_key=True,
                 parent_kernel_lock_checked=True, parent_state_bytes_unchanged=True,
                 parent_owner_released_after_exit=True, parent_sidecars_unchanged=True,
                 parent_hot_replay_proven=name == 'hot_journal', retained=name in v.RETAINED,
                 modules_clean=name not in v.RETAINED, imports_retained=name in v.RETAINED,
                 failed_calls_blocked=name in v.RETAINED, partial_reader_retained=name == 'reader_constructor',
                 failed_handle_rooted=name in v.RETAINED,
                 public_methods=False, false_flags=False, summary_matches=False,
                 lease_acquired=name in v.RETAINED, pending=name.startswith('pending_'), repeat_refused=name == 'repeat',
                 drift_refused=name in ('profile_drift', 'secret_drift', 'lease_drift', 'recovery_drift'))
        if name in v.CONSTRUCTED:
            m.update(checked=True, public_methods=True, false_flags=True, summary_matches=True,
                     lease_acquired=True, sql=2, key_opens=1, key_reads=2, reader_constructor=1, server_constructor=1)
        if name in v.POSITIVE:
            m['closed'] = True
        if name == 'reader_constructor':
            m.update(reader_constructor=1, lease_acquired=True, sql=1, key_opens=1, key_reads=1)
        if name in ('secret_wrong_key', 'secret_extra', 'server_constructor'):
            m.update(lease_acquired=True, sql=1, key_opens=1, key_reads=1,
                     reader_constructor=int(name == 'server_constructor'), server_constructor=int(name == 'server_constructor'))
        if name in ('bad_schema', 'bad_location'):
            m.update(sql=1, lease_acquired=True)
        if name in ('secret_inode', 'secret_owner', 'secret_mode', 'secret_hardlink'):
            m.update(sql=1, key_opens=1, lease_acquired=True)
        rows.append(dict(case=name, outcome='PASS', metrics=m))
    hashes = {str(i): 'a' * 64 for i in range(33)}
    return dict(format='pult-staged-host-construction-native-proof-v1', outcome='PASS',
                scope='LOCAL_CONSTRUCTION_ONLY_NO_OUTER_WRITE_STOP_DEPLOYMENT', tests=60,
                failures=[], skips=0, results=rows, source_hashes_before=hashes, source_hashes_after=dict(hashes),
                sources_unchanged=True, private_pid_network_namespace=True, native_tree_removed=True,
                elapsed_seconds=10, new_paid_resources=0, additional_spend_rub=0,
                remaining_owned_processes=[], deployment_admitted=False, listener_admitted=False,
                external_io_admitted=False, outer_write_stop_bound=False)


def test_independent_summary_accepts_complete_construction_only_shape():
    assert verifier().valid_summary(summary())


@pytest.mark.parametrize('field', ['deployment_admitted', 'listener_admitted', 'external_io_admitted', 'outer_write_stop_bound'])
def test_summary_cannot_claim_operational_admission(field):
    report = summary()
    report[field] = True
    assert not verifier().valid_summary(report)


@pytest.mark.parametrize('field', ['sources_unchanged', 'private_pid_network_namespace', 'native_tree_removed'])
def test_summary_requires_independent_lifecycle_evidence(field):
    report = summary()
    report[field] = False
    assert not verifier().valid_summary(report)


@pytest.mark.parametrize('case,field', [
    ('happy', 'owner_before_sql'), ('happy', 'owner_before_key'),
    ('happy', 'parent_kernel_lock_checked'), ('happy', 'parent_state_bytes_unchanged'),
    ('happy', 'parent_owner_released_after_exit'), ('happy', 'public_methods'),
    ('happy', 'summary_matches'), ('happy', 'false_flags'), ('happy', 'closed'),
    ('happy', 'modules_clean'), ('reader_constructor', 'partial_reader_retained'),
    ('reader_constructor', 'imports_retained'), ('reader_constructor', 'failed_calls_blocked'),
    ('partial_capsule_close', 'imports_retained'), ('post_capsule_owner_check', 'imports_retained'),
    ('reader_close', 'retained'), ('capsule_close', 'failed_calls_blocked'),
    ('profile_drift', 'drift_refused'), ('secret_drift', 'drift_refused'),
    ('lease_drift', 'drift_refused'), ('repeat', 'repeat_refused')])
def test_missing_semantic_proof_refused(case, field):
    report = summary()
    next(r['metrics'] for r in report['results'] if r['case'] == case)[field] = False
    assert not verifier().valid_summary(report)


@pytest.mark.parametrize('case,field', [
    ('happy', 'reader_constructor'),
    ('server_constructor', 'sql'), ('server_constructor', 'key_opens'),
    ('server_constructor', 'key_reads'), ('server_constructor', 'reader_constructor'),
    ('server_constructor', 'server_constructor'),
    ('secret_wrong_key', 'sql'), ('secret_wrong_key', 'key_opens'), ('secret_wrong_key', 'key_reads'),
    ('secret_extra', 'sql'), ('secret_extra', 'key_opens'), ('secret_extra', 'key_reads')])
def test_late_phase_cannot_pass_without_actual_activity(case, field):
    report = summary()
    next(r['metrics'] for r in report['results'] if r['case'] == case)[field] = 0
    assert not verifier().valid_summary(report)


@pytest.mark.parametrize('case,field', [
    ('hot_journal', 'parent_hot_replay_proven'), ('hot_journal', 'parent_sidecars_unchanged'),
    ('wal_sidecar', 'parent_sidecars_unchanged'), ('shm_sidecar', 'parent_sidecars_unchanged'),
    ('recovery_drift', 'drift_refused'), ('recovery_drift', 'failed_handle_rooted'),
    ('reader_close', 'failed_handle_rooted'), ('reader_constructor', 'failed_handle_rooted')])
def test_recovery_and_process_root_proof_cannot_be_omitted(case, field):
    report = summary()
    next(r['metrics'] for r in report['results'] if r['case'] == case)[field] = False
    assert not verifier().valid_summary(report)


@pytest.mark.parametrize('case,field', [
    ('wrong_approval', 'sql'), ('old_source_profile', 'key_reads'),
    ('host_profile_symlink', 'key_opens'), ('host_profile_hardlink', 'sql'),
    ('host_profile_userowned', 'sql'), ('host_profile_acl', 'key_reads'),
    ('host_profile_parent_acl', 'sql'), ('double_state', 'sql'),
    ('double_secret', 'key_reads'), ('double_source', 'sql'),
    ('profile_alias', 'key_opens'), ('bad_schema', 'key_opens'),
    ('bad_location', 'key_reads'), ('secret_inode', 'key_reads'),
    ('secret_owner', 'key_reads'), ('secret_mode', 'key_reads'), ('secret_hardlink', 'key_reads')])
def test_early_activity_cannot_be_hidden(case, field):
    report = summary()
    next(r['metrics'] for r in report['results'] if r['case'] == case)[field] = 1
    assert not verifier().valid_summary(report)


@pytest.mark.parametrize('case,field', [
    ('wrong_approval', 'payload_exec'), ('stage_tamper', 'payload_exec'),
    ('wheel_tamper', 'payload_exec'), ('preloaded', 'bootstrap_exec')])
def test_unexpected_code_execution_cannot_be_hidden(case, field):
    report = summary()
    m = next(r['metrics'] for r in report['results'] if r['case'] == case)
    m[field] = 99
    assert not verifier().valid_summary(report)


def test_pidfd_failure_reaps_owned_child_and_closes_lock(tmp_path):
    (tmp_path / 'state').mkdir()
    (tmp_path / 'state/anchor.sqlite').write_bytes(b'synthetic')
    closed = []
    child = SimpleNamespace(returncode=None, killed=False, reaped=False)
    child.poll = lambda: child.returncode
    def kill():
        child.killed = True
        child.returncode = -9
    def communicate(timeout):
        child.reaped = True
        return b'', b''
    child.kill, child.communicate = kill, communicate
    def unavailable(pid):
        raise OSError('synthetic pidfd failure')
    fake_os = SimpleNamespace(open=lambda *a: 71, close=closed.append,
        O_RDWR=2, O_CLOEXEC=0, pidfd_open=unavailable)
    child.pid = 123
    tree = ast.parse((TESTS / 'run_gate.py').read_bytes())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'exercise')
    namespace = dict(os=fake_os, Path=Path, subprocess=SimpleNamespace(Popen=lambda *a, **kw: child, PIPE=-1), UID=47003, GID=47004)
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'owned_child_cleanup_contract', 'exec'), namespace)
    with pytest.raises(OSError, match='synthetic pidfd failure'):
        namespace['exercise'](tmp_path, tmp_path / 'worker.py', 'happy')
    assert child.killed and child.reaped and closed == [71]


def test_namespace_reexec_targets_current_gate_not_private_prototype():
    text = (TESTS / 'run_gate.py').read_text()
    assert "str(HERE / 'run_gate.py')" in text
    assert "str(HERE / 'run_native.py')" not in text


@pytest.mark.parametrize('mutation', ['less', 'duplicate', 'skip', 'hash', 'extra', 'bool', 'time'])
def test_malformed_receipt_shape_refused(mutation):
    report = summary()
    if mutation == 'less': report['results'].pop()
    elif mutation == 'duplicate': report['results'][0] = copy.deepcopy(report['results'][1])
    elif mutation == 'skip': report['skips'] = 1
    elif mutation == 'hash': report['source_hashes_after']['0'] = 'b' * 64
    elif mutation == 'extra': report['operator_approval'] = True
    elif mutation == 'bool': report['new_paid_resources'] = False
    elif mutation == 'time': report['elapsed_seconds'] = float('nan')
    assert not verifier().valid_summary(report)


def test_draft_profile_is_not_operator_approval_or_service():
    value = json.loads((SOURCE / 'source-profile.json').read_bytes())
    assert value['status'] == 'DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED'
    assert value['operator_approval'] is None and value['admitted_operations'] == []
    for key in ('working_credentials_included', 'state_bootstrap_or_reset', 'installation_service_or_network_actions'):
        assert value[key] is False
    assert value['sources'] == {'staged_host.py': hashlib.sha256((SOURCE / 'staged_host.py').read_bytes()).hexdigest()}


def test_constructor_api_is_not_a_listener_or_writer():
    module = load(SOURCE / 'staged_host.py', 'dormant_staged_host')
    assert {n for n in vars(module.Constructed) if not n.startswith('_')} == {'check', 'close'}
    tree = ast.parse((SOURCE / 'staged_host.py').read_bytes())
    assert not any(isinstance(n, ast.Import) and any(a.name.split('.')[0] in module.PAYLOAD for a in n.names) for n in tree.body)
    assert not any(isinstance(n, ast.ImportFrom) and n.module and n.module.split('.')[0] in module.PAYLOAD for n in tree.body)
    text = (SOURCE / 'staged_host.py').read_text()
    assert "result.anchor != '/'" in text
    assert 'ready._failure = error' in text and 'self._registrations = registrations' in text
    assert 'sys.meta_path[:] = meta' in text and 'sys.modules[name] = module' in text
    assert text.index('ready._owner = owned_host.Lease') < text.index('ready._anchor.load()') < text.index('credential(host_core, value)')
    assert 'transport=httpx.MockTransport(no_io)' in text
    assert '.serve(' not in text and '.prepare(' not in text and '.create(' not in text
    assert 'ColdReady' not in text and '._cold' not in text and '._finder' not in text


def test_workflow_runs_full_native_gate_and_optimized_refusals():
    doc = yaml.safe_load(WORKFLOW.read_text())
    assert doc['permissions'] == {'contents': 'read'}
    assert set(doc['jobs']) == {'host-construction'}
    job = doc['jobs']['host-construction']
    assert job['runs-on'] == 'ubuntu-24.04' and job['timeout-minutes'] == 20
    assert set(doc[True]) == {'pull_request', 'push', 'workflow_dispatch'}
    text = WORKFLOW.read_text()
    for line in text.splitlines():
        if 'uses:' in line:
            assert re.fullmatch('[0-9a-f]{40}', line.split('@')[-1].strip())
    assert 'persist-credentials: false' in text and '--require-hashes' in text
    assert 'sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B ops/backup/tests/custodian_v2/host/ci_gate.py --reviewed-by-main' in text
    assert 'optimized_host_construction_ci_refused' in text
    assert 'optimized_host_construction_gate_refused' in text
    assert 'optimized_host_construction_verifier_refused' in text
    for bad in ('upload-artifact', 'docker push', 'workflow_run:', 'continue-on-error', 'secrets.'):
        assert bad not in text


def test_native_gate_and_verifier_have_exact_same_complete_cases():
    gate = ast.parse((TESTS / 'run_gate.py').read_bytes())
    cases = next(ast.literal_eval(n.value) for n in gate.body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == 'CASES' for t in n.targets))
    v = verifier()
    assert len(cases) == len(set(cases)) == len(v.CASES) == 60 and set(cases) == v.CASES
    text = (TESTS / 'run_gate.py').read_text()
    assert 'pidfd_send_signal' in text and 'assert locked(lock_fd)' in text
    assert 'parent_owner_released_after_exit=True' in text
    assert 'dir=work' in text and 'base.parent == work' in text
    wrapper = (TESTS / 'ci_gate.py').read_text()
    assert 'supervisor.create_scope(control)' in wrapper and 'supervisor.supervised_run' in wrapper
    assert 'verifier().verify(file)' in wrapper and 'tests=60' in wrapper


def test_worker_drops_caller_handle_before_gc_root_check():
    text = (TESTS / 'worker.py').read_text()
    assert text.index('ready = failed = None') < text.index('gc.collect()') < text.index("metrics['failed_handle_rooted'] =")
    assert 'any(owned is failed_ref() for owned in module._FAILED)' in text
