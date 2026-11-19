"""Disposable source-intake proof worker; trusted root fixture, no host authority."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sqlite3  # Preload stdlib only, before denying every connection attempt.
import stat
import sys
from types import ModuleType


STAGED_SHA = 'f8010c39d6162dcac44b5e20cef1e79323c0e88a2e8a01eac34e8fe9163dbf02'
MODES = {'normal', 'happy', 'capture_only', 'wrong_approval', 'preloaded_app',
         'preloaded_stage', 'foreign_finder', 'foreign_path', 'optimized',
         'profile_checkpoint', 'captured_source_checkpoint',
         'uncaptured_probe_checkpoint', 'loader_drift', 'repeat_capture', 'repeat_activate'}
# Fixture mutations still exercise the ordinary capture path.
MODES |= {'old_profile', 'wrong_mode', 'extra_field', 'duplicate_profile', 'noncanonical',
          'profile_symlink', 'profile_hardlink', 'profile_userowned', 'profile_writable',
          'profile_acl', 'cold_tamper', 'stage_tamper', 'base_tamper', 'wheel_tamper',
          'dependency_tamper', 'release_extra', 'staged_inside_release'}
CHECKPOINTS = {'profile_checkpoint', 'captured_source_checkpoint', 'uncaptured_probe_checkpoint'}


def root_read(file, limit):
    """Capture the trusted fixture bootstrap, never use mutable-path imports."""
    for parent in file.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or (info.st_mode & 0o022 and not sticky)
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(parent, follow_symlinks=False))):
            raise RuntimeError('FIXTURE_PARENT')
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) not in (0o444, 0o644)
                or not 0 < before.st_size <= limit
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(fd))):
            raise RuntimeError('FIXTURE_FILE')
        chunks = bytearray()
        while len(chunks) <= limit:
            part = os.read(fd, min(8192, limit + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        after = os.fstat(fd)
        current = file.lstat()
        if (len(chunks) != before.st_size
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
                or any(getattr(before, key) != getattr(after, key) for key in
                       ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))):
            raise RuntimeError('FIXTURE_CHANGED')
        return bytes(chunks)
    finally:
        os.close(fd)


def run():
    if len(sys.argv) != 3:
        raise RuntimeError('WORKER_ARGUMENTS')
    fixture, mode = Path(sys.argv[1]), sys.argv[2]
    if not fixture.is_absolute() or '..' in fixture.parts or mode not in MODES:
        raise RuntimeError('WORKER_ARGUMENTS')
    release, stage_file = fixture / 'release', fixture / 'entry/staged_ipc.py'
    profile, secret = fixture / 'profile.json', fixture / 'secret'
    metrics = dict(outcome='refused', ok=False, refused=True, interrupted=False,
                   phase='bootstrap', captured=False, activated=False, candidate_pin=False,
                   payload_exec=0, base_exec=0, staged_exec=0, exec_files={},
                   sql=0, credential_opens=0, credential_body_reads=0,
                   network_attempts=0, listener_attempts=0, profile_reads=0,
                   capture_payload_exec=0, capture_clean=False, modules_identity=False,
                   modules_clean=False, finder_clean=False, all_false_flags=False,
                   summary_exact=False, no_module_export=False, no_host_methods=True,
                   captured_source_used=False, marker_absent=False,
                   repeat_capture_refused=False, repeat_activate_refused=False,
                   repeat_no_exec=False, drift_blocked=False, probe_blocked=False,
                   probe_no_exec=False, isolated=bool(sys.flags.isolated),
                   no_site=bool(sys.flags.no_site), no_bytecode=bool(sys.dont_write_bytecode),
                   optimized=bool(sys.flags.optimize))
    capsule, finder, owned = None, None, {}
    original_meta, original_path = tuple(sys.meta_path), tuple(sys.path)
    native_read, native_pread = os.read, os.pread

    def deny(counter):
        metrics[counter] += 1
        raise RuntimeError('FORBIDDEN_ACTIVITY')

    def audit(event, args):
        if event == 'exec':
            name = args[0].co_filename
            base = name.startswith(str(release) + '/')
            staged = name == str(stage_file)
            if base or staged:
                metrics['payload_exec'] += 1
                metrics['base_exec' if base else 'staged_exec'] += 1
                relative = str(Path(name).relative_to(fixture))
                metrics['exec_files'][relative] = metrics['exec_files'].get(relative, 0) + 1
        elif event.startswith('sqlite3.connect'):
            deny('sql')
        elif event == 'open':
            name = args[0]
            if name in (str(profile), os.fsencode(profile)):
                metrics['profile_reads'] += 1
            if name in (str(secret), os.fsencode(secret)):
                deny('credential_opens')
        elif event == 'socket.bind':
            deny('listener_attempts')
        elif event.startswith('socket.'):
            deny('network_attempts')

    def protected(fd):
        try:
            return os.readlink('/proc/self/fd/' + str(fd)) == str(secret)
        except OSError:
            return False

    def read(fd, size):
        if protected(fd):
            deny('credential_body_reads')
        return native_read(fd, size)

    def pread(fd, size, offset):
        if protected(fd):
            deny('credential_body_reads')
        return native_pread(fd, size, offset)

    def no_sql(*args, **kwargs):
        deny('sql')

    def no_network(*args, **kwargs):
        deny('network_attempts')

    def no_listener(*args, **kwargs):
        deny('listener_attempts')

    os.read, os.pread = read, pread
    sqlite3.connect = no_sql
    socket.socket.connect = socket.socket.connect_ex = no_network
    socket.create_connection = socket.getaddrinfo = no_network
    socket.socket.bind = socket.socket.listen = no_listener
    sys.addaudithook(audit)
    try:
        entry_file = fixture / 'entry/staged_intake.py'
        expected = root_read(fixture / 'entry.sha256', 128).decode('ascii').strip()
        source = root_read(entry_file, 100000)
        if len(expected) != 64 or hashlib.sha256(source).hexdigest() != expected:
            raise RuntimeError('ENTRY_PIN')
        metrics['candidate_pin'] = True
        entry = ModuleType('_staged_source_intake_candidate')
        entry.__file__ = str(entry_file)
        exec(compile(source, str(entry_file), 'exec', dont_inherit=True, optimize=0), entry.__dict__)
        approved = root_read(fixture / 'approval.txt', 128).decode('ascii').strip()
        if mode == 'wrong_approval':
            approved = ('0' if approved[:1] != '0' else '1') + approved[1:]
        elif mode in ('preloaded_app', 'preloaded_stage'):
            name = 'anchor_v2' if mode == 'preloaded_app' else 'staged_ipc'
            sys.modules[name] = ModuleType(name)
        elif mode == 'foreign_finder':
            class ForeignFinder:
                def find_spec(self, *args, **kwargs):
                    return None
            sys.meta_path.insert(0, ForeignFinder())
        elif mode == 'foreign_path':
            sys.path.append(str(fixture))
        metrics['phase'] = 'capture'
        capsule = entry.capture(str(profile), approved)
        finder = capsule._finder  # Test-only observation, never returned in JSON.
        metrics['captured'] = True
        metrics['capture_payload_exec'] = metrics['payload_exec']
        metrics['capture_clean'] = not any(metrics[k] for k in
            ('payload_exec', 'sql', 'credential_opens', 'credential_body_reads',
             'network_attempts', 'listener_attempts'))
        metrics['no_host_methods'] = {n for n in dir(capsule) if not n.startswith('_')} == {'activate', 'check', 'close'}
        if mode in CHECKPOINTS:
            metrics['phase'] = 'checkpoint'
            print('CAPTURED', flush=True)
            if sys.stdin.readline().strip() != 'CONTINUE':
                raise RuntimeError('CHECKPOINT_PROTOCOL')
        if mode == 'repeat_capture':
            before = metrics['payload_exec']
            try:
                entry.capture(str(profile), approved)
            except BaseException:
                metrics['repeat_capture_refused'] = True
            metrics['repeat_no_exec'] = metrics['payload_exec'] == before
        if mode != 'capture_only':
            metrics['phase'] = 'activate'
            summary = capsule.activate()
            owned.update(finder.owned)
            metrics['activated'] = True
            expected_summary = dict(status='STAGED_SOURCES_ONLY', staged_sha256=STAGED_SHA,
                                    write_admitted=False, host_capability_created=False,
                                    purge_or_restore_admitted=False)
            metrics['summary_exact'] = type(summary) is dict and summary == expected_summary
            metrics['all_false_flags'] = type(summary) is dict and all(summary.get(k) is False for k in
                ('write_admitted', 'host_capability_created', 'purge_or_restore_admitted'))
            metrics['no_module_export'] = type(summary) is dict and all(type(v) in (str, bool) for v in summary.values())
            stage, anchor, protocol, codec, store, owner = (sys.modules[n] for n in
                ('staged_ipc', 'anchor_v2', 'protocol_v2', 'event_codec', 'event_store', 'owned_host'))
            metrics['modules_identity'] = (
                stage.V2Anchor is anchor.V2Anchor and stage.V2Custodian is protocol.V2Custodian
                and stage.EventReader is store.EventReader and stage.EventWriter is store.EventWriter
                and protocol.V2Anchor is anchor.V2Anchor and store.Binding is codec.Binding
                and anchor.Lease is owner.Lease
                and all(sys.modules.get(n) is m for n, m in owned.items()))
            metrics['captured_source_used'] = (
                hashlib.sha256(finder.loaders['staged_ipc'].body).hexdigest() == STAGED_SHA
                and metrics['staged_exec'] == 1 and stage.__file__ == str(stage_file))
            metrics['phase'] = 'check'
            capsule.check()
            if mode == 'loader_drift':
                stage.__spec__.origin = 'SYNTHETIC_DRIFT'
                before = metrics['payload_exec']
                try:
                    capsule.check()
                except BaseException:
                    metrics['drift_blocked'] = metrics['payload_exec'] == before
            elif mode == 'repeat_activate':
                before = metrics['payload_exec']
                try:
                    capsule.activate()
                except BaseException:
                    metrics['repeat_activate_refused'] = True
                metrics['repeat_no_exec'] = metrics['payload_exec'] == before
            elif mode == 'uncaptured_probe_checkpoint':
                before = metrics['payload_exec']
                try:
                    importlib.import_module('httpx.uncaptured_probe')
                except BaseException:
                    metrics['probe_blocked'] = 'httpx.uncaptured_probe' not in sys.modules
                metrics['probe_no_exec'] = metrics['payload_exec'] == before
            metrics['marker_absent'] = (
                metrics['captured_source_used'] and not (fixture / 'MALICIOUS_EXEC').exists()
                and not any(n in vars(stage) for n in ('MALICIOUS_MARKER', 'UNCAPTURED_MARKER'))
                and (mode != 'uncaptured_probe_checkpoint' or
                     (metrics['probe_blocked'] and metrics['probe_no_exec'])))
        metrics.update(outcome='ok', ok=True, refused=False, phase='complete')
    except BaseException as error:
        metrics['interrupted'] = isinstance(error, (KeyboardInterrupt, SystemExit))
    finally:
        if finder is not None:
            owned.update(finder.owned)
        try:
            if capsule is not None:
                capsule.close()
        except BaseException:
            metrics.update(outcome='refused', ok=False, refused=True, phase='close')
        metrics['modules_clean'] = all(sys.modules.get(n) is not m for n, m in owned.items())
        metrics['finder_clean'] = (finder is None or finder not in sys.meta_path)
        if mode not in ('foreign_finder', 'foreign_path'):
            metrics['finder_clean'] &= tuple(sys.meta_path) == original_meta and tuple(sys.path) == original_path
    # Names consumed by the root gate; detailed counters above remain available.
    metrics.update(secret_opens=metrics['credential_opens'], network=metrics['network_attempts'],
                   listeners=metrics['listener_attempts'],
                   no_host_api=metrics['no_host_methods'] and
                       (not metrics['activated'] or metrics['no_module_export']),
                   false_flags=metrics['summary_exact'] and metrics['all_false_flags'],
                   drift_refused=metrics['drift_blocked'],
                   probe_refused=metrics['probe_blocked'] and metrics['probe_no_exec'])
    return metrics


if __name__ == '__main__':
    try:
        result = run()
    except BaseException:
        result = dict(outcome='worker_error', ok=False, refused=True)
    # Checkpoints alone add CAPTURED; all outcomes are one redacted JSON object.
    print(json.dumps(result, sort_keys=True, separators=(',', ':')), flush=True)
