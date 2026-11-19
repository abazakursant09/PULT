"""Independent constructor receipt/current-source/resource verification."""
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
CASES = {'happy', 'pending_prepared', 'pending_dispatched', 'repeat', 'wrong_approval',
         'old_profile', 'wrong_mode', 'extra_field', 'duplicate', 'noncanonical',
         'uid_wrong', 'writer_same', 'wrong_bounds', 'state_inode', 'lease_inode',
         'missing_lock', 'state_mode', 'state_acl', 'bad_schema', 'bad_location',
         'secret_inode', 'secret_owner', 'secret_mode', 'secret_hardlink',
         'secret_wrong_key', 'secret_extra', 'intake_tamper', 'stage_tamper',
         'wheel_tamper', 'source_extra', 'preloaded', 'foreign_path', 'optimized',
         'reader_constructor', 'reader_close', 'capsule_close', 'server_constructor',
         'profile_drift', 'secret_drift', 'lease_drift', 'double_state', 'double_secret',
         'double_source', 'profile_alias', 'post_capsule_owner_check', 'partial_capsule_close',
         'old_source_profile', 'host_profile_symlink', 'host_profile_hardlink',
         'host_profile_userowned', 'host_profile_writable', 'host_profile_acl',
         'host_profile_parent_acl', 'source_profile_hash', 'hot_journal', 'cold_journal',
         'wal_sidecar', 'shm_sidecar', 'wal_header', 'recovery_drift'}
POSITIVE = {'happy', 'pending_prepared', 'pending_dispatched', 'repeat'}
RECOVERY_REJECT = {'hot_journal', 'cold_journal', 'wal_sidecar', 'shm_sidecar', 'wal_header'}
RETAINED = {'reader_constructor', 'reader_close', 'capsule_close', 'profile_drift',
            'secret_drift', 'lease_drift', 'post_capsule_owner_check', 'partial_capsule_close', 'recovery_drift'} | RECOVERY_REJECT
CONSTRUCTED = POSITIVE | (RETAINED - RECOVERY_REJECT - {'reader_constructor'})
NO_BOOTSTRAP = {'wrong_approval', 'old_profile', 'wrong_mode', 'extra_field', 'duplicate', 'noncanonical',
    'uid_wrong', 'writer_same', 'wrong_bounds', 'intake_tamper', 'optimized', 'double_state',
    'double_secret', 'double_source', 'profile_alias', 'old_source_profile', 'host_profile_symlink',
    'host_profile_hardlink', 'host_profile_userowned', 'host_profile_writable', 'host_profile_acl',
    'host_profile_parent_acl', 'source_profile_hash'}
INTAKE_ONLY = {'stage_tamper', 'preloaded', 'foreign_path'}
COLD_CAPTURE = {'wheel_tamper', 'source_extra'}
EARLY = CASES - CONSTRUCTED - {'reader_constructor', 'server_constructor', 'bad_schema',
        'bad_location', 'secret_inode', 'secret_owner', 'secret_mode', 'secret_hardlink',
        'secret_wrong_key', 'secret_extra'}
FIELDS = {'format', 'outcome', 'tests', 'failures', 'skips', 'results', 'source_hashes_before',
          'source_hashes_after', 'sources_unchanged', 'private_pid_network_namespace',
          'native_tree_removed', 'elapsed_seconds', 'new_paid_resources', 'additional_spend_rub',
          'scope', 'remaining_owned_processes', 'deployment_admitted', 'listener_admitted',
          'external_io_admitted', 'outer_write_stop_bound'}


def require(value):
    if not value:
        raise ValueError('HOST_CONSTRUCTION_RECEIPT_REFUSED')


def valid_summary(value):
    try:
        require(type(value) is dict and set(value) == FIELDS)
        require(value['format'] == 'pult-staged-host-construction-native-proof-v1' and value['outcome'] == 'PASS')
        require(value['scope'] == 'LOCAL_CONSTRUCTION_ONLY_NO_OUTER_WRITE_STOP_DEPLOYMENT')
        require(type(value['tests']) is int and value['tests'] == 60 and value['failures'] == [])
        for key in ('skips', 'new_paid_resources', 'additional_spend_rub'):
            require(type(value[key]) is int and value[key] == 0)
        for key in ('sources_unchanged', 'private_pid_network_namespace', 'native_tree_removed'):
            require(value[key] is True)
        for key in ('deployment_admitted', 'listener_admitted', 'external_io_admitted', 'outer_write_stop_bound'):
            require(value[key] is False)
        require(value['remaining_owned_processes'] == [])
        elapsed = value['elapsed_seconds']
        require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= 900)
        before = value['source_hashes_before']
        require(type(before) is dict and len(before) == 33 and before == value['source_hashes_after'])
        require(all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k, v in before.items()))
        rows = value['results']
        require(type(rows) is list and len(rows) == 60 and {r['case'] for r in rows} == CASES)
        for row in rows:
            require(set(row) == {'case', 'outcome', 'metrics'} and row['outcome'] == 'PASS')
            name, m = row['case'], row['metrics']
            require(type(m) is dict)
            for key in ('sql', 'key_opens', 'key_reads', 'payload_exec', 'reader_constructor', 'server_constructor'):
                require(type(m[key]) is int and m[key] >= 0)
            bootstrap = m['bootstrap_exec']
            require(type(bootstrap) is dict and set(bootstrap) == {'intake', 'cold', 'staged'})
            require(all(type(n) is int and n >= 0 for n in bootstrap.values()))
            expected = dict(intake=0, cold=0, staged=0) if name in NO_BOOTSTRAP else (
                dict(intake=1, cold=0, staged=0) if name in INTAKE_ONLY else (
                dict(intake=1, cold=1, staged=0) if name in COLD_CAPTURE else dict(intake=1, cold=1, staged=1)))
            require(bootstrap == expected)
            require(m['payload_exec'] == 0 if name in NO_BOOTSTRAP | INTAKE_ONLY | COLD_CAPTURE else m['payload_exec'] > 0)
            require(type(m['network']) is int and type(m['listeners']) is int and m['network'] == m['listeners'] == 0)
            for key in ('owner_before_sql', 'owner_before_key', 'parent_kernel_lock_checked',
                        'parent_state_bytes_unchanged', 'parent_owner_released_after_exit', 'parent_sidecars_unchanged'):
                require(m[key] is True)
            require(m['parent_hot_replay_proven'] is (name == 'hot_journal'))
            require(m['constructed'] is (name in CONSTRUCTED))
            require(m['refused'] is (name not in POSITIVE))
            require(m['error_type'] == (None if name in POSITIVE else 'Refused'))
            require(m['retained'] is (name in RETAINED))
            if name in CONSTRUCTED:
                for key in ('checked', 'public_methods', 'false_flags', 'summary_matches', 'lease_acquired'):
                    require(m[key] is True)
                require(m['sql'] > 0 and m['key_opens'] > 0 and m['key_reads'] > 0
                        and m['reader_constructor'] == m['server_constructor'] == 1)
            if name in POSITIVE:
                require(m['closed'] is True and m['modules_clean'] is True)
                require(m['pending'] is name.startswith('pending_'))
            if name in RETAINED:
                require(m['imports_retained'] is True and m['failed_calls_blocked'] is True
                        and m['failed_handle_rooted'] is True and m['lease_acquired'] is True)
            else:
                require(m['modules_clean'] is True)
            if name == 'reader_constructor':
                require(m['partial_reader_retained'] is True and m['reader_constructor'] == 1 and m['server_constructor'] == 0)
            if name == 'repeat':
                require(m['repeat_refused'] is True)
            if name in ('profile_drift', 'secret_drift', 'lease_drift', 'recovery_drift'):
                require(m['drift_refused'] is True)
            if name in EARLY:
                require(m['sql'] == m['key_opens'] == m['key_reads'] == 0)
            if name in ('bad_schema', 'bad_location'):
                require(m['sql'] > 0 and m['key_opens'] == m['key_reads'] == 0)
            if name in ('secret_inode', 'secret_owner', 'secret_mode', 'secret_hardlink'):
                require(m['sql'] > 0 and m['key_opens'] > 0 and m['key_reads'] == 0)
            if name in ('server_constructor', 'secret_wrong_key', 'secret_extra', 'reader_constructor'):
                require(m['sql'] > 0 and m['key_opens'] > 0 and m['key_reads'] > 0)
                require(m['reader_constructor'] == int(name in ('reader_constructor', 'server_constructor')))
                require(m['server_constructor'] == int(name == 'server_constructor'))
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def expected_paths():
    source, tests = REPO / 'ops/backup/custodian_v2', ROOT.parent
    catalogue, cold = source / 'source-profile.json', source / 'cold/cold_host.py'
    require(hashlib.sha256(catalogue.read_bytes()).hexdigest() == '8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3')
    require(hashlib.sha256(cold.read_bytes()).hexdigest() == '091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c')
    paths = {}
    for name, wanted in json.loads(catalogue.read_bytes())['sources'].items():
        file = REPO / name.removeprefix('frozen/') if name.startswith('frozen/') and name != 'frozen/ops/backup/erasure_journal_s3.py' else source / name
        require(hashlib.sha256(file.read_bytes()).hexdigest() == wanted)
        paths['source:' + name] = file
    paths.update(helper=tests / 'support.py', catalogue=catalogue, entry=source / 'host/staged_host.py',
                 worker=ROOT / 'worker.py', gate=ROOT / 'run_gate.py',
                 scope=tests / 'controller/scope_control.py', supervisor=tests / 'controller/ci_gate.py',
                 wrapper=ROOT / 'ci_gate.py', verifier=ROOT / 'verify_receipt.py', draft=source / 'host/source-profile.json')
    for name in ('staged_intake.py', 'cold_host.py', 'staged_ipc.py'):
        paths['entry:' + name] = source / {'staged_intake.py': 'intake', 'cold_host.py': 'cold', 'staged_ipc.py': 'staged'}[name] / name
    pins = {'entry': 'faf627fef860d560d01605e3717c9939fe81c2c4156b231b03e94c57320c2bae', 'worker': '5f64b491e201e01444cacba6cdd3d9221083b7e850c70e9640df6d2edc903d6a',
            'entry:staged_intake.py': '48c238948c9f2c94ed485093b0a583ebcaf7dca760612622e5a01eebd063617c',
            'entry:cold_host.py': '091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c',
            'entry:staged_ipc.py': 'f8010c39d6162dcac44b5e20cef1e79323c0e88a2e8a01eac34e8fe9163dbf02',
            'helper': 'd343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d',
            'scope': 'd46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234',
            'supervisor': 'd30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b'}
    for name, wanted in pins.items():
        require(hashlib.sha256(paths[name].read_bytes()).hexdigest() == wanted)
    tree = ast.parse(cold.read_bytes())
    wheels = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == 'WHEELS' for t in n.targets))
    require(len(wheels) == 7)
    for name, wanted in wheels.items():
        file = tests / 'wheels' / name
        require(hashlib.sha256(file.read_bytes()).hexdigest() == wanted)
        paths['wheel:' + name] = file
    require(len(paths) == 33)
    return paths


def verify(file):
    require(sys.platform == 'linux' and os.geteuid() == 0)
    file = Path(file)
    require(file.parent.resolve() == ROOT and re.fullmatch('RUN_[0-9]+\\.json', file.name))
    info = file.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) in (0o444, 0o644) and 0 < info.st_size < 1048576)
    require(not any(n.startswith('system.posix_acl_') for n in os.listxattr(file, follow_symlinks=False)))
    raw = file.read_bytes()
    def pairs(rows):
        result = {}
        for key, item in rows:
            require(key not in result)
            result[key] = item
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    require(valid_summary(value))
    paths = expected_paths()
    require(set(paths) == set(value['source_hashes_before']))
    for name, path in paths.items():
        require(hashlib.sha256(path.read_bytes()).hexdigest() == value['source_hashes_before'][name])
    for directory, pattern in (('/run', 'pult-controller-scope-*'), ('/tmp', 'pult-cc-*')):
        require(not list(Path(directory).glob(pattern)))
    units = subprocess.run(['/usr/bin/systemctl', 'list-units', '--all', '--plain', '--no-legend',
                            'pult-controller-ci-run-*', 'pult-call-gate-*'], capture_output=True, timeout=10)
    require(units.returncode == 0 and not units.stdout.strip() and not units.stderr)
    return hashlib.sha256(raw).hexdigest()


def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_host_construction_verifier_refused"}')
        return 2
    if len(sys.argv) != 2:
        return 2
    try:
        digest = verify(Path(sys.argv[1]))
    except Exception:
        print('{"outcome":"REFUSED","reason":"host_construction_receipt_verification_failed"}')
        return 1
    print('HOST60_INPUTS33_REHASHED_RESOURCES_EMPTY ' + digest)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
