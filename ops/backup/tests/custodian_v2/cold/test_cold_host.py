"""Root-only synthetic suite; run_gate must provide private PID/network namespaces.

No payload is imported in this parent. configure() is the sole gate interface.
This file deliberately does not execute tests when imported or run directly.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import time
import unittest
from uuid import uuid4

NATIVE_ROOT = TEMPLATE_ROOT = WORKER_PATH = None
DIAGNOSTIC_FAILURES=[]
UID, GID, TIMEOUT = 47003, 47004, 25


def configure(native_root, template_root, worker_path):
    global NATIVE_ROOT, TEMPLATE_ROOT, WORKER_PATH
    NATIVE_ROOT, TEMPLATE_ROOT, WORKER_PATH = map(Path, (native_root, template_root, worker_path))
    if not all(path.is_absolute() for path in (NATIVE_ROOT, TEMPLATE_ROOT, WORKER_PATH)):
        raise ValueError('ABSOLUTE_GATE_PATHS_REQUIRED')


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode('ascii')


def identity(path):
    info = path.stat()
    return {'device': info.st_dev, 'inode': info.st_ino}


def put(path, body, mode=0o644, uid=0, gid=0):
    path.write_bytes(body)
    os.chown(path, uid, gid)
    path.chmod(mode)


def approve(fixture, profile=None, raw=None):
    if raw is None:
        raw = canonical(profile)
    put(fixture / 'profile.json', raw)
    put(fixture / 'approval.txt', (hashlib.sha256(raw).hexdigest() + '\n').encode('ascii'))


def network_state():
    return {name: (Path('/proc/net') / name).read_bytes()
            for name in ('tcp', 'tcp6', 'udp', 'udp6', 'unix')}


def unchanged_bytes(fixture):
    files = [fixture / 'private-state/anchor.sqlite', fixture / 'private-state/owner.lock']
    files += sorted((fixture / 'objects').iterdir())
    return {str(path.relative_to(fixture)): path.read_bytes() for path in files}


def stop(process):
    try:
        if process.poll() is None:
            signal.pidfd_send_signal(process.cold_pidfd, signal.SIGKILL)
        process.communicate(timeout=TIMEOUT)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=TIMEOUT)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        if process.cold_pidfd is not None:
            os.close(process.cold_pidfd)
            process.cold_pidfd = None


class ColdHostTests(unittest.TestCase):
    def case(self, phase=None):
        self.assertIsNotNone(NATIVE_ROOT, 'GATE_NOT_CONFIGURED')
        self.assertEqual(os.getuid(), 0, 'ROOT_PARENT_REQUIRED')
        self.assertEqual(sys.platform, 'linux', 'NATIVE_LINUX_REQUIRED')
        fixture = NATIVE_ROOT / ('case-' + uuid4().hex)
        shutil.copytree(TEMPLATE_ROOT, fixture)
        self.addCleanup(shutil.rmtree, fixture)
        for path in (fixture, *fixture.rglob('*')):
            self.assertFalse(path.is_symlink(), 'TEMPLATE_LINK_FORBIDDEN')
            os.chown(path, 0, 0)
            path.chmod(0o755 if path.is_dir() else 0o644)
        state, secrets = fixture / 'private-state', fixture / 'secrets'
        for directory in (state, secrets):
            directory.mkdir()
            os.chown(directory, UID, GID)
            directory.chmod(0o700)
        database, lock, credential = state / 'anchor.sqlite', state / 'owner.lock', secrets / 'reader.json'
        scope, generation = 'synthetic-cold', str(uuid4())
        head = {'sequence': 0, 'digest': hashlib.sha256(canonical({
            'format': 'pult-erasure-event-v2', 'kind': 'genesis', 'scope': scope,
            'generation': generation})).hexdigest()}
        # Parse literals, never execute root-owned candidate code.
        tree = ast.parse((fixture / 'release/candidate/anchor_v2.py').read_bytes())
        names = {'META_SQL', 'RECEIPTS_SQL', 'APP_ID', 'SCHEMA'}
        literals = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names}
        self.assertEqual(set(literals), names, 'ANCHOR_LITERALS_REQUIRED')
        location = 'https://s3.ru-3.storage.selcloud.ru/fake-cold-bucket/pult/erasure-v2/' + scope + '/' + generation + '/events/'
        connection = sqlite3.connect(database)
        try:
            connection.execute(literals['META_SQL'])
            connection.execute(literals['RECEIPTS_SQL'])
            connection.execute('PRAGMA application_id=' + str(literals['APP_ID']))
            connection.execute('PRAGMA user_version=' + str(literals['SCHEMA']))
            connection.execute('INSERT INTO anchor_v2 VALUES(1,?,?,?,0,?,0,NULL)',
                               (scope, generation, location, head['digest']))
            objects = fixture / 'objects'
            objects.mkdir(mode=0o755)
            put(objects / 'sentinel', b'SYNTHETIC_OBJECT_UNCHANGED\n')
            if phase:
                event = {'format': 'pult-erasure-event-v2', 'scope': scope, 'generation': generation,
                         'sequence': 1, 'previous': head['digest'], 'event': {
                             'purge_id': str(uuid4()), 'subject_id': str(uuid4()), 'phase': 'intent'}}
                target = hashlib.sha256(canonical(event)).hexdigest()
                event['digest'] = target
                body, attempt = canonical(event), str(uuid4())
                connection.execute('INSERT INTO attempts_v2 VALUES(?,?,?,?,?,?,?)',
                                   (attempt, 0, head['digest'], 1, target, body, phase))
                connection.execute('UPDATE anchor_v2 SET revision=?,pending=?',
                                   (1 if phase == 'PREPARED' else 2, attempt))
                put(objects / '00000000000000000001.json', body)
            connection.commit()
        finally:
            connection.close()
        os.chown(database, UID, GID)
        database.chmod(0o600)
        put(lock, b'', 0o600, UID, GID)
        put(credential, canonical({'access_key': 'FAKE_READER_COLD', 'secret_key': 'invented-not-real'}), 0o400, UID, GID)
        profile = dict(format='pult-schema2-cold-host-profile-v1', mode='OFFLINE_CURRENT_ONLY',
                       release_root=str(fixture / 'release'), scope=scope, generation=generation,
                       custodian_uid=UID, socket_gid=GID, state_file=str(database), state_identity=identity(database),
                       lease_file=str(lock), lease_identity=identity(lock), reader_credential_file=str(credential),
                       reader_credential_identity=identity(credential), reader_access_key='FAKE_READER_COLD',
                       s3=dict(endpoint='https://s3.ru-3.storage.selcloud.ru', region='ru-3', bucket='fake-cold-bucket'),
                       bounds=dict(max_receipts=1024, max_state_bytes=2097152, record_bytes=2048))
        approve(fixture, profile)
        put(fixture / 'expected.json', canonical({'head': head,
            'revision': (1 if phase == 'PREPARED' else 2) if phase else 0,
            'status': 'PENDING_REQUIRES_REVIEW' if phase else 'OBSERVED_LOCAL_ONLY'}))
        return fixture, profile

    def spawn(self, fixture, mode='normal'):
        before, net = unchanged_bytes(fixture), network_state()
        flags = ['-S', '-B'] if mode == 'no_isolation' else ['-I', '-S', '-B']
        if mode=='optimized': flags.append('-O')
        process = subprocess.Popen(['/usr/bin/python3.12', *flags, str(WORKER_PATH), str(fixture), mode],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   user=UID, group=GID, extra_groups=[], cwd='/',
                                   env={'LANG': 'C.UTF-8', 'PATH': '/usr/bin:/bin'}, close_fds=True, umask=0o077)
        process.cold_pidfd = None
        process.cold_before, process.cold_network, process.cold_mode = before, net, mode
        try:
            process.cold_pidfd = os.pidfd_open(process.pid)
        except BaseException:
            process.kill()
            process.communicate(timeout=TIMEOUT)
            raise AssertionError('PIDFD_REQUIRED') from None
        self.addCleanup(stop, process)
        return process

    def line(self, process):
        # Unbuffered, bounded protocol read; never expose raw child output in assertions.
        body, deadline = bytearray(), time.monotonic() + TIMEOUT
        while time.monotonic() < deadline and len(body) < 16384:
            readable, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
            if not readable:
                break
            value = os.read(process.stdout.fileno(), 1)
            if value == b'\n':
                return bytes(body)
            if not value:
                break
            body.extend(value)
        self.fail('WORKER_PROTOCOL_TIMEOUT_OR_BOUND')

    def result(self, fixture, mode='normal', process=None, expected_ok=True, zero_exec=False, zero_sql=False, zero_key=False):
        process = process or self.spawn(fixture, mode)
        before, net, mode = process.cold_before, process.cold_network, process.cold_mode
        try:
            stdout, stderr = process.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            self.fail('WORKER_TIMEOUT')
        self.assertTrue(process.returncode == 0 and not stderr, 'WORKER_EXIT_OR_STDERR')
        self.assertTrue(len(stdout) <= 16384 and stdout.count(b'\n') == 1, 'ONE_REDACTED_RESULT_REQUIRED')
        try:
            result = json.loads(stdout)
        except (ValueError, UnicodeError):
            self.fail('WORKER_INVALID_JSON')
        self.assertNotIn('harness_error', result, 'WORKER_HARNESS_FAILURE')
        self.assertTrue(all(type(value) in (int, bool) for value in result.values()), 'REDACTED_SCALARS_REQUIRED')
        if result['ok']!=expected_ok:
            DIAGNOSTIC_FAILURES.append({'test':self._testMethodName,'metrics':result})
        self.assertEqual(result['ok'], expected_ok, 'UNEXPECTED_PREPARATION_OUTCOME')
        self.assertEqual(result['refused'], not expected_ok, 'REFUSAL_FLAG')
        for name in ('network_attempts', 'listener_attempts', 'guard_violation'):
            self.assertFalse(result[name], 'UNSAFE_WORKER_EFFECT')
        for name in ('principal', 'owner_before_sql', 'owner_before_credential', 'owner_before_credential_open',
                     'owner_before_credential_body', 'no_site', 'no_bytecode'):
            self.assertTrue(result[name], 'WORKER_PRINCIPAL_OR_ORDER')
        self.assertTrue(unchanged_bytes(fixture) == before, 'RAW_DB_OR_OBJECT_CHANGED')
        self.assertTrue(network_state() == net, 'NETWORK_OR_LISTENER_CHANGED')
        self.assertEqual({path.name for path in (fixture / 'private-state').iterdir()},
                         {'anchor.sqlite', 'owner.lock'}, 'STATE_ARTIFACT_CREATED')
        if zero_exec:
            self.assertEqual(result['payload_exec'], 0, 'PAYLOAD_EXECUTED_BEFORE_REJECTION')
        if zero_sql:
            self.assertEqual(result['sql'], 0, 'SQL_BEFORE_REJECTION')
        if zero_key:
            self.assertEqual(result['credential_opens'], 0, 'CREDENTIAL_OPEN_BEFORE_REJECTION')
            self.assertEqual(result['credential_body_reads'], 0, 'CREDENTIAL_BODY_BEFORE_REJECTION')
        if mode != 'handler':
            self.assertEqual(result['transport_requests'], 0, 'UNREQUESTED_GET')
        if expected_ok:
            for name in ('isolated', 'modules_identity', 'modules_clean', 'finder_clean', 'lease_acquired',
                         'lease_held', 'lease_released', 'genesis', 'revision_matches', 'local_only',
                         'no_append_flags', 'no_freshness_claim'):
                self.assertTrue(result[name], 'POSITIVE_COLD_CONTRACT')
            self.assertGreater(result['payload_exec'], 0, 'NO_PAYLOAD_PROOF')
            self.assertGreater(result['sql'], 0, 'NO_SQL_PROOF')
            self.assertEqual(result['credential_opens'], 1, 'ONE_CREDENTIAL_OPEN')
            self.assertGreater(result['credential_body_reads'], 0, 'CREDENTIAL_BODY_READ_PROOF_REQUIRED')
        else:
            self.assertTrue(result['lease_released'] and result['modules_clean'] and result['finder_clean'], 'REFUSAL_CLEANUP')
        return result

    def test_fresh_process_genesis_and_cleanup(self):
        fixture, _ = self.case()
        self.assertGreater(self.result(fixture)['failed_import_execs'],0,'OPTIONAL_IMPORT_FAILURE_PATH_NOT_EXERCISED')
        self.result(fixture)  # a fresh process, same existing bytes and inodes

    def test_pending_is_diagnostic_not_reset_or_dispatch(self):
        for phase in ('PREPARED', 'DISPATCHED'):
            with self.subTest(phase=phase):
                fixture, _ = self.case(phase)
                self.assertTrue(self.result(fixture)['pending'], 'PENDING_DIAGNOSTIC_REQUIRED')

    def test_one_attempt_and_socket_free_handler(self):
        for mode, flag in (('one_attempt', 'one_attempt'), ('handler', 'handler_blocked')):
            fixture, _ = self.case()
            result = self.result(fixture, mode)
            self.assertTrue(result[flag], 'REQUIRED_DENIAL_MISSING')
            if mode == 'handler':
                self.assertEqual(result['transport_requests'], 1, 'MOCK_HANDLER_MUST_BE_REACHED')

    def test_cleanup_preserves_foreign_module(self):
        fixture, _ = self.case()
        result=self.result(fixture,'foreign_cleanup')
        self.assertTrue(result['foreign_preserved'] and result['observe_blocked'],'FOREIGN_IDENTITY_NOT_PRESERVED_OR_REFUSED')

    def test_wrong_approval_and_noncanonical_json(self):
        fixture, _ = self.case()
        self.result(fixture, 'wrong_approval', expected_ok=False, zero_exec=True, zero_sql=True, zero_key=True)
        for variant in ('whitespace', 'duplicate'):
            fixture, profile = self.case()
            raw = canonical(profile)
            raw = b' ' + raw if variant == 'whitespace' else b'{"mode":"OFFLINE_CURRENT_ONLY",' + raw[1:]
            approve(fixture, raw=raw)
            self.result(fixture, expected_ok=False, zero_exec=True, zero_sql=True, zero_key=True)

    def test_profile_shape_mode_principals_bounds_and_location(self):
        mutations = [dict(extra=False), dict(mode='APPEND'), dict(custodian_uid=UID + 1), dict(socket_gid=GID + 1),
                     dict(state_identity={'device': 0, 'inode': 0}), dict(reader_credential_identity={'inode': 1}),
                     dict(reader_access_key='BAD KEY'), dict(bounds={'max_receipts': 1025}),
                     dict(s3={'endpoint': 'https://invalid.example', 'region': 'ru-3', 'bucket': 'fake-cold-bucket'}),
                     dict(generation='00000000-0000-0000-0000-000000000000')]
        for index, mutation in enumerate(mutations):
            with self.subTest(case=index):
                fixture, profile = self.case()
                profile.update(mutation)
                approve(fixture, profile)
                self.result(fixture, expected_ok=False, zero_exec=True, zero_sql=True, zero_key=True)

    def test_existing_state_and_lock_identity_mismatch_before_sql(self):
        for field in ('state_identity', 'lease_identity'):
            fixture, profile = self.case()
            profile[field]['inode'] += 1
            approve(fixture, profile)
            self.result(fixture, expected_ok=False, zero_sql=True, zero_key=True)

    def test_every_catalogued_source_is_checked_even_unused_thirteenth(self):
        sample, _ = self.case()
        names = list(json.loads((sample / 'release/source-profile.json').read_bytes())['sources'])
        self.assertEqual(len(names), 13, 'EXACT_SOURCE_CATALOGUE_REQUIRED')
        for index, name in enumerate(names):
            with self.subTest(source_index=index):
                fixture, _ = self.case()
                path = fixture / 'release' / name
                put(path, path.read_bytes() + b'\n# SYNTHETIC_INVALID\n')
                self.result(fixture, expected_ok=False, zero_exec=True, zero_sql=True, zero_key=True)

    def test_catalogue_wheel_and_extracted_dependency_bytes(self):
        for kind in ('catalogue', 'wheel', 'dependency'):
            fixture, _ = self.case()
            path = (fixture / 'release/source-profile.json' if kind == 'catalogue' else
                    next((fixture / 'release/wheels').iterdir()) if kind == 'wheel' else
                    fixture / 'release/deps/httpx/__init__.py')
            put(path, path.read_bytes() + b'\nSYNTHETIC_INVALID\n')
            self.result(fixture, expected_ok=False, zero_exec=True, zero_sql=True, zero_key=True)

    def test_release_metadata_and_closed_tree(self):
        for variant in ('nonroot', 'writeable', 'symlink', 'hardlink', 'directory', 'extra_py', 'shadow', 'pyc', 'pth', 'native'):
            with self.subTest(case=variant):
                fixture, _ = self.case()
                path = fixture / 'release/candidate/anchor_v2.py'
                if variant == 'nonroot':
                    os.chown(path, UID, GID)
                elif variant == 'writeable':
                    path.chmod(0o666)
                elif variant == 'symlink':
                    saved = fixture / 'saved-source'
                    path.rename(saved)
                    path.symlink_to(saved)
                elif variant == 'hardlink':
                    os.link(path, fixture / 'second-link')
                elif variant == 'directory':
                    path.parent.chmod(0o777)
                elif variant == 'shadow':
                    shadow = fixture / 'release/candidate/anchor_v2'
                    shadow.mkdir()
                    put(shadow / '__init__.py', b'raise RuntimeError("SYNTHETIC_INVALID")\n')
                else:
                    suffix = {'extra_py': '.py', 'pyc': '.pyc', 'pth': '.pth', 'native': '.so'}[variant]
                    put(fixture / ('release/deps/unapproved' + suffix), b'SYNTHETIC_INVALID\n')
                self.result(fixture, expected_ok=False, zero_exec=True, zero_sql=True, zero_key=True)

    def test_cold_runtime_rejects_preload_finder_path_and_no_isolation(self):
        for mode in ('preloaded_app', 'preloaded_dep', 'foreign_finder', 'foreign_path', 'no_isolation','optimized'):
            with self.subTest(mode=mode):
                fixture, _ = self.case()
                self.result(fixture, mode, expected_ok=False, zero_exec=True, zero_sql=True, zero_key=True)

    def test_private_credential_modes_and_inode_before_content(self):
        for mode in (0o400, 0o600):
            fixture, _ = self.case()
            (fixture / 'secrets/reader.json').chmod(mode)
            self.result(fixture)
        for variant in ('mode', 'inode', 'key'):
            fixture, profile = self.case()
            if variant == 'mode':
                (fixture / 'secrets/reader.json').chmod(0o644)
            elif variant == 'inode':
                profile['reader_credential_identity']['inode'] += 1
                approve(fixture, profile)
            else:
                profile['reader_access_key'] = 'FAKE_WRONG_IDENTITY'
                approve(fixture, profile)
            result = self.result(fixture, expected_ok=False)
            self.assertGreater(result['sql'], 0, 'LOCAL_SCHEMA_VALIDATION_REQUIRED')
            self.assertEqual(result['credential_opens'], 1, 'DESCRIPTOR_VALIDATION_OPEN_REQUIRED')
            self.assertTrue(result['lease_released'] and result['finder_clean'], 'REFUSAL_CLEANUP')
            if variant != 'key':
                self.assertEqual(result['credential_body_reads'], 0, 'UNVALIDATED_SECRET_CONTENT_READ')
            else:
                self.assertGreater(result['credential_body_reads'], 0, 'KEY_IDENTITY_CHECK_REQUIRES_BODY')

    def test_root_projected_acl_credential_is_not_admitted(self):
        fixture, _ = self.case()
        secret = fixture / 'secrets/reader.json'
        os.chown(secret, 0, 0)
        secret.chmod(0o440)
        undefined = 0xffffffff
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *row) for row in (
            (1, 4, undefined), (2, 4, UID), (4, 0, undefined), (16, 4, undefined), (32, 0, undefined)))
        os.setxattr(secret, 'system.posix_acl_access', acl)
        result = self.result(fixture, expected_ok=False)
        self.assertGreater(result['sql'], 0, 'LOCAL_SCHEMA_VALIDATION_REQUIRED')
        self.assertEqual(result['credential_body_reads'], 0, 'PROJECTED_SECRET_READ')

    def test_post_capture_source_and_dependency_replacement_uses_snapshot(self):
        for relative in ('candidate/anchor_v2.py', 'deps/httpx/__init__.py'):
            fixture, _ = self.case()
            process = self.spawn(fixture, 'checkpoint')
            self.assertTrue(self.line(process) == b'SNAPSHOT_READY', 'INTAKE_CHECKPOINT_REQUIRED')
            target = fixture / 'release' / relative
            replacement = fixture / 'replacement.py'
            put(replacement, b'raise RuntimeError("SYNTHETIC_INVALID_REPLACEMENT")\n')
            os.replace(replacement, target)
            process.stdin.write(b'CONTINUE\n')
            process.stdin.flush()
            self.result(fixture, process=process)

    def test_post_capture_profile_change_refuses_before_sql_and_key(self):
        fixture, profile = self.case()
        process = self.spawn(fixture, 'checkpoint')
        self.assertTrue(self.line(process) == b'SNAPSHOT_READY', 'INTAKE_CHECKPOINT_REQUIRED')
        profile['scope'] = 'changed-after-intake'
        approve(fixture, profile)  # updating approval must not replace the worker's captured digest
        process.stdin.write(b'CONTINUE\n')
        process.stdin.flush()
        self.result(fixture, process=process, expected_ok=False, zero_sql=True, zero_key=True)

    def test_post_capture_unknown_dependency_module_cannot_fall_through(self):
        fixture, _ = self.case()
        process = self.spawn(fixture, 'checkpoint_probe')
        self.assertTrue(self.line(process) == b'SNAPSHOT_READY', 'INTAKE_CHECKPOINT_REQUIRED')
        put(fixture / 'release/deps/httpx/uncaptured_probe.py',
            b'raise RuntimeError("SYNTHETIC_UNCAPTURED_EXECUTED")\n')
        process.stdin.write(b'CONTINUE\n')
        process.stdin.flush()
        result = self.result(fixture, mode='checkpoint_probe', process=process)
        self.assertTrue(result['probe_blocked'] and result['probe_no_exec'], 'UNCAPTURED_IMPORT_NOT_BLOCKED')

    def test_module_spec_origin_drift_blocks_observe_before_more_sql(self):
        fixture, _ = self.case()
        result = self.result(fixture, 'loader_drift')
        self.assertTrue(result['drift_blocked_before_sql'], 'IDENTITY_DRIFT_ALLOWED_SQL')

    def test_opaque_failures_retain_owner_until_pidfd_kill(self):
        for mode in ('constructor_hold', 'close_hold'):
            with self.subTest(mode=mode):
                fixture, _ = self.case()
                before, net = unchanged_bytes(fixture), network_state()
                process = self.spawn(fixture, mode)
                try:
                    holding = json.loads(self.line(process))
                except (ValueError, UnicodeError):
                    self.fail('HOLDING_RESULT_REQUIRED')
                self.assertTrue(holding.get('event') == 'HOLDING' and holding.get('retained')
                                and holding.get('lease_held') and not holding.get('finder_clean'), 'UNCERTAIN_OWNER_RELEASED')
                self.assertTrue(holding.get('owner_before_sql') and holding.get('owner_before_credential'), 'HOLD_OWNER_ORDER')
                self.assertFalse(holding.get('network_attempts') or holding.get('listener_attempts') or holding.get('guard_violation'),
                                 'HOLD_UNSAFE_EFFECT')
                if mode == 'close_hold':
                    self.assertTrue(holding.get('observe_blocked'), 'OBSERVATION_AFTER_CLOSE_FAULT')
                self.result(fixture, expected_ok=False, zero_sql=True, zero_key=True)
                signal.pidfd_send_signal(process.cold_pidfd, signal.SIGKILL)
                stdout, stderr = process.communicate(timeout=TIMEOUT)
                self.assertTrue(process.returncode == -signal.SIGKILL and not stdout and not stderr, 'OWNED_KILL_OR_OUTPUT')
                self.assertTrue(unchanged_bytes(fixture) == before, 'HOLD_CHANGED_STATE')
                self.assertTrue(network_state() == net, 'HOLD_CHANGED_NETWORK')
                self.result(fixture)

    def test_legacy_profile_input_and_old_anchor_pin_are_incompatible(self):
        fixture, _ = self.case()
        old_tree = ast.parse((fixture / 'release/ownership/owned_host.py').read_bytes())
        assignment = next(node for node in old_tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'CORE_PINS' for target in node.targets))
        guard = next(ast.literal_eval(node.value) for node in old_tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'GUARD_SHA' for target in node.targets))
        old_pins = {ast.literal_eval(key): guard if isinstance(value, ast.Name) and value.id == 'GUARD_SHA'
                    else ast.literal_eval(value) for key, value in zip(assignment.value.keys, assignment.value.values)}
        self.assertEqual(len(old_pins), 7, 'EXACT_LEGACY_SOURCE_PROFILE')
        approve(fixture, old_pins)
        self.result(fixture, expected_ok=False, zero_exec=True, zero_sql=True, zero_key=True)
        old_anchor_pin = old_pins['ops/backup/erasure_anchor.py']
        new_anchor_hash = hashlib.sha256((fixture / 'release/candidate/anchor_v2.py').read_bytes()).hexdigest()
        self.assertNotEqual(old_anchor_pin, new_anchor_hash, 'LEGACY_PIN_MUST_NOT_ADMIT_V2')
        # This is new-entry rejection + static old-pin incompatibility, NOT an
        # executed old prepare_host proof (its separate legacy venv is not built).

    def test_actual_credential_replacement_rejected_before_body_read(self):
        fixture,_=self.case()
        process=self.spawn(fixture,'credential_checkpoint')
        self.assertTrue(self.line(process)==b'CREDENTIAL_READY','CREDENTIAL_CHECKPOINT_REQUIRED')
        replacement=fixture/'replacement-credential'
        put(replacement,canonical({'access_key':'FAKE_READER_COLD','secret_key':'invented-replacement'}),0o400,UID,GID)
        os.replace(replacement,fixture/'secrets/reader.json')
        process.stdin.write(b'CONTINUE\n');process.stdin.flush()
        result=self.result(fixture,process=process,expected_ok=False)
        self.assertGreater(result['sql'],0,'SCHEMA_CHECK_MUST_PRECEDE_KEY')
        self.assertEqual(result['credential_opens'],1,'PINNED_DESCRIPTOR_OPEN_REQUIRED')
        self.assertEqual(result['credential_body_reads'],0,'REPLACED_CREDENTIAL_BODY_WAS_READ')

    def test_actual_old_loader_rejects_only_changed_anchor_before_sql_or_key(self):
        fixture,profile=self.case();legacy=fixture/'legacy';legacy.mkdir()
        tree=ast.parse((fixture/'release/ownership/owned_host.py').read_bytes())
        guard=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id=='GUARD_SHA' for target in node.targets))
        assignment=next(node for node in tree.body if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id=='CORE_PINS' for target in node.targets))
        pins={ast.literal_eval(key):guard if isinstance(value,ast.Name) and value.id=='GUARD_SHA' else ast.literal_eval(value)
            for key,value in zip(assignment.value.keys,assignment.value.values)}
        for name,digest in pins.items():
            source=(fixture/'release/ownership/host_core.py' if name=='ops/backup/erasure_custodian_host.py' else
                fixture/'release/candidate/anchor_v2.py' if name=='ops/backup/erasure_anchor.py' else fixture/'release/frozen'/name)
            target=legacy/'core'/name;target.parent.mkdir(parents=True,exist_ok=True);put(target,source.read_bytes())
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest()!=digest,name=='ops/backup/erasure_anchor.py')
        config={key:profile[key] for key in ('scope','generation','custodian_uid','socket_gid','state_file')}
        config.update(format='pult-erasure-custodian-host-v1',writer_uid=47005,
            socket_file=str(fixture/'legacy-runtime/c.sock'),s3=dict(profile['s3'],key='pult/erasure-test/'+profile['scope']+'/journal.json'))
        raw=canonical(config);put(legacy/'profile.json',raw)
        ownership={key:profile[key] for key in ('state_file','state_identity','lease_file','lease_identity','custodian_uid','socket_gid')}
        ownership.update(format='pult-custodian-ownership-v1',host_config_sha256=hashlib.sha256(raw).hexdigest())
        raw=canonical(ownership);put(legacy/'ownership.json',raw)
        put(legacy/'approval.txt',(hashlib.sha256(raw).hexdigest()+'\n').encode('ascii'))
        result=self.result(fixture,'legacy_pin')
        self.assertTrue(result['legacy_pin_refused'] and result['legacy_bad_anchor_checked'],'LEGACY_LOADER_DID_NOT_REJECT_EXACT_PIN')
