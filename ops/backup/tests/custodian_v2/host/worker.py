"""Disposable unprivileged constructor proof; fake data, denied external I/O."""
import fcntl
import gc
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import sys
from types import ModuleType
import weakref


def run():
    fixture, mode = Path(sys.argv[1]), sys.argv[2]
    secret, lock = fixture / 'secrets/reader.json', fixture / 'state/owner.lock'
    expected = (fixture / 'state/missing-owner.lock' if mode == 'missing_lock' else lock).stat()
    metrics = dict(constructed=False, checked=False, refused=False, closed=False,
                   sql=0, key_opens=0, key_reads=0, payload_exec=0,
                   bootstrap_exec={'intake': 0, 'cold': 0, 'staged': 0}, lease_acquired=False,
                   owner_before_sql=True, owner_before_key=True, network=0, listeners=0,
                   reader_constructor=0, server_constructor=0, public_methods=False,
                   false_flags=False, pending=False, summary_matches=False,
                   repeat_refused=False, drift_refused=False,
                   modules_clean=False, retained=False, imports_retained=False,
                   partial_reader_retained=False, failed_calls_blocked=False,
                   failed_handle_rooted=False,
                   error_type=None, last_phase='bootstrap')
    native_flock, native_read = fcntl.flock, os.read
    lease_fds = []
    before_meta = tuple(sys.meta_path)
    original_reader_init = original_reader_close = None
    instrumented = False
    ready = None
    reader_refs = []
    registrations = {}
    captured_finder = None

    def held():
        for fd in lease_fds:
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino):
                    return True
            except OSError:
                pass
        return False

    def flock(fd, operation):
        result = native_flock(fd, operation)
        info = os.fstat(fd)
        if operation & fcntl.LOCK_EX and (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino):
            lease_fds.append(fd)
            metrics['lease_acquired'] = True
        return result

    def guard(key):
        if not held():
            metrics[key] = False
            raise RuntimeError('OWNER_REQUIRED')

    def audit(event, args):
        if event == 'exec':
            filename = args[0].co_filename
            if filename.startswith(str(fixture / 'release') + '/'):
                metrics['payload_exec'] += 1
            for key, name in (('intake', 'staged_intake.py'), ('cold', 'cold_host.py'), ('staged', 'staged_ipc.py')):
                if filename == str(fixture / 'entry' / name):
                    metrics['bootstrap_exec'][key] += 1
        elif event == 'sqlite3.connect':
            metrics['sql'] += 1
            guard('owner_before_sql')
        elif event == 'open' and args[0] in (str(secret), os.fsencode(secret)):
            metrics['key_opens'] += 1
            guard('owner_before_key')

    def read(fd, size):
        try:
            protected = os.readlink('/proc/self/fd/' + str(fd)) == str(secret)
        except OSError:
            protected = False
        if protected:
            metrics['key_reads'] += 1
            guard('owner_before_key')
        return native_read(fd, size)

    def deny_network(*args, **kwargs):
        metrics['network'] += 1
        raise RuntimeError('NO_NETWORK')

    def deny_listener(*args, **kwargs):
        metrics['listeners'] += 1
        raise RuntimeError('NO_LISTENER')

    def trace(frame, event, arg):
        nonlocal instrumented, original_reader_init, original_reader_close, captured_finder
        if str(frame.f_code.co_filename).endswith('/entry/staged_host.py') and event == 'call':
            metrics['last_phase'] = frame.f_code.co_name
        if (not instrumented and event == 'return' and frame.f_code.co_name == 'activate'
                and frame.f_code.co_filename == str(fixture / 'entry/staged_intake.py')):
            instrumented = True
            captured_finder = sys.meta_path[0]
            registrations.update({n: m for n, m in sys.modules.items()
                                  if n.split('.', 1)[0] in {'anchor_v2', 'protocol_v2', 'event_codec', 'event_store',
                                      'owned_host', 'host_core', 'ops', 'httpx', 'anyio', 'certifi', 'h11', 'httpcore', 'idna', 'typing_extensions', 'staged_ipc'}})
            store, stage = sys.modules['event_store'], sys.modules['staged_ipc']
            original_reader_init, original_reader_close = store.EventReader.__init__, store.EventReader.close
            original_server = stage.Server.__init__

            def reader_init(self, *args, **kwargs):
                metrics['reader_constructor'] += 1
                result = original_reader_init(self, *args, **kwargs)
                if mode == 'reader_constructor':
                    reader_refs.append(weakref.ref(self))
                    raise RuntimeError('OPAQUE_READER_CONSTRUCTOR')
                return result

            def reader_close(self):
                result = original_reader_close(self)
                if mode == 'reader_close':
                    raise RuntimeError('READER_CLOSE_UNKNOWN')
                return result

            def server_init(self, *args, **kwargs):
                metrics['server_constructor'] += 1
                result = original_server(self, *args, **kwargs)
                if mode == 'server_constructor':
                    raise RuntimeError('SERVER_CONSTRUCTOR_REFUSED')
                return result

            store.EventReader.__init__, store.EventReader.close = reader_init, reader_close
            stage.Server.__init__ = server_init
            if mode in ('capsule_close', 'partial_capsule_close'):
                original_capsule_close = type(frame.f_locals['self']).close
                def bad_close(self):
                    if mode == 'partial_capsule_close':
                        original_capsule_close(self)
                    raise RuntimeError('CAPSULE_CLOSE_UNKNOWN')
                type(frame.f_locals['self']).close = bad_close
            if mode == 'post_capsule_owner_check':
                owner_type = sys.modules['owned_host'].Lease
                original_owner_check = owner_type.check
                def owner_check(self):
                    if captured_finder not in sys.meta_path:
                        raise RuntimeError('POST_CAPSULE_OWNER_UNCERTAIN')
                    return original_owner_check(self)
                owner_type.check = owner_check
        return trace

    fcntl.flock, os.read = flock, read
    socket.socket.connect = socket.socket.connect_ex = deny_network
    socket.create_connection = socket.getaddrinfo = deny_network
    socket.socket.bind = socket.socket.listen = deny_listener
    sys.addaudithook(audit)
    sys.setprofile(trace)
    source = (fixture / 'entry/staged_host.py').read_bytes()
    if hashlib.sha256(source).hexdigest() != (fixture / 'entry.sha256').read_text().strip():
        raise RuntimeError('FIXED_ENTRY_REQUIRED')
    module = ModuleType('_synthetic_staged_host_entry')
    module.__file__ = str(fixture / 'entry/staged_host.py')
    exec(compile(source, module.__file__, 'exec', dont_inherit=True, optimize=0), module.__dict__)
    approved = (fixture / 'approval.txt').read_text().strip()
    if mode == 'wrong_approval':
        approved = ('0' if approved[0] != '0' else '1') + approved[1:]
    if mode == 'preloaded':
        sys.modules['staged_ipc'] = ModuleType('staged_ipc')
    if mode == 'foreign_path':
        sys.path.append(str(fixture))
    try:
        profile_path = str(fixture / 'host.json')
        if mode == 'profile_alias': profile_path = '/' + profile_path
        ready = module.prepare(profile_path, approved)
        metrics['constructed'] = True
        summary = ready.check()
        metrics['checked'] = True
        metrics['public_methods'] = {x for x in dir(ready) if not x.startswith('_')} == {'check', 'close'}
        metrics['false_flags'] = all(summary[x] is False for x in ('write_admitted', 'listener_admitted', 'purge_or_restore_admitted'))
        metrics['pending'] = summary['pending']
        expected_state = json.loads((fixture / 'expected.json').read_bytes())
        metrics['summary_matches'] = summary['head'] == expected_state['head'] and summary['revision'] == expected_state['revision']
        if mode == 'repeat':
            before = metrics['sql'], metrics['key_opens'], metrics['key_reads']
            try:
                module.prepare(str(fixture / 'host.json'), approved)
            except module.Refused:
                metrics['repeat_refused'] = before == (metrics['sql'], metrics['key_opens'], metrics['key_reads'])
        print('CONSTRUCTED', flush=True)
        if sys.stdin.readline().strip() != 'CONTINUE':
            raise RuntimeError('CHECKPOINT_REQUIRED')
        if mode in ('profile_drift', 'secret_drift', 'lease_drift', 'recovery_drift'):
            before = metrics['sql'], metrics['key_reads']
            try:
                ready.check()
            except module.Refused:
                metrics['drift_refused'] = before == (metrics['sql'], metrics['key_reads'])
        ready.close()
        metrics['closed'] = True
    except BaseException as error:
        metrics['refused'] = True
        metrics['error_type'] = type(error).__name__
        if ready is not None:
            try:
                ready.close()
            except BaseException:
                pass
    finally:
        failed = ready if ready is not None else next(iter(module._FAILED), None)
        failed_ref = weakref.ref(failed) if failed is not None and failed._failed else None
        if failed is not None and failed._failed:
            before = metrics['sql'], metrics['key_opens'], metrics['key_reads']
            refused = 0
            for call in (failed.check, failed.close,
                         lambda: module.prepare(str(fixture / 'host.json'), approved)):
                try:
                    call()
                except module.Refused:
                    refused += 1
            metrics['failed_calls_blocked'] = refused == 3 and before == (metrics['sql'], metrics['key_opens'], metrics['key_reads'])
            del call
        # No caller-owned strong handle may make the retention proof pass.
        ready = failed = None
        gc.collect()
        metrics['failed_handle_rooted'] = (failed_ref is not None and failed_ref() is not None
            and any(owned is failed_ref() for owned in module._FAILED))
        metrics['partial_reader_retained'] = bool(reader_refs) and all(ref() is not None for ref in reader_refs)
        metrics['imports_retained'] = bool(registrations) and captured_finder in sys.meta_path and all(
            sys.modules.get(n) is m for n, m in registrations.items())
        metrics['retained'] = held()
        metrics['modules_clean'] = (tuple(sys.meta_path) == before_meta and
            not any(n.split('.', 1)[0] in {'anchor_v2', 'protocol_v2', 'event_codec', 'event_store',
                    'owned_host', 'host_core', 'ops', 'httpx', 'anyio', 'certifi', 'h11', 'httpcore', 'idna', 'typing_extensions', 'staged_ipc'}
                    for n in sys.modules if not (mode == 'preloaded' and n == 'staged_ipc')))
        print(json.dumps(metrics, sort_keys=True), flush=True)
        if sys.stdin.readline().strip() != 'EXIT':
            raise RuntimeError('EXIT_CHECKPOINT_REQUIRED')


if __name__ == '__main__':
    run()
