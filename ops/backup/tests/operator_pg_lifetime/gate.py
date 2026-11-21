"""Six-case synthetic PG/operator lifetime composition, never PG admission.

Root test orchestrator only. No production credentials, network listeners or
published ports. The accepted eleven-case and eight-case gates stay separate.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from uuid import uuid4
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
CASES = ('authorized', 'close_then_raise', 'blocked_close_hang',
         'controller_death', 'wrong_peer', 'restored')
OLD = ('journal', 'anchor', 'ipc', 'operator', 'stop', 'supervisor', 'core',
       'profile', 'host', 'bootstrap', 'worker')
ACTOR, GROUP, OPERATOR, STRANGER = 46201, 46202, 46202, 46203
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
FLAGS = dict(identity_enrollment_admitted=False, journal_dispatch_admitted=False,
             purge_or_restore_admitted=False)
SYNTHETIC = dict(account_id='c7461350-9188-4bf1-bc6c-c4a5b9455bec',
    draft_id='ff636b10-6ccb-4b1b-9394-df59af74b793',
    closure_epoch='2026-10-21T12:00:00.000000Z', reviewed_at='2026-11-21T12:00:00.000000Z',
    policy='normal-close-30d-v1')


class Refused(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Refused(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False)+'\n').encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ident(path):
    info = path.lstat()
    return dict(device=info.st_dev, inode=info.st_ino)


def capture(file, maximum):
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and 0 <= before.st_size <= maximum, 'bounded_regular_input_required')
        raw = os.pread(fd, maximum+1, 0)
        after = os.fstat(fd)
        need(len(raw) == before.st_size and ident(file) == dict(device=before.st_dev, inode=before.st_ino)
             and all(getattr(before,k) == getattr(after,k) for k in
                 ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')), 'captured_input_changed')
        return raw
    finally:
        os.close(fd)


def put(file, raw, mode=0o444):
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with file.open('xb') as output:
        os.fchmod(output.fileno(), mode)
        need(output.write(raw) == len(raw), 'incomplete_fixture_write')
        output.flush()
        os.fsync(output.fileno())


def module(name, raw, file):
    need(name not in sys.modules, 'foreign_test_module')
    result = ModuleType(name)
    result.__file__ = str(file)
    sys.modules[name] = result
    try:
        exec(compile(raw, str(file), 'exec', dont_inherit=True, optimize=0), result.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return result


def sources():
    manifest = json.loads(capture(HERE/'source-manifest.json', 20000))
    need(manifest['format'] == 'pult-operator-pg-joint-test-sources-v1'
         and manifest['status'] == 'TEST_COMPOSITION_ONLY_NO_PG_RUNTIME_ADMISSION'
         and tuple(manifest['cases']) == CASES and manifest['flags'] == FLAGS
         and manifest['admitted_operations'] == [] and len(manifest['sources']) == 14
         and len(manifest['wheels']) == 4, 'exact_test_source_manifest_required')
    captured = {}
    for name, entry in manifest['sources'].items():
        relative = PurePosixPath(entry['path'])
        need(not relative.is_absolute() and '..' not in relative.parts
             and str(relative) == entry['path'], 'fixed_relative_source_required')
        # Checkout newline normalization is assembly only, never a worker loader.
        body = capture(REPO/entry['path'], 150000).replace(b'\r\n', b'\n')
        need(sha(body) == entry['sha256'], 'frozen_input_pin_changed')
        captured[name] = body
    return manifest, captured


def wheel_bodies(directory, manifest):
    need(directory.is_absolute() and directory.resolve() == directory,
         'absolute_wheel_directory_required')
    result = {}
    for name, expected in manifest['wheels'].items():
        need(PurePosixPath(name).name == name and name.endswith('.whl'), 'fixed_wheel_name_required')
        raw = capture(directory/name, 12000000)
        need(sha(raw) == expected, 'wheel_pin_refused')
        result[name] = raw
    return result


def install_wheels(root, bodies):
    deps = root/'deps'
    deps.mkdir(mode=0o755)
    total = count = 0
    entries = {}
    for raw in bodies.values():
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                need(not path.is_absolute() and '..' not in path.parts and path.parts
                     and str(path) == info.filename.rstrip('/')
                     and not stat.S_ISLNK(info.external_attr >> 16) and not info.flag_bits & 1
                     and path.suffix not in ('.pyc', '.pth'), 'unsafe_wheel_member')
                if info.is_dir():
                    (deps/path).mkdir(parents=True, exist_ok=True, mode=0o755)
                    continue
                total += info.file_size
                count += 1
                need(total <= 50000000 and count <= 5000, 'wheel_expansion_bound')
                body = archive.read(info)
                need(len(body) == info.file_size and str(path) not in entries, 'wheel_collision')
                put(deps/path, body)
                entries[str(path)] = sha(body)
    put(root/'deps-manifest.json', canonical(entries))
    return sha(canonical(entries))


def preflight():
    need(sys.platform == 'linux' and os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0
         and sys.implementation.name == 'cpython' and sys.version_info[:2] == (3,12)
         and Path(sys.executable).resolve() == Path('/usr/bin/python3.12')
         and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
         and not sys.flags.optimize and len(os.listdir('/proc/self/task')) == 1,
         'fixed_native_runtime_required')
    need(Path('/proc/1/comm').read_text().strip() == 'systemd'
         and Path('/sys/fs/cgroup/cgroup.controllers').is_file(), 'real_PID1_cgroup2_required')
    need(os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net'), 'private_network_required')
    import pwd
    import grp
    for value in (ACTOR, GROUP, STRANGER):
        for lookup in (pwd.getpwuid, grp.getgrgid):
            try:
                lookup(value)
            except KeyError:
                continue
            raise Refused('synthetic_NSS_identity_already_exists')


def wait_record(file, process, seconds):
    until = time.monotonic()+seconds
    while not file.exists():
        need(process.poll() is None and time.monotonic() < until, 'fixture_milestone_missing')
        time.sleep(.025)
    info = file.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid in (0, ACTOR)
         and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8192, 'bounded_evidence_required')
    return json.loads(file.read_bytes())


def client_argv(root, case, cfg_sha, bootstrap_sha, profile_sha, uid):
    return ['/usr/bin/setpriv', '--reuid='+str(uid), '--regid='+str(GROUP),
        '--clear-groups', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all',
        '--no-new-privs', '/usr/bin/python3.12', '-I', '-S', '-B', str(root/'fixture.py'),
        'client', str(root), case, cfg_sha, bootstrap_sha, profile_sha]


def run_case(case, manifest, captured, wheels, baseline, pg_module, payload):
    temporary = tempfile.TemporaryDirectory(prefix='pult-op-pg-', dir='/tmp')
    root = Path(temporary.name).resolve()
    root.chmod(0o755)
    original = ident(root)
    pg = pg_module.PGFixture(root, 'joint-'+case)
    unit = 'pult-call-gate-'+uuid4().hex+'.service'
    description = 'PULT joint PG controller '+uuid4().hex
    producer = peer = None
    profile_sha = None
    passed = False
    evidence = None
    try:
        for name in ('release', 'control', 'ipc'):
            (root/name).mkdir(mode=0o755)
        os.chown(root/'ipc', ACTOR, GROUP)
        (root/'ipc').chmod(0o750)
        put(root/'control/operator-host.lock', b'', 0o600)
        for name, body in captured.items():
            put(root/'release'/manifest['sources'][name]['path'], body)
        put(root/'fixture.py', payload)
        deps_sha = install_wheels(root, wheels)
        pg_config = pg.start(case)
        pg_baseline = pg.baseline
        if case == 'blocked_close_hang':
            pg.hold_lock()
        bootstrap_sha = manifest['sources']['bootstrap']['sha256']
        cfg = dict(format='pult-operator-pg-joint-fixture-v1', case=case, root=str(root),
            fixture_sha256=sha(payload), bootstrap_sha256=bootstrap_sha,
            dependencies_manifest_sha256=deps_sha, pg=pg_config,
            backend_sources={name:dict(path=str(root/'release'/manifest['sources'][name]['path']),
                sha256=manifest['sources'][name]['sha256']) for name in ('policy','reviewer')})
        cfg_sha = sha(canonical(cfg))
        put(root/'test.json', canonical(cfg))
        props = dict(Type='exec', ExitType='main', RemainAfterExit='no', User='0', Group='0',
            KillMode='control-group', KillSignal='SIGTERM', SendSIGKILL='yes', FinalKillSignal='SIGKILL',
            TimeoutStopSec='2', Restart='no', RuntimeMaxSec='45', PrivateNetwork='yes',
            NoNewPrivileges='yes', ConditionPathExists='!'+str(root/'control/constructor-stop.claim'),
            Description=description)
        argv = ['/usr/bin/systemd-run', '--quiet', '--pipe', '--no-ask-password',
                '--expand-environment=no', '--unit='+unit]
        argv += ['--property='+key+'='+value for key,value in sorted(props.items())]
        argv += ['/usr/bin/python3.12','-I','-S','-B',str(root/'fixture.py'),
                 'controller',str(root),case,cfg_sha,bootstrap_sha]
        need(baseline.state(unit).get('LoadState') == 'not-found', 'unit_collision_never_adopt')
        producer = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True, env=ENV)
        first = baseline.line(producer, 8)
        view = baseline.state(unit)
        pid = int(view['MainPID'])
        need(first == 'CONTROLLER_READY '+str(pid) and view['ActiveState'] == 'active',
             'original_controller_not_confirmed')
        profile = dict(format='pult-offline-operator-host-v1', mode='SYNTHETIC_REVIEW_ENDPOINT_ONLY',
            operation=str(uuid4()), release=dict(root=str(root/'release'), identity=ident(root/'release')),
            controller=dict(unit=unit, invocation_id=view['InvocationID'],
                **baseline.process_identity(pid), boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()),
            control=dict(root=str(root/'control'), identity=ident(root/'control'),
                         lock_identity=ident(root/'control/operator-host.lock')),
            actor=dict(uid=ACTOR,gid=GROUP), operator_uid=OPERATOR,
            ipc=dict(path=str(root/'ipc'), identity=ident(root/'ipc')), synthetic=SYNTHETIC,
            sources={name:dict(path=str(root/'release'/manifest['sources'][name]['path']),
                sha256=manifest['sources'][name]['sha256']) for name in OLD})
        profile_sha = sha(canonical(profile))
        put(root/'profile.json', canonical(profile))
        began = time.monotonic()
        producer.stdin.write((profile_sha+'\n').encode('ascii'))
        producer.stdin.flush()
        ready = wait_record(root/'ready.json', producer, 9)
        need(ready['unit'] != unit and re.fullmatch(r'pult-call-gate-[0-9a-f]{32}\.service',ready['unit']),
             'owned_actor_unit_required')
        peer = subprocess.Popen(client_argv(root,case,cfg_sha,bootstrap_sha,profile_sha,
                    STRANGER if case == 'wrong_peer' else OPERATOR),
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,env=ENV)
        death_backend = None
        if case == 'controller_death':
            held = wait_record(root/'ipc/driver-close-enter.json',producer,8)
            observation = pg.observe()
            rows = observation['reader_backends']
            need(len(rows) == 1 and rows[0]['pid'] == held['pid']
                 and observation['rows_sha256'] == pg_baseline, 'held_reader_not_independently_confirmed')
            death_backend = rows[0]['pid']
            fd = os.pidfd_open(pid)
            try:
                need(baseline.process_identity(pid)['start_ticks'] == profile['controller']['start_ticks']
                     and baseline.state(unit)['InvocationID'] == profile['controller']['invocation_id'],
                     'original_controller_changed_before_signal')
                signal.pidfd_send_signal(fd,signal.SIGKILL)
            finally:
                os.close(fd)
        peer_out, peer_err = peer.communicate(timeout=14)
        output, error = producer.communicate(timeout=26)
        elapsed = time.monotonic()-began
        need(len(peer_out) <= 4096 and len(peer_err) <= 4096
             and len(output) <= 8192 and len(error) <= 4096, 'bounded_role_output_required')
        for owned in (unit,ready['unit']):
            until = time.monotonic()+6
            while not baseline.empty(owned):
                need(time.monotonic() < until, 'owned_cgroup_not_empty')
                time.sleep(.025)
        if case == 'blocked_close_hang':
            lock = wait_record(root/'ipc/lock-timeout.json',producer,1)
            need(lock['sqlstate'] == '55P03', 'real_lock_timeout_not_observed')
            pg.release_lock()
        until = time.monotonic()+5
        observed = pg.observe()
        while observed['reader_backends']:
            need(time.monotonic() < until, 'reader_backend_retained_before_pg_teardown')
            time.sleep(.05)
            observed = pg.observe()
        need(observed['rows_sha256'] == pg_baseline, 'application_rows_changed')
        if case == 'controller_death':
            need(producer.returncode != 0 and not output and peer.returncode != 0 and not peer_out,
                 'dead_controller_emitted_receipt_or_observation')
            receipt = None
            need((root/'control/constructor-stop.claim').is_file(), 'dead_controller_claim_missing')
        else:
            need(producer.returncode == 0 and not error, 'controller_role_failed')
            answer = json.loads(output)
            need(answer['repeat_refused'] is True, 'controller_repeat_not_refused')
            receipt = answer['parent_receipt']
            need(all(receipt.get(k) is False for k in FLAGS)
                 and receipt['unit'] == ready['unit'] and receipt['cgroup_empty'] is True,
                 'nonadmitting_receipt_or_cleanup_required')
            terminal = wait_record(root/'precleanup.json',producer,1)
            if case == 'authorized':
                need(peer.returncode == 0 and not peer_err, 'authorized_operator_failed')
                early = json.loads(peer_out)
                need(early['kind'] == 'EARLY_NONADMITTING_OBSERVATION_NOT_PARENT_RECEIPT'
                     and all(early['observation'].get(k) is False for k in FLAGS)
                     and receipt['status'] == 'OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION'
                     and receipt['observation_sha256'] == sha(canonical(early['observation']))
                     and all(receipt[k] is True for k in ('cleanup_confirmed','pipe_eof_confirmed','child_reaped'))
                     and terminal['Result'] == 'success', 'positive_joint_lifetime_not_confirmed')
                closed = wait_record(root/'ipc/driver-closed.json',producer,1)
                pool = wait_record(root/'ipc/pool-state.json',producer,1)
                need(pool == dict(connections=1,cleanup_failed=False,repeat_refused=False,
                    connections_after_repeat=0,native_closed_before_observation=True)
                    and closed['pid'] > 0, 'native_close_before_observation_not_confirmed')
            else:
                need(peer.returncode != 0 and not peer_out and peer_err == b'joint_fixture_refused\n'
                     and receipt['status'] == 'UNKNOWN_REQUIRES_REVIEW'
                     and receipt['observation_sha256'] is None, 'failed_review_falsely_confirmed')
                if case == 'blocked_close_hang':
                    need(terminal['Result'] == 'timeout' and terminal['ExecMainCode'] == '2'
                         and terminal['ExecMainStatus'] == '9' and elapsed >= 16,
                         'resistant_close_not_physically_SIGKILL_stopped')
                elif case == 'wrong_peer':
                    need(not (root/'ipc/reviewer-called.json').exists()
                         and not (root/'ipc/connection-open.json').exists(), 'wrong_peer_caused_checkout')
                else:
                    pool = wait_record(root/'ipc/pool-state.json',producer,1)
                    need(pool['connections'] == 1 and (root/'ipc/driver-closed.json').is_file(),
                         'failed_review_physical_close_not_confirmed')
                    if case == 'close_then_raise':
                        need(pool['cleanup_failed'] is True and pool['repeat_refused'] is True
                             and pool['connections_after_repeat'] == 1, 'cleanup_latch_not_sticky')
                    else:
                        need(pool['cleanup_failed'] is False, 'restored_case_wrong_refusal')
        evidence = dict(case=case,outcome='PASS',seconds=round(elapsed,3),
            legacy_supervision_receipt_test_payload_only=receipt, reader_backends_absent_before_pg_teardown=True,
            application_rows_unchanged=True, held_reader_before_controller_death=death_backend,
            production_pg_source_admission_proved=False, **FLAGS)
        passed = True
    finally:
        try:
            baseline.stop_owned(unit,description)
            ledger = root/'actor.json'
            if ledger.exists():
                actor = json.loads(ledger.read_bytes())
                need(actor['approval'] == profile_sha, 'actor_ledger_profile_changed')
                baseline.stop_owned(actor['unit'],'PULT offline operator '+profile_sha)
            for process in (peer,producer):
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=3)
            if pg.locker is not None:
                pg.release_lock()
            if pg.pg is not None and not pg.closed:
                # Observe even after a refused/early role. Teardown must never
                # conceal a retained reader or change the failed case to PASS.
                pg.observe()
            pg.close()
            need(root.parent == Path('/tmp') and root.name.startswith('pult-op-pg-')
                 and ident(root) == original and not root.is_symlink(), 'scope_cleanup_identity_changed')
            if passed:
                temporary.cleanup()
            else:
                temporary._finalizer.detach()
                failure = dict(outcome='FAIL',case=case,evidence_root=str(root),
                    scope_identity=original,controller_unit=unit,container_id=pg.cid,probe_id=pg.probe)
                put(root/'failure-scope.json',canonical(failure),0o600)
                print(json.dumps(failure,sort_keys=True),file=sys.stderr,flush=True)
        except BaseException:
            temporary._finalizer.detach()
            failure = dict(outcome='FAIL',case=case,evidence_root=str(root),
                scope_identity=original,controller_unit=unit,container_id=pg.cid,probe_id=pg.probe)
            if not (root/'failure-scope.json').exists():
                put(root/'failure-scope.json',canonical(failure),0o600)
            print(json.dumps(failure,sort_keys=True),file=sys.stderr,flush=True)
            raise
    evidence['owned_resources_cleanup_confirmed'] = True
    return evidence


def valid_results(results):
    if type(results) is not list or len(results) != 6:
        return False
    for case, item in zip(CASES, results):
        if (type(item) is not dict or item.get('case') != case or item.get('outcome') != 'PASS'
                or 'legacy_supervision_receipt_test_payload_only' not in item
                or item.get('owned_resources_cleanup_confirmed') is not True
                or item.get('reader_backends_absent_before_pg_teardown') is not True
                or item.get('application_rows_unchanged') is not True
                or item.get('production_pg_source_admission_proved') is not False
                or any(item.get(key) is not False for key in FLAGS)):
            return False
        receipt = item.get('legacy_supervision_receipt_test_payload_only')
        if case == 'controller_death':
            if receipt is not None:
                return False
        elif (type(receipt) is not dict or any(receipt.get(k) is not False for k in FLAGS)
              or any(receipt.get(k) is not True for k in
                     ('cleanup_confirmed','cgroup_empty','pipe_eof_confirmed','child_reaped'))
              or 'observation_sha256' not in receipt):
            return False
        elif case == 'authorized':
            if (receipt.get('status') != 'OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION'
                    or type(receipt['observation_sha256']) is not str
                    or not re.fullmatch('[0-9a-f]{64}',receipt['observation_sha256'])):
                return False
        elif receipt.get('status') != 'UNKNOWN_REQUIRES_REVIEW' or receipt.get('observation_sha256') is not None:
            return False
    return True


def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_joint_pg_gate_refused"}')
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheels-dir', type=Path, required=True)
    args = parser.parse_args()
    preflight()
    manifest,captured = sources()
    wheels = wheel_bodies(args.wheels_dir,manifest)
    payload = capture(HERE/'fixture.py',100000).replace(b'\r\n',b'\n')
    pg_body = capture(HERE/'pg_fixture.py',100000).replace(b'\r\n',b'\n')
    before = {str(p):sha(capture(p,150000)) for p in
        (Path(__file__),HERE/'fixture.py',HERE/'pg_fixture.py',HERE/'source-manifest.json')}
    baseline = module('_joint_legacy_helpers',captured['baseline_helper'],
                      REPO/manifest['sources']['baseline_helper']['path'])
    pg_module = module('_joint_owned_pg',pg_body,HERE/'pg_fixture.py')
    need(pg_module.IMAGE == manifest['postgres_image'], 'pinned_pg_image_changed')
    results = [run_case(case,manifest,captured,wheels,baseline,pg_module,payload) for case in CASES]
    _,after_sources = sources()
    need(all(captured[k] == after_sources[k] for k in captured)
         and all(sha(capture(Path(p),150000)) == wanted for p,wanted in before.items())
         and valid_results(results),
         'complete_unchanged_six_case_gate_required')
    print(json.dumps(dict(format='pult-operator-pg-joint-native-v1',outcome='PASS',tests=6,skips=0,
        scope='SYNTHETIC_TEST_COMPOSITION_ONLY',production_pg_source_admission_proved=False,
        test_source_hashes={Path(p).name:sha(capture(Path(p),150000).replace(b'\r\n',b'\n')) for p in before},
        frozen_input_hashes={k:v['sha256'] for k,v in manifest['sources'].items()},wheel_hashes=manifest['wheels'],
        results=results,remaining_owned_resources=[],**FLAGS),sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        code = error.args[0] if type(error) is Refused and len(error.args) == 1 else 'joint_pg_gate_failed'
        if type(code) is not str or not re.fullmatch('[a-zA-Z0-9_]{1,80}', code):
            code = 'joint_pg_gate_failed'
        print(json.dumps(dict(outcome='FAIL', reason=code, error_type=type(error).__name__)))
        raise SystemExit(1)
