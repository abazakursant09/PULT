"""Separate root supervisor for the OFFLINE, read-only observer-v2 candidate.

Trusted local wiring must call load_core(absolute_legacy_path) once before use.
Only the exact pinned legacy bytes supply low-level transport/manager helpers;
their namespace is neither registered globally nor patched.  Profile/protocol
approval belongs to observer_v2.Prepared, not to socket input or this loader.
There is no listener, bootstrap, claim recovery, conversion or deployment CLI.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import subprocess
import sys
import threading
import time
from types import ModuleType
import uuid


CORE_SHA='961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7'
LEGACY_SHA256=CORE_SHA
MAX_REPLY=2048
MAX_CLAIM=4096
IO_SECONDS=2.0
RUNTIME_SECONDS=40
STOP_SECONDS=2
# Deliberately retain the ONLY unit namespace accepted by legacy _empty/cleanup.
PREFIX='pult-call-gate-'
LOCK_NAME='observer-v2.lock'
CLAIM_NAME='observer-v2.claim'
CLAIM_FORMAT='pult-observer-operation-claim-v2'
UNKNOWN=b'OBSERVER_V2_UNKNOWN\n'
REFUSED=b'OBSERVER_V2_REFUSED\n'

_CORE=None
_CORE_PATH=None
_CORE_LOCK=threading.Lock()


class Refused(Exception): pass
class Uncertain(Exception): pass


def _runtime():
    if (sys.platform!='linux' or sys.implementation.name!='cpython'
            or sys.version_info[:2]!=(3,12) or not sys.flags.isolated
            or not sys.flags.no_site or not sys.dont_write_bytecode
            or sys.flags.optimize or not __debug__
            or Path(sys.executable).resolve()!=Path('/usr/bin/python3.12')
            or os.getuid()!=0 or os.geteuid()!=0):
        raise Refused('isolated nonoptimized fixed root runtime required')


def _trusted_ancestors(path):
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('absolute trusted path required')
    for parent in path.parents:
        info=parent.lstat()
        sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=0
                or (info.st_mode&0o022 and not sticky)
                or any(name.startswith('system.posix_acl_')
                       for name in os.listxattr(parent,follow_symlinks=False))):
            raise Refused('trusted ancestor required')


def _private_file(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid==0
            and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600)


def _identity(info):
    return info.st_dev,info.st_ino


def load_core(path,sha=CORE_SHA):
    """Load unmodified, vetted bytes into one private ModuleType; return it.

    The path is supplied by trusted local wiring, NEVER by a connected caller.
    sha is not an approval knob: only CORE_SHA is accepted. Repeating the same
    configuration is idempotent; replacing it in a running supervisor is refused.
    No legacy call/Profile/Claim/accept_reply entry is used by this supervisor.
    """
    global _CORE,_CORE_PATH
    _runtime()
    if type(sha) is not str or sha!=CORE_SHA:
        raise Refused('fixed legacy digest required')
    path=Path(path)
    _trusted_ancestors(path)
    with _CORE_LOCK:
        if _CORE is not None:
            if path!=_CORE_PATH: raise Refused('legacy core already configured')
            return _CORE
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        try:
            before=os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0
                    or before.st_nlink!=1 or before.st_mode&0o022
                    or not 0<before.st_size<=100000
                    or any(name.startswith('system.posix_acl_') for name in os.listxattr(fd))):
                raise Refused('trusted bounded legacy file required')
            source=bytearray()
            while len(source)<=100000:
                part=os.read(fd,min(8192,100001-len(source)))
                if not part: break
                source.extend(part)
            after=os.fstat(fd)
            if (len(source)!=before.st_size or any(getattr(before,key)!=getattr(after,key)
                    for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))
                    or _identity(path.lstat())!=_identity(before)
                    or hashlib.sha256(source).hexdigest()!=CORE_SHA):
                raise Refused('exact stable legacy bytes required')
        finally:
            os.close(fd)
        core=ModuleType('_pult_observer_v2_legacy_961cc3ae')
        core.__file__=str(path)
        core.__package__=''
        # No source rewriting, sys.modules registration or monkey-patching.
        exec(compile(bytes(source),str(path),'exec',dont_inherit=True,optimize=0),core.__dict__)
        _CORE=core
        _CORE_PATH=path
        return core


def _core():
    if _CORE is None: raise Refused('trusted load_core invocation required')
    return _CORE


# Thin aliases keep the harness surface without changing legacy function globals.
def canonical(value): return _core().canonical(value)
def receive(connection,profile): return _core().receive(connection,profile)
def _cmd(argv,timeout=5,check=True): return _core()._cmd(argv,timeout=timeout,check=check)
def _state(unit): return _core()._state(unit)
def _empty(unit): return _core()._empty(unit)


def _sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)


def _binding(profile,body):
    if type(body) is not bytes or body!=profile.expected_request:
        raise Refused('fixed observer request required')
    if (type(profile.profile_sha) is not str
            or not re.fullmatch('[0-9a-f]{64}',profile.profile_sha)):
        raise Refused('approved manifest digest required')
    for value in (profile.epoch,profile.boot):
        if type(value) is not str or not 0<len(value)<=128:
            raise Refused('explicit boot and control epoch required')
    # profile_sha is the independently approved full manifest SHA, not merely a
    # source hash. Prepared.validate proves its exact epoch/boot/source closure.
    return {'profile_sha256':profile.profile_sha,
            'request_sha256':hashlib.sha256(body).hexdigest(),
            'control_epoch':profile.epoch,'host_boot_id':profile.boot}


def _read_claim(path,expected_identity=None):
    _trusted_ancestors(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if (not _private_file(before) or not 0<before.st_size<=MAX_CLAIM
                or (expected_identity is not None and _identity(before)!=expected_identity)
                or any(name.startswith('system.posix_acl_') for name in os.listxattr(fd))):
            raise Uncertain('private bounded claim required')
        raw=os.pread(fd,MAX_CLAIM+1,0)
        after=os.fstat(fd)
        current=path.lstat()
        if (len(raw)!=before.st_size or not _private_file(current)
                or _identity(current)!=_identity(before)
                or any(getattr(before,key)!=getattr(after,key) for key in
                       ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))):
            raise Uncertain('stable claim inode required')
        return raw
    finally:
        os.close(fd)


def _claim_record(profile,body):
    raw=_read_claim(Path(profile.control)/CLAIM_NAME)
    try:
        value=json.loads(raw)
        fields={'format','operation_id','unit',*_binding(profile,body)}
        if (type(value) is not dict or set(value)!=fields or canonical(value)!=raw
                or value['format']!=CLAIM_FORMAT
                or type(value['operation_id']) is not str
                or not re.fullmatch('[0-9a-f]{32}',value['operation_id'])
                or value['unit']!=PREFIX+value['operation_id']+'.service'
                or any(value[key]!=item for key,item in _binding(profile,body).items())):
            raise Uncertain('exact observer-v2 claim required')
        return value
    except (ValueError,TypeError,KeyError,RecursionError) as error:
        raise Uncertain('exact observer-v2 claim required') from error


class Claim:
    """Durable crash fence. No TTL, PID recovery, automatic unlink or retry."""
    NAME=CLAIM_NAME

    @classmethod
    def begin(cls,profile,body):
        claim=cls()
        claim.path=Path(profile.control)/cls.NAME
        claim.fd=None
        claim.binding=_binding(profile,body)
        token=uuid.uuid4().hex
        claim.unit=PREFIX+token+'.service'
        claim.body=canonical(dict(claim.binding,format=CLAIM_FORMAT,
                                  operation_id=token,unit=claim.unit))
        _trusted_ancestors(claim.path)
        try:
            claim.fd=os.open(claim.path,os.O_RDWR|os.O_CREAT|os.O_EXCL
                             |os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        except FileExistsError:
            raise Refused('unresolved observer-v2 operation exists') from None
        try:
            os.fchmod(claim.fd,0o600)
            initial=os.fstat(claim.fd)
            if not _private_file(initial): raise Uncertain('private claim required')
            claim.identity=_identity(initial)
            offset=0
            while offset<len(claim.body):
                count=os.write(claim.fd,claim.body[offset:])
                if count<=0: raise Uncertain('claim write incomplete')
                offset+=count
            os.fsync(claim.fd)
            _sync_directory(claim.path.parent)
            if _read_claim(claim.path,claim.identity)!=claim.body:
                raise Uncertain('durable exact claim required')
            return claim
        except BaseException:
            claim.close()  # Leave even a partial marker. Never launch/fall back.
            raise

    def finish(self,profile,metadata):
        final=metadata.get('manager')
        if (Path(profile.control)/self.NAME!=self.path
                or _binding(profile,profile.expected_request)!=self.binding
                or metadata.get('unit')!=self.unit
                or metadata.get('cleanup_confirmed') is not True
                or metadata.get('cgroup_empty') is not True
                or metadata.get('pipe_eof_confirmed') is not True
                or type(metadata.get('supervisor_child_exit')) is not int
                or metadata['supervisor_child_exit']!=0
                or type(final) is not dict or final.get('Result')!='success'
                or final.get('ExecMainCode')!='1' or final.get('ExecMainStatus')!='0'
                or final.get('SubState')!='exited'
                or not _empty(self.unit)
                or _state(self.unit).get('ActiveState') not in ('inactive','failed')):
            raise Uncertain('observer completion not confirmed')
        descriptor=os.fstat(self.fd)
        current=self.path.lstat()
        if (not _private_file(descriptor) or not _private_file(current)
                or _identity(descriptor)!=self.identity or _identity(current)!=self.identity
                or os.pread(self.fd,len(self.body)+1,0)!=self.body
                or _read_claim(self.path,self.identity)!=self.body):
            raise Uncertain('observer claim ownership changed')
        # Bounded SINGLE-SHOT profile: the already durable original claim is
        # NEVER unlinked, including after a successful local observation. An
        # unlink->directory-fsync crash could otherwise rearm an UNKNOWN call.
        # A positive reply is evidence, not permission to rearm this profile.
        # Repeated operation requires a separately reviewed recovery/lifecycle
        # protocol; this observer exposes no reset/retirement/TTL authority.
        os.fsync(self.fd)
        _sync_directory(self.path.parent)

    def close(self):
        if self.fd is not None:
            fd=self.fd
            self.fd=None
            os.close(fd)


def _accept_reply(profile,body,stdout):
    if type(stdout) is not bytes or not 0<len(stdout)<=MAX_REPLY or stdout in (UNKNOWN,REFUSED):
        return UNKNOWN
    try:
        accepted=profile.accept_reply(body,stdout)
        return stdout if type(accepted) is bytes and accepted==stdout else UNKNOWN
    except Exception:
        # Protocol rejection (including pending) is UNKNOWN, never v1 fallback.
        return UNKNOWN


class Manager:
    """Trusted local manager only; no caller-selected paths/options/timeouts."""
    runtime=RUNTIME_SECONDS
    stop=STOP_SECONDS

    def started(self,unit,initial):
        pass  # Trusted proof subclasses may inject cancellation/faults here.

    def cleanup(self,unit):
        return _core().Manager.cleanup(self,unit)

    def run(self,profile,body,caller):
        # Explicit observer-v2 claim reader, NOT the legacy operation.claim.
        unit=_claim_record(profile,body)['unit']
        if _state(unit).get('LoadState')!='not-found': raise Uncertain('unit collision')
        properties={'Type':'exec','ExitType':'cgroup','RemainAfterExit':'yes',
            'User':str(profile.writer_uid),'Group':str(profile.writer_gid),'SupplementaryGroups':'',
            'RuntimeMaxSec':str(self.runtime)+'s','TimeoutStartSec':'2s','TimeoutStopSec':str(self.stop)+'s',
            'KillMode':'control-group','KillSignal':'SIGTERM','SendSIGKILL':'yes','FinalKillSignal':'SIGKILL',
            'Restart':'no','NoNewPrivileges':'yes','CapabilityBoundingSet':'','AmbientCapabilities':'',
            'PrivateNetwork':'yes','PrivateTmp':'yes','PrivateDevices':'yes','ProtectSystem':'strict',
            'ProtectHome':'yes','ProtectControlGroups':'yes','RestrictAddressFamilies':'AF_UNIX',
            'BindReadOnlyPaths':str(profile.root),'TasksMax':'16','MemoryMax':'128M',
            'LimitFSIZE':'4096','UMask':'0077',
            'Environment':'PATH=/usr/bin:/bin LANG=C.UTF-8'}
        argv=['/usr/bin/systemd-run','--quiet','--pipe','--no-ask-password','--expand-environment=no','--unit='+unit]
        for key,value in sorted(properties.items()): argv.append('--property='+key+'='+value)
        argv.extend(profile.argv())
        created=False; final={}; clean=False; started=time.monotonic(); process=None
        selector=selectors.DefaultSelector(); buffers={'stdout':bytearray(),'stderr':bytearray()}; offset=0
        def pump():
            nonlocal offset
            for key,mask in selector.select(.025):
                stream=key.fileobj; name=key.data
                if name=='stdin':
                    try: count=os.write(stream.fileno(),body[offset:])
                    except BlockingIOError: continue
                    if count<=0: raise Uncertain('request write incomplete')
                    offset+=count
                    if offset==len(body): selector.unregister(stream);stream.close()
                else:
                    try: chunk=os.read(stream.fileno(),4096)
                    except BlockingIOError: continue
                    if not chunk: selector.unregister(stream);stream.close();continue
                    buffers[name].extend(chunk)
                    if len(buffers[name])>(MAX_REPLY if name=='stdout' else 1024):
                        raise Uncertain('output bound')
        try:
            caller.check()
            created=True
            process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},close_fds=True)
            for name in ('stdin','stdout','stderr'):
                stream=getattr(process,name);os.set_blocking(stream.fileno(),False)
                selector.register(stream,selectors.EVENT_WRITE if name=='stdin' else selectors.EVENT_READ,name)
            deadline=started+3
            while True:
                caller.check();pump();initial=_state(unit)
                if initial.get('LoadState')=='loaded' and initial.get('SubState') not in ('dead','start-pre','start','start-post'): break
                if time.monotonic()>deadline: raise Uncertain('start deadline')
            expected={'RuntimeMaxUSec':str(self.runtime)+'s','TimeoutStopUSec':str(self.stop)+'s',
                'ExitType':'cgroup','KillMode':'control-group','KillSignal':'15','FinalKillSignal':'9',
                'SendSIGKILL':'yes','Restart':'no','NRestarts':'0','User':str(profile.writer_uid),
                'Group':str(profile.writer_gid),'NoNewPrivileges':'yes','ProtectControlGroups':'yes',
                'PrivateNetwork':'yes','CapabilityBoundingSet':''}
            if any(initial.get(k)!=v for k,v in expected.items()): raise Uncertain('manager profile mismatch')
            self.started(unit,initial)
            deadline=started+self.runtime+self.stop+5
            while True:
                caller.check();pump()
                final=_state(unit)
                if final.get('SubState')=='exited' or final.get('ActiveState') in ('failed','inactive'): break
                if time.monotonic()>deadline: raise Uncertain('manager deadline')
            if not _empty(unit): raise Uncertain('descendants remain')
            caller.check()
        finally:
            interrupted=sys.exc_info()[1]
            try:
                if created: self.cleanup(unit); clean=True
                if process is not None:
                    limit=time.monotonic()+2
                    while selector.get_map():
                        if time.monotonic()>limit: raise Uncertain('pipe EOF deadline')
                        pump()
                    process.wait(timeout=max(.01,limit-time.monotonic()))
            finally:
                cleanup_interrupted=sys.exc_info()[1]
                try:
                    selector.close()
                    if process is not None:
                        for name in ('stdin','stdout','stderr'):
                            stream=getattr(process,name)
                            if stream is not None and not stream.closed: stream.close()
                        if process.poll() is None: process.kill();process.wait(timeout=2)
                finally:
                    # A later cleanup/reap Exception must not mask cancellation
                    # and turn it into call()'s ordinary UNKNOWN response.
                    for error in (interrupted,cleanup_interrupted):
                        if error is not None and not isinstance(error,Exception):
                            raise error
        # Candidate bytes remain private until cleanup, CLI reaping and pipe EOF.
        caller.check()
        stdout=bytes(buffers['stdout']);stderr=bytes(buffers['stderr'])
        metadata={'unit':unit,'manager':final,'cleanup_confirmed':clean,'cgroup_empty':_empty(unit),
                  'elapsed_seconds':round(time.monotonic()-started,3),
                  'pipe_eof_confirmed':True,'supervisor_child_exit':process.returncode,
                  'stdout_bytes':len(stdout),'stderr_bytes':len(stderr)}
        good=final.get('Result')=='success' and final.get('ExecMainCode')=='1' and final.get('ExecMainStatus')=='0' and final.get('SubState')=='exited'
        if not good or stderr or offset!=len(body) or process.returncode!=0: return UNKNOWN,metadata
        return _accept_reply(profile,body,stdout),metadata


def _validate(profile):
    try:
        if profile.validate() is False: raise Refused('profile refused')
    except Exception as error:
        # Prepared has its own Refused type; normalize ONLY validation failures.
        # BaseException (notably KeyboardInterrupt/SystemExit) still propagates.
        raise Refused('observer profile refused') from error


def _check_lock(fd,path,identity=None):
    descriptor=os.fstat(fd)
    current=path.lstat()
    if (not _private_file(descriptor) or not _private_file(current)
            or descriptor.st_size!=0 or current.st_size!=0
            or _identity(descriptor)!=_identity(current)
            or (identity is not None and _identity(descriptor)!=identity)
            or any(name.startswith('system.posix_acl_') for name in os.listxattr(fd))):
        raise Refused('existing pinned private observer lock required')
    return _identity(descriptor)


def call(connection,profile,manager=None):
    """One authenticated observation; uncertainty retains the durable claim."""
    lock=None; caller=None; claim=None; metadata={'launched':False}
    core=None
    try:
        try:
            _runtime()
            core=_core()
            # Receive/authenticate before profile intake, locks or unit work.
            body,caller=receive(connection,profile)
            caller.check()
            lock_path=Path(profile.control)/LOCK_NAME
            _trusted_ancestors(lock_path)
            # NO O_CREAT: profile/control bootstrap is a separate trusted act.
            lock=os.open(lock_path,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
            lock_identity=_check_lock(lock,lock_path)
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Refused('observer busy') from None
            _check_lock(lock,lock_path,lock_identity)
            caller.check()
            _validate(profile)
            if Path(profile.control)/LOCK_NAME!=lock_path: raise Refused('control changed')
            _check_lock(lock,lock_path,lock_identity)
            caller.check()
            claim=Claim.begin(profile,body)
            caller.check()
            if manager is None: manager=Manager()
            metadata['launched']=True
            answer,evidence=manager.run(profile,body,caller)
            metadata.update(evidence)
            metadata['launched']=True
            caller.check()
            # Reprove full profile after cleanup and BEFORE touching the fence.
            _validate(profile)
            if Path(profile.control)/LOCK_NAME!=lock_path: raise Refused('control changed')
            _check_lock(lock,lock_path,lock_identity)
            if _accept_reply(profile,body,answer)==UNKNOWN:
                raise Uncertain('valid nonpending observation required before claim release')
            caller.check()
            claim.finish(profile,metadata)
        except Exception as error:
            refused=isinstance(error,(Refused,OSError,ValueError,TimeoutError))
            if core is not None and isinstance(error,core.Refused): refused=True
            answer=REFUSED if refused and not metadata['launched'] else UNKNOWN
        try:
            connection.settimeout(IO_SECONDS)
            connection.sendall(answer)
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            answer=UNKNOWN  # Never retry operation or delivery after disconnect.
        return answer,metadata
    finally:
        try:
            if claim is not None: claim.close()
        finally:
            try:
                if lock is not None: os.close(lock)
            finally:
                if caller is not None: caller.close()
