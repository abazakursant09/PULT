"""Independent, source-bound verifier for construction and real safe-stop proof."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
CASES = {'happy', 'wrong_boot', 'wrong_invocation', 'wrong_pid', 'wrong_start',
    'wrong_mode', 'source_changed', 'control_mode', 'state_mode', 'busy_control',
    'no_condition', 'trigger_condition', 'wrong_condition', 'cgroup_controller',
    'manager_alias', 'filesystem', 'cleanup_unknown', 'kill_controller', 'late_launch', 'freeze', 'tail'}
HELD = {'happy', 'cleanup_unknown', 'kill_controller', 'freeze', 'tail'}
FENCED = HELD | {'filesystem', 'late_launch'}
NEW_FILES = ('.github/workflows/custodian_stop_binding.yml',
    'backend/tests/test_custodian_stop_binding_ci.py',
    'ops/backup/custodian_v2/stop/constructor_stop.py', 'ops/backup/custodian_v2/stop/constructor_child.py',
    'ops/backup/custodian_v2/stop/source-profile.json',
    'ops/backup/tests/custodian_v2/stop/ci_gate.py', 'ops/backup/tests/custodian_v2/stop/run_gate.py',
    'ops/backup/tests/custodian_v2/stop/verify_receipt.py', 'ops/backup/tests/custodian_v2/stop/worker.py',
    'ops/backup/tests/custodian_v2/stop/actor_fault.py')
FIELDS = {'format', 'scope', 'outcome', 'tests', 'skips', 'failures', 'results',
    'source_hashes_before', 'source_hashes_after', 'sources_unchanged', 'private_network',
    'native_tree_removed', 'remaining_owned_processes', 'journal_write_admitted', 'listener_admitted',
    'deployment_admitted', 'remote_io_admitted', 'new_paid_resources', 'additional_spend_rub'}
ROW_FIELDS = {'case', 'outcome', 'seconds', 'database_unchanged', 'credential_unchanged',
    'owner_lock_released', 'descendants_empty', 'construction_held_proved', 'permanent_fence', 'kernel_fence_receipt'}

def valid_condition_receipt(record, unit, fence):
    if (type(record) is not dict or set(record) != {'UNIT', 'MESSAGE', 'MESSAGE_ID', 'JOB_TYPE', 'JOB_RESULT', '_PID'}
            or type(unit) is not str or not re.fullmatch('pult-call-gate-[0-9a-f]{32}\\.service', unit)
            or type(fence) is not str or not re.fullmatch('/[A-Za-z0-9_./-]+/constructor-stop\\.claim', fence)
            or '..' in PurePosixPath(fence).parts or str(PurePosixPath(fence)) != fence): return False
    return (record['UNIT'] == unit and record['_PID'] == '1' and record['JOB_TYPE'] == 'start'
        and record['JOB_RESULT'] == 'done' and record['MESSAGE_ID'] == '39f53479d3a045ac8e11786248231fbf'
        and type(record['MESSAGE']) is str
        and record['MESSAGE'].startswith(unit + ' - ')
        and record['MESSAGE'].endswith(' was skipped because of an unmet condition check (ConditionPathExists=!' + fence + ').'))

def require(value):
    if not value: raise ValueError('STOP_BINDING_RECEIPT_REFUSED')

def valid_summary(value):
    try:
        require(type(value) is dict and set(value) == FIELDS)
        require(value['format'] == 'pult-stop-bound-construction-native-v1'
            and value['scope'] == 'LOCAL_SYNTHETIC_CONSTRUCTION_AND_STOP_ONLY' and value['outcome'] == 'PASS')
        require(type(value['tests']) is int and value['tests'] == 21 and value['failures'] == [])
        for key in ('skips', 'new_paid_resources', 'additional_spend_rub'):
            require(type(value[key]) is int and value[key] == 0)
        for key in ('sources_unchanged', 'private_network', 'native_tree_removed'):
            require(value[key] is True)
        for key in ('journal_write_admitted', 'listener_admitted', 'deployment_admitted', 'remote_io_admitted'):
            require(value[key] is False)
        require(value['remaining_owned_processes'] == [])
        before = value['source_hashes_before']
        require(type(before) is dict and len(before) == 45 and before == value['source_hashes_after'])
        require(all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k, v in before.items()))
        rows = value['results']
        require(type(rows) is list and len(rows) == 21 and {r['case'] for r in rows} == CASES)
        for row in rows:
            require(type(row) is dict and set(row) == ROW_FIELDS and row['outcome'] == 'PASS')
            name, seconds = row['case'], row['seconds']
            require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds < 65)
            if name in ('freeze', 'tail'): require(37 < seconds < 57)
            for key in ('database_unchanged', 'credential_unchanged', 'owner_lock_released', 'descendants_empty'):
                require(row[key] is True)
            require(row['construction_held_proved'] is (name in HELD))
            require(row['permanent_fence'] is (name in FENCED))
            receipt = row['kernel_fence_receipt']
            if name in ('happy', 'late_launch'):
                require(type(receipt) is dict and set(receipt) == {'unit', 'fence', 'record'}
                    and valid_condition_receipt(receipt['record'], receipt['unit'], receipt['fence']))
            else: require(receipt is None)
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False

def expected_paths():
    file = ROOT.parent / 'host/verify_receipt.py'
    raw = file.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == '415574625141a879ab234011345d24e8a2d04d943b89bb3ed599196ced216b6a')
    from types import ModuleType
    old = ModuleType('_fixed_construction_inventory'); old.__file__ = str(file)
    exec(compile(raw, str(file), 'exec', dont_inherit=True), old.__dict__)
    paths = {'host:' + name: path for name, path in old.expected_paths().items()}
    for relative in NEW_FILES: paths['binding:' + relative] = REPO / relative
    for name, relative, digest in (
        ('core', 'ops/backup/custodian_v2/controller/frozen/admission.py', '961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7'),
        ('supervisor', 'ops/backup/custodian_v2/controller/supervisor_v2.py', '3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e')):
        file = REPO / relative
        require(hashlib.sha256(file.read_bytes()).hexdigest() == digest)
        paths['binding:' + name] = file
    draft = json.loads((REPO / 'ops/backup/custodian_v2/stop/source-profile.json').read_bytes())
    require(draft['status'] == 'DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED' and draft['operator_approval'] is None
        and draft['admitted_operations'] == [] and draft['working_credentials_included'] is False
        and draft['installation_service_or_network_actions'] is False)
    require(draft['sources'] == {name: hashlib.sha256((REPO / 'ops/backup/custodian_v2/stop' / name).read_bytes()).hexdigest()
        for name in ('constructor_stop.py', 'constructor_child.py')})
    require(len(paths) == 45)
    return paths

def verify(file):
    require(sys.platform == 'linux' and os.geteuid() == 0)
    file = Path(file)
    require(file.parent.resolve() == ROOT and re.fullmatch('RUN_[0-9]+\\.json', file.name))
    info = file.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o644 and 0 < info.st_size < 1048576)
    require(not any(n.startswith('system.posix_acl_') for n in os.listxattr(file, follow_symlinks=False)))
    raw = file.read_bytes()
    def pairs(rows):
        value = {}
        for name, item in rows:
            require(name not in value); value[name] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs)
    require(valid_summary(value))
    expected = expected_paths()
    require(set(expected) == set(value['source_hashes_before']))
    for name, path in expected.items(): require(hashlib.sha256(path.read_bytes()).hexdigest() == value['source_hashes_before'][name])
    for row in value['results']:
        receipt = row['kernel_fence_receipt']
        if receipt is None: continue
        journal = subprocess.run(['/usr/bin/journalctl', '--no-pager', '-o', 'json', '-u', receipt['unit']],
            capture_output=True, text=True, timeout=5, check=True)
        fields = set(receipt['record'])
        require(any({k: entry.get(k) for k in fields} == receipt['record']
            for entry in (json.loads(line) for line in journal.stdout.splitlines())))
    for directory, pattern in (('/run', 'pult-controller-scope-*'), ('/tmp', 'pult-cc-*')):
        require(not list(Path(directory).glob(pattern)))
    units = subprocess.run(['/usr/bin/systemctl', 'list-units', '--all', '--plain', '--no-legend',
        'pult-controller-ci-run-*', 'pult-call-gate-*'], capture_output=True, timeout=10)
    require(units.returncode == 0 and not units.stdout.strip() and not units.stderr)
    return hashlib.sha256(raw).hexdigest()

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_stop_binding_verifier_refused"}'); return 2
    if sys.argv[1:] != ['--reviewed-by-main']: return 2
    rows = sorted(ROOT.glob('RUN_*.json'), key=lambda p: p.stat().st_mtime_ns)
    require(bool(rows)); digest = verify(rows[-1])
    print(json.dumps(dict(outcome='PASS', receipt_sha256=digest, independent_postcheck=True), sort_keys=True))
    return 0

if __name__ == '__main__': raise SystemExit(main())
