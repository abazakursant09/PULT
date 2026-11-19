"""Owned real-systemd construction/stop fault matrix; synthetic local data only."""
import sys
sys.dont_write_bytecode = True
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
SOURCE = REPO / 'ops/backup/custodian_v2/stop'
MODES = ('happy', 'wrong_boot', 'wrong_invocation', 'wrong_pid', 'wrong_start',
    'wrong_mode', 'source_changed', 'control_mode', 'state_mode', 'busy_control',
    'no_condition', 'trigger_condition', 'wrong_condition', 'cgroup_controller',
    'manager_alias', 'filesystem', 'cleanup_unknown', 'kill_controller', 'late_launch', 'freeze', 'tail')

def load(file, name):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

def sha(file): return hashlib.sha256(file.read_bytes()).hexdigest()
def canonical(value): return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
def put(file, raw, mode=0o644): file.write_bytes(raw); file.chmod(mode)
def ident(file):
    info = file.lstat()
    return {'device': info.st_dev, 'inode': info.st_ino}

def line(child):
    deadline = time.monotonic() + 15
    raw = bytearray()
    while len(raw) < 4096:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([child.stdout], [], [], remaining)[0]:
            raise RuntimeError('ROOT_CONTROLLER_START_TIMEOUT')
        part = os.read(child.stdout.fileno(), 1)
        if not part: raise RuntimeError('ROOT_CONTROLLER_START_EOF')
        if part == b'\n': return bytes(raw)
        raw.extend(part)
    raise RuntimeError('ROOT_CONTROLLER_OUTPUT_BOUND')

def state(unit):
    result = subprocess.run(['/usr/bin/systemctl', 'show', unit, '--no-pager',
        '--property=MainPID,InvocationID,ActiveState,SubState,LoadState,ConditionResult'],
        capture_output=True, text=True, timeout=5, check=True)
    return dict(row.split('=', 1) for row in result.stdout.splitlines() if '=' in row)

def empty(unit):
    root = Path('/sys/fs/cgroup/system.slice') / unit
    return not root.exists() or not any(f.read_text().strip() for f in root.rglob('cgroup.procs'))

def stopped(unit, timeout=7):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        status = state(unit)
        if status.get('ActiveState') in ('inactive', 'failed') and empty(unit): return True
        time.sleep(0.05)
    return False

def condition_receipt(unit, fence, verifier):
    until = time.monotonic() + 4
    fields = ('UNIT', 'MESSAGE', 'MESSAGE_ID', 'JOB_TYPE', 'JOB_RESULT', '_PID')
    while time.monotonic() < until:
        result = subprocess.run(['/usr/bin/journalctl', '--no-pager', '-o', 'json', '-u', unit],
            capture_output=True, text=True, timeout=2, check=True)
        for line in result.stdout.splitlines():
            entry = json.loads(line)
            record = {key: entry.get(key) for key in fields}
            if verifier.valid_condition_receipt(record, unit, str(fence)):
                return dict(unit=unit, fence=str(fence), record=record)
        time.sleep(0.05)
    raise AssertionError('MANAGER_FENCE_REJECTION_NOT_PROVED')

def case(mode, base, original, scope, control, supervisor, helper):
    root = helper.gate.fixture(base, original, 'happy')
    root_id = ident(root)
    prior_units = set(Path(scope['units']).iterdir())
    process = None
    root_unit = None
    busy = None
    kernel_receipt = None
    try:
        for name in ('state', 'state/anchor.sqlite', 'state/owner.lock', 'secrets', 'secrets/reader.json'):
            os.chown(root / name, 65534, 65534)
        host = json.loads((root / 'host.json').read_bytes())
        host['custodian_uid'] = host['socket_gid'] = 65534
        put(root / 'host.json', canonical(host))
        binding = root / 'binding'; binding.mkdir(mode=0o755)
        for name in ('constructor_stop.py', 'constructor_child.py', 'worker.py', 'actor_fault.py'):
            shutil.copyfile((SOURCE if name.startswith('constructor_') else ROOT) / name, binding / name)
            (binding / name).chmod(0o644)
        for name, relative in (
            ('supervisor_v2.py', 'ops/backup/custodian_v2/controller/supervisor_v2.py'),
            ('admission.py', 'ops/backup/custodian_v2/controller/frozen/admission.py'),
            ('scope_control.py', 'ops/backup/tests/custodian_v2/controller/scope_control.py')):
            shutil.copyfile(REPO / relative, binding / name); (binding / name).chmod(0o644)
        owned = root / 'control'; owned.mkdir(mode=0o700); owned.chmod(0o700)
        put(owned / 'constructor-stop.lock', b'', 0o600)
        seen_control = owned
        if mode == 'manager_alias':
            seen_control = root / 'alternate-control'
            seen_control.mkdir(mode=0o700); seen_control.chmod(0o700)
            put(seen_control / 'constructor-stop.lock', b'', 0o600)
        profile = root / 'stop-profile.json'
        root_unit = 'pult-call-gate-' + uuid4().hex + '.service'
        condition = '--property=ConditionPathExists=!' + str(owned / 'constructor-stop.claim')
        if mode == 'trigger_condition': condition = condition.replace('=!', '=|!')
        if mode == 'wrong_condition': condition += '-other'
        command = ['/usr/bin/systemd-run', '--quiet', '--pipe', '--no-ask-password', '--expand-environment=no',
            '--unit=' + root_unit, '--property=Type=exec', '--property=ExitType=' + ('cgroup' if mode == 'cgroup_controller' else 'main'),
            '--property=RemainAfterExit=no', '--property=User=0', '--property=Group=0',
            '--property=KillMode=control-group', '--property=KillSignal=SIGTERM', '--property=SendSIGKILL=yes',
            '--property=FinalKillSignal=SIGKILL', '--property=TimeoutStopSec=2', '--property=Restart=no',
            '--property=RuntimeMaxSec=70', '--property=PrivateNetwork=yes', '--property=NoNewPrivileges=yes',
            '--setenv=PATH=/usr/bin:/bin', '--setenv=LANG=C.UTF-8',
            '--setenv=PULT_CONTROLLER_CI_SCOPE=' + os.environ['PULT_CONTROLLER_CI_SCOPE']]
        if mode != 'no_condition': command.append(condition)
        if mode == 'manager_alias': command.append('--property=BindPaths=' + str(seen_control) + ':' + str(owned))
        command += ['/usr/bin/python3.12', '-I', '-S', '-B', str(binding / 'worker.py'), str(profile), mode]
        process = subprocess.Popen(control.bound_command(command), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        first = line(process).decode()
        assert first.startswith('CONTROLLER_READY '), first
        parent = state(root_unit)
        assert parent['MainPID'] == first.split()[-1] and parent['ActiveState'] == 'active'
        spec = dict(format='pult-construction-stop-profile-v1', mode='STOP_BOUND_CONSTRUCTION_ONLY',
            stop_entry=str(binding / 'constructor_stop.py'), stop_sha256=sha(binding / 'constructor_stop.py'),
            helper_entry=str(binding / 'constructor_child.py'), helper_sha256=sha(binding / 'constructor_child.py'),
            constructor_entry=str(root / 'entry/staged_host.py'), host_profile=str(root / 'host.json'),
            host_profile_sha256=sha(root / 'host.json'), supervisor_entry=str(binding / 'supervisor_v2.py'),
            legacy_core=str(binding / 'admission.py'),
            controller=dict(unit=root_unit, invocation_id=parent['InvocationID'], pid=int(parent['MainPID']),
                start_ticks=int(Path('/proc/' + parent['MainPID'] + '/stat').read_text().rsplit(') ', 1)[1].split()[19]),
                boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()),
            control=dict(root=str(owned), identity=ident(seen_control), lock_identity=ident(seen_control / 'constructor-stop.lock')),
            actor=dict(uid=65534, gid=65534), state_directory=dict(path=str(root / 'state'), identity=ident(root / 'state')))
        if mode == 'wrong_boot': spec['controller']['boot'] = str(uuid4())
        if mode == 'wrong_invocation': spec['controller']['invocation_id'] = uuid4().hex
        if mode == 'wrong_pid': spec['controller']['pid'] += 1
        if mode == 'wrong_start': spec['controller']['start_ticks'] += 1
        if mode == 'wrong_mode': spec['mode'] = 'WRITE'
        if mode == 'source_changed':
            with (binding / 'constructor_child.py').open('ab') as file: file.write(b'\n')
        if mode == 'control_mode': owned.chmod(0o755)
        if mode == 'state_mode': (root / 'state').chmod(0o755)
        if mode == 'busy_control':
            busy = os.open(owned / 'constructor-stop.lock', os.O_RDWR)
            fcntl.flock(busy, fcntl.LOCK_EX | fcntl.LOCK_NB)
        put(profile, canonical(spec))
        db_sha, credential_sha = sha(root / 'state/anchor.sqlite'), sha(root / 'secrets/reader.json')
        began = time.monotonic()
        process.stdin.write((sha(profile) + '\n').encode()); process.stdin.flush()
        out, err = process.communicate(timeout=60)
        elapsed = time.monotonic() - began
        result = json.loads(out) if out else None
        fence = owned / 'constructor-stop.claim'
        proof = root / 'constructed-proof.json'
        deferred = root / 'deferred-command.json'
        if mode == 'happy':
            assert process.returncode == 0 and not err and result['outcome'] == 'PASS', (out, err)
            assert result['same_process_repeat_refused'] and result['fence_preserved']
            assert proof.is_file() and json.loads(proof.read_bytes())['owner_lock_held']
            # A fresh controller, not just the same Python global, is denied by the permanent fence.
            retry_unit = 'pult-call-gate-' + uuid4().hex + '.service'
            retry = [('--unit=' + retry_unit) if a.startswith('--unit=') else a for a in command]
            retry_result = subprocess.run(control.bound_command(retry), input=b'', capture_output=True, timeout=8)
            assert not retry_result.stdout and (root / 'controller-starts').read_bytes() == b'START\n'
            kernel_receipt = condition_receipt(retry_unit, fence, helper.verifier)
        elif mode in ('kill_controller', 'late_launch'):
            assert process.returncode != 0 and result is None, (out, err)
            if mode == 'kill_controller':
                assert proof.is_file() and json.loads(proof.read_bytes())['owner_lock_held']
            else:
                assert deferred.is_file() and not proof.exists()
                queued = json.loads(deferred.read_bytes())
                assert next(a for a in queued if a.startswith('--unit=')) == '--unit=' + json.loads(fence.read_bytes())['unit']
                late = subprocess.run(queued, input=b'', capture_output=True, timeout=8)
                # A condition-skipped systemd job may return zero. Prove no
                # execution instead of misinterpreting CLI success as a launch.
                assert not late.stdout and not (root / 'state/late-child-executed').exists(), (late.stdout, late.stderr)
                assert (root / 'controller-starts').read_bytes() == b'START\n', 'CONTROLLER_REACTIVATED'
                assert not proof.exists()
                kernel_receipt = condition_receipt(root_unit, fence, helper.verifier)
        elif mode in ('freeze', 'tail', 'cleanup_unknown', 'filesystem'):
            assert process.returncode == 1 and not err and result['outcome'] == 'FAIL', (out, err)
            assert result['error_type'] == 'Unknown' and result['submitted'] and fence.is_file()
            if mode != 'filesystem':
                assert proof.is_file() and json.loads(proof.read_bytes())['owner_lock_held']
            if mode in ('freeze', 'tail'): assert 37 < elapsed < 57, elapsed
            if mode == 'tail': assert len(json.loads(proof.read_bytes())['pids']) >= 2
            if mode == 'filesystem':
                probe = json.loads((root / 'state/filesystem-proof.json').read_bytes())
                assert probe.pop('state_directory') == 'WRITABLE' and set(probe.values()) == {'DENIED'}, probe
        else:
            assert process.returncode == 1 and not err and result['outcome'] == 'FAIL', (out, err)
            assert result['error_type'] == 'Refused' and not result['submitted'] and not fence.exists(), result
            assert not proof.exists()
            if mode == 'manager_alias': assert result['diagnostic'] == 'trusted construction stop profile refused' or 'manager' in result['diagnostic']
        if fence.is_file():
            claim = json.loads(fence.read_bytes()); actor_unit = claim['unit']
            assert stopped(actor_unit), 'ACTOR_SURVIVED_CONTROLLER_OR_CLEANUP'
            assert fence.read_bytes() == canonical(claim), 'PERMANENT_FENCE_CHANGED'
        assert sha(root / 'state/anchor.sqlite') == db_sha and sha(root / 'secrets/reader.json') == credential_sha
        assert not any(Path(str(root / 'state/anchor.sqlite') + suffix).exists() for suffix in ('-journal', '-wal', '-shm'))
        lock = os.open(root / 'state/owner.lock', os.O_RDWR)
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally: os.close(lock)
        answer = dict(case=mode, outcome='PASS', seconds=round(elapsed, 3), database_unchanged=True,
            credential_unchanged=True, owner_lock_released=True, descendants_empty=True,
            construction_held_proved=proof.exists(), permanent_fence=fence.exists())
        answer['kernel_fence_receipt'] = kernel_receipt
        return answer
    finally:
        if busy is not None: os.close(busy)
        # Quiesce the only producer BEFORE snapshotting its durable unit ledger.
        # On any uncertainty, do not remove this tree: outer supervision first
        # closes launch admission, then stops producers and scans the ledger.
        if root_unit is not None:
            supervisor.stop_owned(root_unit)
            assert empty(root_unit), 'ROOT_PRODUCER_STILL_LIVE'
        if process is not None:
            if process.poll() is None: process.kill()
            process.communicate(timeout=5)
        owned_records = set(Path(scope['units']).iterdir()) - prior_units
        for record in sorted(owned_records):
            unit = json.loads(record.read_bytes())['unit']
            supervisor.stop_owned(unit)
            assert empty(unit), 'DIRECT_CGROUP_POSTCHECK_FAILED'
        assert set(Path(scope['units']).iterdir()) - prior_units == owned_records, 'LATE_UNIT_REGISTRATION'
        assert root.parent == base and ident(root) == root_id and root.name.startswith('case-')
        shutil.rmtree(root)

def native():
    assert os.geteuid() == 0 and sys.flags.isolated and not sys.flags.optimize
    assert [r.split(':', 1)[0].strip() for r in Path('/proc/self/net/dev').read_text().splitlines()[2:]] == ['lo']
    control = load(REPO / 'ops/backup/tests/custodian_v2/controller/scope_control.py', 'matrix_scope')
    supervisor = load(REPO / 'ops/backup/tests/custodian_v2/controller/ci_gate.py', 'matrix_cleanup')
    from types import SimpleNamespace
    helper = SimpleNamespace(gate=load(ROOT.parent / 'host/run_gate.py', 'immutable_fixture'))
    verifier = load(ROOT / 'verify_receipt.py', 'native_inventory')
    helper.verifier = verifier
    inventory = verifier.expected_paths()
    hashes = {name: sha(file) for name, file in inventory.items()}
    scope = control.read_scope()
    base = Path(tempfile.mkdtemp(prefix='stop-matrix-', dir=scope['work'])).resolve(); base.chmod(0o755)
    base_id = ident(base)
    results = []
    try:
        original, _, _ = helper.gate.template(base, REPO, ROOT.parent / 'wheels')
        for mode in MODES:
            result = case(mode, base, original, scope, control, supervisor, helper)
            results.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
        assert [item['case'] for item in results] == list(MODES)
    finally:
        # If a per-case cleanup is uncertain, leave the tree to the independent
        # outer owner, which closes the launch gate and kills producers FIRST.
        for record in Path(scope['units']).iterdir():
            assert empty(json.loads(record.read_bytes())['unit']), 'TREE_TEARDOWN_WITH_LIVE_ACTOR_REFUSED'
        assert base.parent == Path(scope['work']) and ident(base) == base_id and base.name.startswith('stop-matrix-')
        shutil.rmtree(base)
    after = {name: sha(file) for name, file in verifier.expected_paths().items()}
    assert hashes == after
    report = dict(format='pult-stop-bound-construction-native-v1', scope='LOCAL_SYNTHETIC_CONSTRUCTION_AND_STOP_ONLY',
        outcome='PASS', tests=len(MODES), skips=0, failures=[], results=results,
        source_hashes_before=hashes, source_hashes_after=after, sources_unchanged=True,
        private_network=True, native_tree_removed=not base.exists(), remaining_owned_processes=[],
        journal_write_admitted=False, listener_admitted=False, deployment_admitted=False,
        remote_io_admitted=False, new_paid_resources=0, additional_spend_rub=0)
    target = ROOT / ('RUN_' + str(time.time_ns()) + '.json')
    with target.open('x') as stream:
        os.fchmod(stream.fileno(), 0o644)
        json.dump(report, stream, sort_keys=True, indent=2); stream.write('\n')
    print('RECEIPT ' + str(target), flush=True)
    print('REAL_CONSTRUCTION_STOP_MATRIX_PASS ' + str(len(results)), flush=True)
    return 0

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_stop_binding_gate_refused"}'); return 2
    if (sys.argv[1:] != ['--reviewed-by-main'] or sys.platform != 'linux' or os.geteuid() != 0
            or sys.version_info[:2] != (3, 12) or not sys.flags.isolated or not sys.flags.no_site): return 2
    return native()

if __name__ == '__main__': raise SystemExit(main())
