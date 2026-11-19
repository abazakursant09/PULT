"""Root synthetic constructor proof in private Linux PID/network namespaces."""
import sys
sys.dont_write_bytecode = True
import ast
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import grp
import select
import shutil
import signal
import sqlite3
import struct
import subprocess
import tempfile
import time
from uuid import uuid4

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
SOURCE = REPO / 'ops/backup/custodian_v2'
ENTRY_SHA = 'faf627fef860d560d01605e3717c9939fe81c2c4156b231b03e94c57320c2bae'
WORKER_SHA = '5f64b491e201e01444cacba6cdd3d9221083b7e850c70e9640df6d2edc903d6a'
UID, GID = 47003, 47004
CASES = ('happy', 'pending_prepared', 'pending_dispatched', 'repeat', 'wrong_approval',
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
         'wal_sidecar', 'shm_sidecar', 'wal_header', 'recovery_drift')
POSITIVE = {'happy', 'pending_prepared', 'pending_dispatched', 'repeat'}
RECOVERY_REJECT = {'hot_journal', 'cold_journal', 'wal_sidecar', 'shm_sidecar', 'wal_header'}
RETAINED = {'reader_constructor', 'reader_close', 'capsule_close', 'profile_drift', 'secret_drift', 'lease_drift',
            'post_capsule_owner_check', 'partial_capsule_close', 'recovery_drift'} | RECOVERY_REJECT
CONSTRUCTED = POSITIVE | (RETAINED - RECOVERY_REJECT - {'reader_constructor'})
NO_BOOTSTRAP = {'wrong_approval', 'old_profile', 'wrong_mode', 'extra_field', 'duplicate', 'noncanonical',
    'uid_wrong', 'writer_same', 'wrong_bounds', 'intake_tamper', 'optimized', 'double_state',
    'double_secret', 'double_source', 'profile_alias', 'old_source_profile', 'host_profile_symlink',
    'host_profile_hardlink', 'host_profile_userowned', 'host_profile_writable', 'host_profile_acl',
    'host_profile_parent_acl', 'source_profile_hash'}
INTAKE_ONLY = {'stage_tamper', 'preloaded', 'foreign_path'}
COLD_CAPTURE = {'wheel_tamper', 'source_extra'}


def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode('ascii')


def put(file, body, mode=0o644, uid=0, gid=0):
    file.write_bytes(body)
    os.chown(file, uid, gid)
    file.chmod(mode)


def identity(file):
    st = file.stat()
    return {'device': st.st_dev, 'inode': st.st_ino}


def load(file, name):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def template(base, repo, wheels):
    source = repo / 'ops/backup/custodian_v2'
    helper_file = repo / 'ops/backup/tests/custodian_v2/support.py'
    assert sha(helper_file) == 'd343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d'
    assert sha(source / 'source-profile.json') == '8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'
    helper = load(helper_file, 'trusted_fixture_helper')
    helper.WHEEL_DIR = wheels  # Fixture input path only; helper verifies all seven hashes.
    catalogue = json.loads((source / 'source-profile.json').read_bytes())
    root = base / 'template'
    release, entry = root / 'release', root / 'entry'
    release.mkdir(parents=True)
    entry.mkdir()
    inputs = {'helper': helper_file, 'catalogue': source / 'source-profile.json',
              'entry': SOURCE / 'host/staged_host.py', 'worker': HERE / 'worker.py', 'gate': Path(__file__),
              'scope': HERE.parent / 'controller/scope_control.py',
              'supervisor': HERE.parent / 'controller/ci_gate.py',
              'wrapper': HERE / 'ci_gate.py', 'verifier': HERE / 'verify_receipt.py',
              'draft': SOURCE / 'host/source-profile.json'}
    for name, digest in catalogue['sources'].items():
        file = helper.source_path(name)
        assert sha(file) == digest
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        inputs['source:' + name] = file
    shutil.copyfile(source / 'source-profile.json', release / 'source-profile.json')
    helper.unpack_verified_wheels(release / 'deps')
    (release / 'wheels').mkdir()
    for name in helper.wheel_pins():
        shutil.copyfile(wheels / name, release / 'wheels' / name)
        inputs['wheel:' + name] = wheels / name
    for name, file in {'staged_host.py': SOURCE / 'host/staged_host.py',
                       'staged_intake.py': source / 'intake/staged_intake.py',
                       'cold_host.py': source / 'cold/cold_host.py',
                       'staged_ipc.py': source / 'staged/staged_ipc.py'}.items():
        shutil.copyfile(file, entry / name)
        if name != 'staged_host.py': inputs['entry:' + name] = file
    put(root / 'entry.sha256', (sha(SOURCE / 'host/staged_host.py') + '\n').encode())
    for file in root.rglob('*'):
        file.chmod(0o755 if file.is_dir() else 0o644)
    root.chmod(0o755)
    worker = base / 'worker.py'
    shutil.copyfile(HERE / 'worker.py', worker)
    worker.chmod(0o644)
    return root, worker, inputs


def fixture(base, original, mode):
    root = base / ('case-' + uuid4().hex)
    shutil.copytree(original, root)
    for name in ('state', 'secrets'):
        file = root / name
        file.mkdir(mode=0o700)
        os.chown(file, UID, GID)
    state, lock, secret = root / 'state/anchor.sqlite', root / 'state/owner.lock', root / 'secrets/reader.json'
    scope, generation = 'synthetic-staged-host', str(uuid4())
    genesis = hashlib.sha256(canonical(dict(format='pult-erasure-event-v2', kind='genesis', scope=scope, generation=generation))).hexdigest()
    tree = ast.parse((root / 'release/candidate/anchor_v2.py').read_bytes())
    literals = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id in {'META_SQL', 'RECEIPTS_SQL', 'APP_ID', 'SCHEMA'}}
    conn = sqlite3.connect(state)
    try:
        conn.execute(literals['META_SQL'])
        conn.execute(literals['RECEIPTS_SQL'])
        conn.execute('PRAGMA application_id=' + str(literals['APP_ID']))
        conn.execute('PRAGMA user_version=' + str(literals['SCHEMA']))
        location = 'https://s3.ru-3.storage.selcloud.ru/fake-host-bucket/pult/erasure-v2/' + scope + '/' + generation + '/events/'
        conn.execute('INSERT INTO anchor_v2 VALUES(1,?,?,?,0,?,0,NULL)', (scope, generation, location, genesis))
        if mode.startswith('pending_'):
            event = dict(format='pult-erasure-event-v2', scope=scope, generation=generation, sequence=1,
                         previous=genesis, event=dict(purge_id=str(uuid4()), subject_id=str(uuid4()), phase='intent'))
            target = hashlib.sha256(canonical(event)).hexdigest()
            event['digest'] = target
            phase = 'PREPARED' if mode == 'pending_prepared' else 'DISPATCHED'
            attempt = str(uuid4())
            conn.execute('INSERT INTO attempts_v2 VALUES(?,?,?,?,?,?,?)', (attempt, 0, genesis, 1, target, canonical(event), phase))
            conn.execute('UPDATE anchor_v2 SET revision=?,pending=?', (1 if phase == 'PREPARED' else 2, attempt))
        if mode == 'bad_schema':
            conn.execute('PRAGMA user_version=1')
        if mode == 'bad_location':
            conn.execute("UPDATE anchor_v2 SET location='wrong'")
        conn.commit()
    finally:
        conn.close()
    os.chown(state, UID, GID)
    state.chmod(0o600)
    put(lock, b'', 0o600, UID, GID)
    put(secret, canonical(dict(access_key='FAKE_HOST_READER', secret_key='synthetic-never-a-working-key')), 0o400, UID, GID)
    intake = dict(format='pult-staged-source-intake-profile-v1', mode='SOURCES_ONLY_NO_HOST_CAPABILITY',
                  release_root=str(root / 'release'), cold_entry=str(root / 'entry/cold_host.py'),
                  staged_source=str(root / 'entry/staged_ipc.py'))
    put(root / 'source.json', canonical(intake))
    value = dict(format='pult-staged-host-construction-profile-v1', mode='CONSTRUCT_ONLY_NO_ADMISSION',
                 source_intake=dict(entry=str(root / 'entry/staged_intake.py'), profile_file=str(root / 'source.json'),
                                    profile_sha256=sha(root / 'source.json')),
                 scope=scope, generation=generation, custodian_uid=UID, socket_gid=GID, writer_uid=47005, writer_gid=GID,
                 state_file=str(state), state_identity=identity(state), lease_file=str(lock), lease_identity=identity(lock),
                 reader_credential_file=str(secret), reader_credential_identity=identity(secret), reader_access_key='FAKE_HOST_READER',
                 s3=dict(endpoint='https://s3.ru-3.storage.selcloud.ru', region='ru-3', bucket='fake-host-bucket'),
                 bounds=dict(max_receipts=1024, max_state_bytes=2097152, record_bytes=2048))
    if mode == 'old_profile': value['format'] = 'pult-schema2-cold-host-profile-v1'
    elif mode == 'wrong_mode': value['mode'] = 'SERVE'
    elif mode == 'extra_field': value['transport'] = 'live'
    elif mode == 'uid_wrong': value['custodian_uid'] += 100
    elif mode == 'writer_same': value['writer_uid'] = UID
    elif mode == 'wrong_bounds': value['bounds']['max_receipts'] += 1
    elif mode in ('state_inode', 'lease_inode'):
        # Never accidentally turn this into the earlier duplicate-identity case.
        other_inode = 1 + max(value[k]['inode'] for k in
            ('state_identity', 'lease_identity', 'reader_credential_identity'))
        value['state_identity' if mode == 'state_inode' else 'lease_identity']['inode'] = other_inode
    elif mode == 'secret_inode':
        value['reader_credential_identity']['inode'] = 1 + max(value[k]['inode'] for k in
            ('state_identity', 'lease_identity', 'reader_credential_identity'))
    elif mode == 'state_mode': state.chmod(0o644)
    elif mode == 'state_acl':
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *row) for row in (
            (1, 6, 0xffffffff), (2, 0, 47005), (4, 0, 0xffffffff), (16, 0, 0xffffffff), (32, 0, 0xffffffff)))
        os.setxattr(state, 'system.posix_acl_access', acl)
    elif mode == 'secret_owner': os.chown(secret, 0, 0)
    elif mode == 'secret_mode': secret.chmod(0o444)
    elif mode == 'secret_hardlink': os.link(secret, root / 'secrets/other')
    elif mode in ('secret_wrong_key', 'secret_extra'):
        data = dict(access_key='WRONG' if mode == 'secret_wrong_key' else 'FAKE_HOST_READER', secret_key='synthetic')
        if mode == 'secret_extra': data['writer'] = True
        put(secret, canonical(data), 0o400, UID, GID)
    elif mode == 'intake_tamper': put(root / 'entry/staged_intake.py', b'raise RuntimeError("UNTRUSTED")\n')
    elif mode == 'stage_tamper': put(root / 'entry/staged_ipc.py', b'raise RuntimeError("UNTRUSTED")\n')
    elif mode == 'wheel_tamper':
        file = next((root / 'release/wheels').iterdir())
        put(file, file.read_bytes() + b'x')
    elif mode == 'source_extra': put(root / 'release/extra.py', b'pass\n')
    elif mode == 'double_state':
        value['state_file'] = '/' + value['state_file']
        value['lease_file'] = '/' + value['lease_file']
    elif mode == 'double_secret': value['reader_credential_file'] = '/' + value['reader_credential_file']
    elif mode == 'double_source':
        intake['release_root'] = '/' + intake['release_root']
        put(root / 'source.json', canonical(intake))
        value['source_intake']['profile_sha256'] = sha(root / 'source.json')
    elif mode == 'source_profile_hash': value['source_intake']['profile_sha256'] = '0' * 64
    elif mode == 'hot_journal':
        # A valid one-page hot rollback journal. The control copy demonstrates
        # actual automatic recovery, not merely a file with a suggestive name.
        original = state.read_bytes()
        page_size = int.from_bytes(original[16:18], 'big')
        assert page_size == 4096 and len(original) % page_size == 0
        nonce = 123456789
        page = original[:page_size]
        checksum = (nonce + sum(page[i] for i in range(page_size - 200, 0, -200))) & 0xffffffff
        journal = (b'\xd9\xd5\x05\xf9\x20\xa1\x63\xd7' + struct.pack('>IIIII',
            1, nonce, len(original) // page_size, 512, page_size)).ljust(512, b'\0')
        journal += struct.pack('>I', 1) + page + struct.pack('>I', checksum)
        changed = bytearray(original)
        changed[60:64] = (999).to_bytes(4, 'big')
        put(state, bytes(changed), 0o600, UID, GID)
        put(Path(str(state) + '-journal'), journal, 0o600, UID, GID)
        control = root / 'recovery-control'
        control.mkdir()
        copy = control / 'anchor.sqlite'
        put(copy, bytes(changed), 0o600)
        put(Path(str(copy) + '-journal'), journal, 0o600)
        with sqlite3.connect(copy) as probe:
            assert probe.execute('PRAGMA user_version').fetchone()[0] == literals['SCHEMA']
        assert copy.read_bytes() == original and not Path(str(copy) + '-journal').exists()
        put(root / 'hot-replay-proven', b'yes\n')
    elif mode in ('cold_journal', 'wal_sidecar', 'shm_sidecar'):
        suffix = {'cold_journal': '-journal', 'wal_sidecar': '-wal', 'shm_sidecar': '-shm'}[mode]
        put(Path(str(state) + suffix), b'', 0o600, UID, GID)
    elif mode == 'wal_header':
        raw_state = bytearray(state.read_bytes())
        raw_state[18:20] = b'\x02\x02'
        put(state, bytes(raw_state), 0o600, UID, GID)
    raw = canonical(value)
    if mode == 'old_source_profile': raw = canonical(intake)
    if mode == 'duplicate': raw = b'{"format":' + json.dumps(value['format']).encode() + b',' + raw[1:]
    elif mode == 'noncanonical': raw = json.dumps(value, indent=2).encode()
    put(root / 'host.json', raw)
    put(root / 'expected.json', canonical(dict(head=dict(sequence=0, digest=genesis),
        revision=1 if mode == 'pending_prepared' else 2 if mode == 'pending_dispatched' else 0)))
    put(root / 'approval.txt', (hashlib.sha256(raw).hexdigest() + '\n').encode())
    if mode == 'host_profile_symlink':
        (root / 'host.json').rename(root / 'original-host.json')
        (root / 'host.json').symlink_to(root / 'original-host.json')
    elif mode == 'host_profile_hardlink': os.link(root / 'host.json', root / 'second-host.json')
    elif mode == 'host_profile_userowned': os.chown(root / 'host.json', UID, GID)
    elif mode == 'host_profile_writable': (root / 'host.json').chmod(0o666)
    elif mode in ('host_profile_acl', 'host_profile_parent_acl'):
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *row) for row in (
            (1, 6, 0xffffffff), (2, 4, UID), (4, 4, 0xffffffff), (16, 4, 0xffffffff), (32, 4, 0xffffffff)))
        os.setxattr(root if mode == 'host_profile_parent_acl' else root / 'host.json',
                    'system.posix_acl_default' if mode == 'host_profile_parent_acl' else 'system.posix_acl_access', acl)
    return root


def line(child):
    value = bytearray()
    deadline = time.monotonic() + 25
    while len(value) < 32768:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([child.stdout], [], [], remaining)[0]:
            raise RuntimeError('WORKER_TIMEOUT')
        part = os.read(child.stdout.fileno(), 1)
        if not part: raise RuntimeError('WORKER_EOF')
        if part == b'\n': return bytes(value)
        value.extend(part)
    raise RuntimeError('WORKER_BOUND')


def locked(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    fcntl.flock(fd, fcntl.LOCK_UN)
    return False


def exercise(root, worker, mode):
    state, lock = root / 'state/anchor.sqlite', root / 'state/owner.lock'
    before = state.read_bytes()
    sidecars = {suffix: Path(str(state) + suffix).read_bytes() if Path(str(state) + suffix).exists() else None
                for suffix in ('-journal', '-wal', '-shm')}
    lock_fd = os.open(lock, os.O_RDWR | os.O_CLOEXEC)
    child = pidfd = None
    try:
        if mode == 'missing_lock': lock.rename(root / 'state/missing-owner.lock')
        flags = ['-I', '-S', '-B'] + (['-O'] if mode == 'optimized' else [])
        child = subprocess.Popen(['/usr/bin/python3.12', *flags, str(worker), str(root), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, user=UID, group=GID,
            extra_groups=[], close_fds=True, start_new_session=True, cwd='/',
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, umask=0o077)
        pidfd = os.pidfd_open(child.pid)
        row = line(child)
        constructed = row == b'CONSTRUCTED'
        if constructed:
            assert locked(lock_fd), 'OWNER_NOT_HELD_AT_CONSTRUCTION'
            if mode == 'profile_drift': put(root / 'host.json', b'{}\n')
            elif mode == 'secret_drift': put(root / 'secrets/reader.json', b'{}\n', 0o400, UID, GID)
            elif mode == 'lease_drift':
                lock.rename(root / 'state/old-owner.lock')
                put(lock, b'', 0o600, UID, GID)
            elif mode == 'recovery_drift':
                put(Path(str(state) + '-journal'), b'synthetic-uncertain-journal', 0o600, UID, GID)
                sidecars['-journal'] = b'synthetic-uncertain-journal'
            child.stdin.write(b'CONTINUE\n')
            child.stdin.flush()
            row = line(child)
        metrics = json.loads(row)
        bootstrap = dict(intake=0, cold=0, staged=0) if mode in NO_BOOTSTRAP else (
            dict(intake=1, cold=0, staged=0) if mode in INTAKE_ONLY else (
            dict(intake=1, cold=1, staged=0) if mode in COLD_CAPTURE else dict(intake=1, cold=1, staged=1)))
        assert metrics['bootstrap_exec'] == bootstrap, 'UNEXPECTED_BOOTSTRAP_EXECUTION'
        assert (metrics['payload_exec'] == 0 if mode in NO_BOOTSTRAP | INTAKE_ONLY | COLD_CAPTURE
                else metrics['payload_exec'] > 0), 'UNEXPECTED_PAYLOAD_EXECUTION'
        assert metrics['constructed'] == (mode in CONSTRUCTED), 'CONSTRUCTOR_OUTCOME'
        assert metrics['refused'] == (mode not in POSITIVE), 'REFUSAL_OUTCOME'
        assert metrics['network'] == metrics['listeners'] == 0, 'FORBIDDEN_NETWORK'
        assert metrics['owner_before_sql'] and metrics['owner_before_key'], 'ORDER_VIOLATION'
        assert metrics['retained'] == (mode in RETAINED), 'RETENTION_METRIC'
        assert locked(lock_fd) == (mode in RETAINED), 'REAL_SECOND_PROCESS_LOCK'
        if mode in RETAINED: assert metrics['imports_retained'], 'IMPORT_REGISTRATIONS_NOT_RETAINED'
        if mode in RETAINED: assert metrics['failed_calls_blocked'], 'UNKNOWN_HANDLE_REUSED'
        if mode in RETAINED: assert metrics['failed_handle_rooted'], 'NO_PROCESS_LIFETIME_ROOT'
        if mode == 'reader_constructor': assert metrics['partial_reader_retained'], 'PARTIAL_READER_NOT_RETAINED'
        assert state.read_bytes() == before, 'STATE_CHANGED'
        assert sidecars == {suffix: Path(str(state) + suffix).read_bytes() if Path(str(state) + suffix).exists() else None
                            for suffix in ('-journal', '-wal', '-shm')}, 'RECOVERY_SIDECARS_CHANGED'
        if mode == 'hot_journal': assert (root / 'hot-replay-proven').read_bytes() == b'yes\n'
        if constructed:
            assert metrics['checked'] and metrics['public_methods'] and metrics['false_flags'] and metrics['summary_matches'], 'HANDLE_SURFACE'
            assert metrics['reader_constructor'] == metrics['server_constructor'] == 1 and metrics['key_opens'] > 0 and metrics['key_reads'] > 0 and metrics['sql'] > 0, 'ACTUAL_CONSTRUCTION'
        if mode in {'server_constructor', 'secret_wrong_key', 'secret_extra', 'reader_constructor'}:
            assert metrics['sql'] > 0 and metrics['key_opens'] > 0 and metrics['key_reads'] > 0, 'ACTUAL_SECRET_PHASE'
            assert metrics['reader_constructor'] == int(mode in {'reader_constructor', 'server_constructor'}), 'ACTUAL_READER_PHASE'
            assert metrics['server_constructor'] == int(mode == 'server_constructor'), 'ACTUAL_SERVER_PHASE'
        if mode in POSITIVE:
            assert metrics['closed'] and metrics['modules_clean'], 'CLEAN_CLOSE'
            assert metrics['pending'] == mode.startswith('pending_'), 'PENDING_PRESERVATION'
        if mode == 'repeat': assert metrics['repeat_refused'], 'NO_REPEAT'
        if mode in ('profile_drift', 'secret_drift', 'lease_drift', 'recovery_drift'): assert metrics['drift_refused'], 'DRIFT_BEFORE_SQL'
        if mode in {'wrong_approval', 'old_profile', 'wrong_mode', 'extra_field', 'duplicate', 'noncanonical',
                    'uid_wrong', 'writer_same', 'wrong_bounds', 'state_inode', 'lease_inode', 'missing_lock',
                    'state_mode', 'state_acl', 'intake_tamper', 'stage_tamper', 'wheel_tamper', 'source_extra',
                    'preloaded', 'foreign_path', 'optimized', 'double_state', 'double_secret', 'double_source', 'profile_alias',
                    'old_source_profile', 'host_profile_symlink', 'host_profile_hardlink', 'host_profile_userowned',
                    'host_profile_writable', 'host_profile_acl', 'host_profile_parent_acl', 'source_profile_hash',
                    'hot_journal', 'cold_journal', 'wal_sidecar', 'shm_sidecar', 'wal_header'}:
            assert metrics['sql'] == metrics['key_opens'] == metrics['key_reads'] == 0, 'EARLY_REFUSAL_ACTIVITY'
        if mode in {'bad_schema', 'bad_location'}: assert metrics['key_opens'] == metrics['key_reads'] == 0, 'STATE_BEFORE_SECRET'
        if mode in {'secret_inode', 'secret_owner', 'secret_mode', 'secret_hardlink'}: assert metrics['key_reads'] == 0, 'KEY_DESCRIPTOR_FIRST'
        child.stdin.write(b'EXIT\n')
        child.stdin.flush()
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0 and not out and not err, 'WORKER_EXIT'
        assert not locked(lock_fd), 'OWNER_NOT_RELEASED_ON_EXIT'
        metrics.update(parent_kernel_lock_checked=True, parent_state_bytes_unchanged=True,
                       parent_owner_released_after_exit=True, parent_sidecars_unchanged=True,
                       parent_hot_replay_proven=mode == 'hot_journal')
        return metrics
    finally:
        try:
            if child is not None:
                if child.poll() is None:
                    # Popen owns this unreaped child even when pidfd allocation failed.
                    if pidfd is None: child.kill()
                    else: signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                child.communicate(timeout=10)
        finally:
            try:
                if pidfd is not None: os.close(pidfd)
            finally: os.close(lock_fd)


def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_host_construction_gate_refused"}'); return 2
    if sys.argv[1:] != ['--reviewed-by-main']: return 2
    if os.geteuid() == 0 and os.getpid() != 1:
        os.execv('/usr/bin/unshare', ['unshare', '--pid', '--fork', '--mount-proc', '--net',
            '/usr/bin/python3.12', '-I', '-S', '-B', str(HERE / 'run_gate.py'), *sys.argv[1:]])
    assert sys.platform == 'linux' and sys.version_info[:2] == (3, 12) and os.geteuid() == 0 and os.getpid() == 1
    assert [x.split(':', 1)[0].strip() for x in Path('/proc/self/net/dev').read_text().splitlines()[2:]] == ['lo']
    for lookup, number in ((pwd.getpwuid, UID), (grp.getgrgid, GID)):
        try: lookup(number)
        except KeyError: pass
        else: raise RuntimeError('IDENTITY_CONFLICT')
    repo, wheels = REPO, HERE.parent / 'wheels'
    assert len(CASES) == 60
    assert sha(SOURCE / 'host/staged_host.py') == ENTRY_SHA and sha(HERE / 'worker.py') == WORKER_SHA
    draft = json.loads((SOURCE / 'host/source-profile.json').read_bytes())
    assert draft['status'] == 'DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED'
    assert draft['operator_approval'] is None and draft['admitted_operations'] == []
    assert draft['sources'] == {'staged_host.py': ENTRY_SHA}
    scope_file = HERE.parent / 'controller/scope_control.py'
    assert sha(scope_file) == 'd46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'
    control = load(scope_file, 'host_construction_fixed_scope')
    scope = control.read_scope()
    work = Path(scope['work'])
    base = Path(tempfile.mkdtemp(prefix='h-', dir=work)).resolve()
    assert base.parent == work and base.name.startswith('h-')
    base.chmod(0o755)
    base_id = identity(base)
    started = time.monotonic()
    results = []
    inputs = None
    try:
        original, worker, inputs = template(base, repo, wheels)
        assert len(inputs) == 33
        before = {k: sha(f) for k, f in inputs.items()}
        for mode in CASES:
            root = fixture(base, original, mode)
            try:
                metrics = exercise(root, worker, mode)
                results.append(dict(case=mode, outcome='PASS', metrics=metrics))
            except BaseException as error:
                results.append(dict(case=mode, outcome='FAIL', error_type=type(error).__name__,
                                    diagnostic=str(error) if type(error) is AssertionError else 'execution_error'))
            finally:
                assert root.parent == base and root.name.startswith('case-')
                shutil.rmtree(root)
            print('CASE_FINISHED ' + mode + ' ' + results[-1]['outcome'], flush=True)
        after = {k: sha(f) for k, f in inputs.items()}
        assert not [p for p in Path('/proc').iterdir() if p.name.isdigit() and int(p.name) != 1]
    finally:
        assert base.parent == work and identity(base) == base_id and base.name.startswith('h-')
        shutil.rmtree(base)
    passed = len(results) == len(CASES) and all(r['outcome'] == 'PASS' for r in results) and before == after
    report = dict(format='pult-staged-host-construction-native-proof-v1', outcome='PASS' if passed else 'FAIL',
                  tests=len(results), failures=[r for r in results if r['outcome'] != 'PASS'], skips=0,
                  results=results, source_hashes_before=before, source_hashes_after=after,
                  sources_unchanged=before == after, private_pid_network_namespace=True,
                  native_tree_removed=not base.exists(), elapsed_seconds=round(time.monotonic() - started, 3),
                  new_paid_resources=0, additional_spend_rub=0,
                  scope='LOCAL_CONSTRUCTION_ONLY_NO_OUTER_WRITE_STOP_DEPLOYMENT',
                  remaining_owned_processes=[], deployment_admitted=False, listener_admitted=False,
                  external_io_admitted=False, outer_write_stop_bound=False)
    receipt = HERE / ('RUN_' + str(time.time_ns()) + '.json')
    with receipt.open('x') as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write('\n')
    print(json.dumps({k: report[k] for k in ('outcome', 'tests', 'failures', 'skips', 'sources_unchanged', 'elapsed_seconds')}, sort_keys=True), flush=True)
    print('RECEIPT ' + str(receipt), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
