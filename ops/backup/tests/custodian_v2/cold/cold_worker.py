"""Disposable, redacted Linux worker. Invoke only through the reviewed root gate."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3  # stdlib only; install the audit hook before any connection
import stat
import sys
from types import ModuleType


def run():
    fixture, mode = Path(sys.argv[1]), sys.argv[2]
    release = fixture / 'release'
    secret = fixture / 'secrets/reader.json'
    lock = fixture / 'private-state/owner.lock'
    expected = lock.stat()
    metrics = dict(payload_exec=0, sql=0, credential_opens=0, credential_body_reads=0, failure_stage=0,
                   activation_kind=0, activation_line=0, activation_guard=0,
                   activation_path_changed=False, activation_finders_changed=False,
                   activation_missing_modules=0, activation_missing_trio=False, failed_import_execs=0,
                   legacy_pin_refused=False, legacy_bad_anchor_checked=False,
                   network_attempts=0, listener_attempts=0, owner_before_sql=True,
                   transport_requests=0, owner_before_credential=True,
                   owner_before_credential_open=True, owner_before_credential_body=True,
                   guard_violation=False, lease_acquired=False,
                   lease_held=False, lease_released=False, modules_identity=False,
                   modules_clean=False, finder_clean=False, foreign_preserved=False,
                   no_append_flags=False, local_only=False, genesis=False, pending=False,
                   observe_blocked=False, one_attempt=False, handler_blocked=False,
                   probe_blocked=False, probe_no_exec=False, drift_blocked_before_sql=False,
                   no_freshness_claim=False, revision_matches=False, retained=False, isolated=bool(sys.flags.isolated),
                   no_site=bool(sys.flags.no_site), no_bytecode=bool(sys.dont_write_bytecode),
                   principal=os.getuid() == 47003 and os.getgid() == 47004 and os.getgroups() == [])
    lease_fds = []
    owned = {}
    finder = None
    ready = None
    native_flock, native_read = fcntl.flock, os.read
    original_meta = tuple(sys.meta_path)

    def live_owner():
        for fd in lease_fds:
            try:
                info = os.fstat(fd)
                if ((info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino)
                        and stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == 0
                        and info.st_uid == 47003 and info.st_gid == 47004
                        and stat.S_IMODE(info.st_mode) == 0o600
                        and os.readlink('/proc/self/fd/' + str(fd)) == str(lock)):
                    return True
            except OSError:
                pass
        return False

    def flock(fd, operation):
        result = native_flock(fd, operation)
        if operation & fcntl.LOCK_EX and operation & fcntl.LOCK_NB:
            info = os.fstat(fd)
            if ((info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino)
                    and os.readlink('/proc/self/fd/' + str(fd)) == str(lock)):
                lease_fds.append(fd)
                metrics['lease_acquired'] = True
        return result

    def guard(kind):
        valid = live_owner()
        metrics['owner_before_' + kind] &= valid
        if kind.startswith('credential_'):
            metrics['owner_before_credential'] &= valid
        if not valid:
            metrics['guard_violation'] = True
            raise RuntimeError('OWNER_GUARD')

    def audit(event, args):
        if event == 'exec' and args[0].co_filename.startswith(str(release) + '/'):
            metrics['payload_exec'] += 1
        elif event == 'sqlite3.connect':
            metrics['sql'] += 1
            guard('sql')
        elif event == 'open' and args[0] in (str(secret), os.fsencode(secret)):
            metrics['credential_opens'] += 1
            guard('credential_open')

    def read(fd, size):
        try:
            is_secret = os.readlink('/proc/self/fd/' + str(fd)) == str(secret)
        except OSError:
            is_secret = False
        if is_secret:
            guard('credential_body')
            metrics['credential_body_reads'] += 1
        return native_read(fd, size)

    def no_network(*args, **kwargs):
        metrics['network_attempts'] += 1
        raise RuntimeError('NETWORK_DENIED')

    def no_listener(*args, **kwargs):
        metrics['listener_attempts'] += 1
        raise RuntimeError('LISTENER_DENIED')

    class GuardedConnection(sqlite3.Connection):
        def execute(self, *args, **kwargs):
            guard('sql')
            return super().execute(*args, **kwargs)

    native_connect = sqlite3.connect

    def connect(*args, **kwargs):
        kwargs['factory'] = GuardedConnection
        return native_connect(*args, **kwargs)

    fcntl.flock, os.read = flock, read
    sqlite3.connect = connect
    socket.socket.connect = socket.socket.connect_ex = no_network
    socket.create_connection = socket.getaddrinfo = no_network
    socket.socket.bind = socket.socket.listen = no_listener
    sys.addaudithook(audit)
    approved = (fixture / 'approval.txt').read_text(encoding='ascii').strip()
    spec = importlib.util.spec_from_file_location('_cold_candidate', fixture / 'entry/cold_host.py')
    cold = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cold
    spec.loader.exec_module(cold)
    # Test-only fixed stage identifiers; no exception text/body/paths exported.
    for function,stage in (('_runtime',1),('_profile',2),('_intake',3),('_credential',5)):
        actual=getattr(cold,function)
        def traced(*args,_actual=actual,_stage=stage,**kwargs):
            try: return _actual(*args,**kwargs)
            except BaseException:
                metrics['failure_stage']=_stage
                raise
        setattr(cold,function,traced)
    original_activate = cold._activate
    actual_exec=cold._Captured.exec_module
    def traced_exec(loader,module):
        try: return actual_exec(loader,module)
        except BaseException:
            metrics['failed_import_execs']+=1
            raise
    cold._Captured.exec_module=traced_exec
    if mode=='credential_checkpoint':
        actual_credential=cold._credential
        def credential_checkpoint(host,value):
            print('CREDENTIAL_READY',flush=True)
            if sys.stdin.readline().strip()!='CONTINUE': raise RuntimeError('CHECKPOINT_REFUSED')
            return actual_credential(host,value)
        cold._credential=credential_checkpoint

    def activate(captured):
        nonlocal finder
        finder = captured
        if mode in ('checkpoint', 'checkpoint_probe'):
            print('SNAPSHOT_READY', flush=True)
            if sys.stdin.readline().strip() != 'CONTINUE':
                raise RuntimeError('CHECKPOINT_REFUSED')
        try: modules = original_activate(captured)
        except BaseException as error:
            metrics['failure_stage']=4
            metrics['activation_kind']={cold.ColdRefused:1,ImportError:2,ModuleNotFoundError:3,AttributeError:4,TypeError:5}.get(type(error),99)
            metrics['activation_guard']={
                'uncaptured payload import refused':1,'loaded module identity changed':2,
                'loaded source identity changed':3,'loaded source origin changed':4,
                'loaded namespace path changed':5,'cross-module identity mismatch':6}.get(str(error),0)
            metrics['activation_path_changed']=tuple(sys.path)!=captured.path
            metrics['activation_finders_changed']=tuple(sys.meta_path)!=(captured,cold.BuiltinImporter,cold.FrozenImporter,cold.PathFinder)
            missing=[name for name,module in captured.owned.items() if sys.modules.get(name) is not module]
            metrics['activation_missing_modules']=len(missing)
            metrics['activation_missing_trio']=any(name.endswith('._backends.trio') for name in missing)
            frame=error.__traceback__
            while frame and frame.tb_next: frame=frame.tb_next
            if frame and frame.tb_frame.f_code.co_filename==str(fixture/'entry/cold_host.py'):
                metrics['activation_line']=frame.tb_lineno
            raise
        owned.update(captured.owned)
        anchor, protocol, codec, store, ownership, host, httpx = modules
        original_request = httpx.MockTransport.handle_request

        def mocked_request(self, request):
            metrics['transport_requests'] += 1
            return original_request(self, request)

        httpx.MockTransport.handle_request = mocked_request
        metrics['modules_identity'] = (
            protocol.V2Anchor is anchor.V2Anchor and anchor.Lease is ownership.Lease
            and store.Binding is codec.Binding
            and all(sys.modules.get(name) is module for name, module in owned.items()))
        if mode == 'constructor_hold':
            actual_init = store.EventReader.__init__

            def opaque_init(self, *args, **kwargs):
                actual_init(self, *args, **kwargs)
                raise RuntimeError('OPAQUE_CONSTRUCTOR')

            store.EventReader.__init__ = opaque_init
        return modules

    cold._activate = activate
    if mode in ('preloaded_app', 'preloaded_dep'):
        name = 'anchor_v2' if mode == 'preloaded_app' else 'httpx'
        sys.modules[name] = ModuleType(name)
    elif mode == 'foreign_finder':
        class ForeignFinder:
            def find_spec(self, *args):
                return None
        sys.meta_path.insert(0, ForeignFinder())
    elif mode == 'foreign_path':
        sys.path.append(str(fixture))
    elif mode == 'wrong_approval':
        approved = '0' * 64
    try:
        ready = cold.prepare(str(release), str(fixture / 'profile.json'), approved)
        owned.update(finder.owned)
        observed = ready.observe()
        expected_observation = json.loads((fixture / 'expected.json').read_bytes())
        metrics['genesis'] = observed['head'] == expected_observation['head']
        metrics['revision_matches'] = observed['revision'] == expected_observation['revision']
        metrics['pending'] = observed['status'] == 'PENDING_REQUIRES_REVIEW'
        metrics['local_only'] = observed['status'] == expected_observation['status']
        metrics['no_append_flags'] = (
            observed['write_admitted'] is False and observed['purge_or_restore_admitted'] is False
            and not any(hasattr(ready, name) for name in ('append', 'prepare', 'dispatch', 'purge', 'restore', 'serve')))
        metrics['no_freshness_claim'] = not any(observed.get(name) for name in (
            'fresh', 'freshness', 'remote_fresh', 'authenticated_fresh', 'ack', 'acknowledged'))
        metrics['lease_held'] = live_owner()
        if mode == 'checkpoint_probe':
            before_exec = metrics['payload_exec']
            try:
                importlib.import_module('httpx.uncaptured_probe')
            except cold.ColdRefused:
                metrics['probe_blocked'] = 'httpx.uncaptured_probe' not in sys.modules
            metrics['probe_no_exec'] = metrics['payload_exec'] == before_exec
        if mode == 'loader_drift':
            before_sql = metrics['sql']
            sys.modules['anchor_v2'].__spec__.origin = 'SYNTHETIC_DRIFT'
            try:
                ready.observe()
            except cold.ColdRefused:
                metrics['drift_blocked_before_sql'] = metrics['sql'] == before_sql
        if mode == 'handler':
            try:
                ready._reader.read_next(ready._anchor.load().head)
            except Exception:
                metrics['handler_blocked'] = True
        if mode == 'one_attempt':
            before = (metrics['sql'], metrics['credential_opens'], metrics['payload_exec'])
            try:
                cold.prepare(str(release), str(fixture / 'profile.json'), approved)
            except cold.ColdRefused:
                metrics['one_attempt'] = before == (metrics['sql'], metrics['credential_opens'], metrics['payload_exec'])
        if mode == 'close_hold':
            def broken_close():
                raise RuntimeError('OPAQUE_CLOSE')
            ready._reader.close = broken_close
            try:
                ready.close()
            except cold.ColdRefused:
                try:
                    ready.observe()
                except cold.ColdRefused:
                    metrics['observe_blocked'] = True
            raise RuntimeError('HOLD_CLOSE')
        if mode == 'foreign_cleanup':
            sentinel = ModuleType('anchor_v2')
            sys.modules['anchor_v2'] = sentinel
            before_sql=metrics['sql']
            try: ready.observe()
            except cold.ColdRefused:
                metrics['observe_blocked']=metrics['sql']==before_sql
        ready.close()
        if mode == 'foreign_cleanup':
            metrics['foreign_preserved'] = sys.modules.get('anchor_v2') is sentinel
            owned.pop('anchor_v2')
        if mode=='legacy_pin':
            old=owned['owned_host'];core=fixture/'legacy/core'
            bad=core/'ops/backup/erasure_anchor.py'
            mismatches=[name for name,digest in old.CORE_PINS.items()
                if hashlib.sha256(old._root_source(core/name,600000)).hexdigest()!=digest]
            actual_source=old._root_source
            def checked_source(path,limit):
                body=actual_source(path,limit)
                if Path(path)==bad:
                    metrics['legacy_bad_anchor_checked']=hashlib.sha256(body).hexdigest()!=old.CORE_PINS['ops/backup/erasure_anchor.py']
                return body
            old._root_source=checked_source
            before=(metrics['sql'],metrics['credential_opens'],metrics['credential_body_reads'])
            try:
                old.prepare_host(core/'ops/backup/erasure_custodian_host.py',fixture/'legacy/profile.json',
                    secret,fixture/'legacy/ownership.json',
                    (fixture/'legacy/approval.txt').read_text(encoding='ascii').strip())
            except old.OwnershipError:
                metrics['legacy_pin_refused']=(mismatches==['ops/backup/erasure_anchor.py']
                    and metrics['legacy_bad_anchor_checked'] and not live_owner() and not old._FAILED_OWNERS
                    and before==(metrics['sql'],metrics['credential_opens'],metrics['credential_body_reads']))
            finally: old._root_source=actual_source
        metrics['lease_released'] = not live_owner()
        metrics['modules_clean'] = all(sys.modules.get(name) is not module for name, module in owned.items())
        metrics['finder_clean'] = tuple(sys.meta_path) == original_meta
        return dict(ok=True, refused=False, **metrics)
    except BaseException:
        if finder is not None:
            owned.update(finder.owned)
        metrics['lease_held'] = live_owner()
        metrics['lease_released'] = not metrics['lease_held']
        metrics['finder_clean'] = finder is None or finder not in sys.meta_path
        metrics['modules_clean'] = all(sys.modules.get(name) is not module for name, module in owned.items())
        metrics['retained'] = bool(cold._FAILED) and metrics['lease_held'] and finder in sys.meta_path
        result = dict(ok=False, refused=True, **metrics)
        if mode in ('constructor_hold', 'close_hold'):
            print(json.dumps(dict(event='HOLDING', **result), sort_keys=True), flush=True)
            if sys.stdin.readline().strip() != 'RELEASE':
                raise RuntimeError('HOLD_PROTOCOL')
        return result


if __name__ == '__main__':
    try:
        result = run()
        print(json.dumps(result, sort_keys=True), flush=True)
        raise SystemExit(0)
    except SystemExit:
        raise
    except BaseException:
        # Never format exceptions, tracebacks, paths, keys, requests or payloads.
        print('{"harness_error":"WORKER_FAILED","ok":false,"refused":true}', flush=True)
        raise SystemExit(70)
