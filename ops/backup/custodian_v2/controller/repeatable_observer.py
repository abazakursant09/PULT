"""Repeated OFFLINE observations in ONE live trusted root controller only.

A durable session fence is never removed. No restart/rearm/recovery/installer
or live transport. Root/OS/interpreter and fixed local wiring stay trusted.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
from types import ModuleType,SimpleNamespace
from uuid import uuid4

OBSERVER_SHA='65462111526213daa1e62b3cbe81e146e8277f7eb5d521b70266f28046972739'
SUPERVISOR_SHA='3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e'
FORMAT='pult-live-controller-observer-profile-v1'
POLICY='LIVE_CONTROLLER_ONLY_KEEP_ALL_FENCES_NO_RESTART_ADOPTION_V1'
MAX_CALLS=64
UNKNOWN=b'OBSERVER_SESSION_UNKNOWN\n'
REFUSED=b'OBSERVER_SESSION_REFUSED\n'
FIELDS={'format','policy','base_manifest','base_manifest_sha256','observer_file','supervisor_file',
 'lifecycle_file','lifecycle_sha256','control_root','control_identity','lock_identity',
 'operations_identity','epoch','boot','max_calls'}

class Refused(Exception): pass
class Uncertain(Exception): pass

def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')

def digest(raw): return hashlib.sha256(raw).hexdigest()

def runtime():
    if (sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12)
        or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.flags.optimize
        or Path(sys.executable).resolve()!=Path('/usr/bin/python3.12') or os.getuid()!=0 or os.geteuid()!=0):
        raise Refused('isolated nonoptimized fixed root runtime required')

def root_bytes(file,limit=100000):
    file=Path(file)
    if not file.is_absolute() or '..' in file.parts: raise Refused('absolute trusted path required')
    for parent in file.parents:
        info=parent.lstat();sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or (info.st_mode&0o022 and not sticky)
            or any(n.startswith('system.posix_acl_') for n in os.listxattr(parent,follow_symlinks=False))):
            raise Refused('trusted ancestor required')
    fd=os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1 or before.st_mode&0o022
            or not 0<before.st_size<=limit or any(n.startswith('system.posix_acl_') for n in os.listxattr(fd))):
            raise Refused('trusted bounded regular file required')
        raw=os.pread(fd,limit+1,0);after=os.fstat(fd)
        if len(raw)!=before.st_size or any(getattr(before,k)!=getattr(after,k) for k in
                ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')): raise Refused('intake changed')
        return raw
    finally: os.close(fd)

def captured(file,wanted,name):
    raw=root_bytes(file)
    if digest(raw)!=wanted: raise Refused('fixed source refused')
    module=ModuleType(name);module.__file__=str(file)
    exec(compile(raw,str(file),'exec'),module.__dict__);return module

def identity_bytes(spec):
    return canonical(dict(format='pult-observer-session-control-v1',profile_sha256=digest(canonical(spec))))

class Prepared:
    def __repr__(self): return '<LiveControllerObserver repeated local reads; no restart authority>'
    @property
    def expected_request(self): return self.base.expected_request
    @property
    def caller_uid(self): return self.base.caller_uid
    @property
    def caller_gid(self): return self.base.caller_gid
    def validate(self,owned_base_fence=None):
        runtime();o=self.o;s=self.spec
        if (canonical(s)!=self.raw or digest(self.raw)!=self.profile_sha or root_bytes(self.manifest)!=self.raw
            or self.control!=o.path(s['control_root']) or self.epoch!=s['epoch'] or self.boot!=s['boot']):
            raise Refused('independent profile/cache changed')
        if (digest(root_bytes(o.path(s['lifecycle_file'])))!=s['lifecycle_sha256']
            or digest(root_bytes(o.path(s['observer_file'])))!=OBSERVER_SHA
            or digest(root_bytes(o.path(s['supervisor_file'])))!=SUPERVISOR_SHA):
            raise Refused('source closure changed')
        self.base.validate()
        if self.base.profile_sha!=s['base_manifest_sha256'] or self.base.manifest!=o.path(s['base_manifest']):
            raise Refused('base approval changed')
        # Never turn an existing one-shot/UNKNOWN claim into a new session.
        base_marker=self.base.control/self.a.CLAIM_NAME
        if owned_base_fence is None:
            if os.path.lexists(base_marker): raise Refused('base fence exists; preserve it')
        else:
            expected_identity,expected_raw=owned_base_fence
            o.check_inode(base_marker,expected_identity,0,0o600)
            if o.root_read(base_marker,4096)!=expected_raw: raise Refused('owned compatibility fence changed')
        if self.base.boot!=self.boot: raise Refused('boot reapproval required')
        o.check_inode(self.control,s['control_identity'],0,0o700,stat.S_ISDIR)
        o.check_inode(self.control/'session.lock',s['lock_identity'],0,0o600)
        if o.root_read(self.control/'session.lock',1)!=b'': raise Refused('lock changed')
        o.check_inode(self.control/'operations',s['operations_identity'],0,0o700,stat.S_ISDIR)
        if o.root_read(self.control/'session.identity',4096)!=identity_bytes(s): raise Refused('control identity changed')
        if not {p.name for p in self.control.iterdir()}.issubset({'session.identity','session.lock','operations','session.claim'}):
            raise Refused('foreign control state')

def prepare(manifest,approved_sha):
    runtime();manifest=Path(manifest);raw=root_bytes(manifest,16384);spec=json.loads(raw)
    if (type(approved_sha) is not str or not re.fullmatch('[0-9a-f]{64}',approved_sha)
        or digest(raw)!=approved_sha or type(spec) is not dict or canonical(spec)!=raw
        or set(spec)!=FIELDS or spec['format']!=FORMAT or spec['policy']!=POLICY
        or type(spec['max_calls']) is not int or spec['max_calls']!=MAX_CALLS): raise Refused('exact new profile required')
    observer=captured(Path(spec['observer_file']),OBSERVER_SHA,'_session_pinned_observer_v2')
    supervisor=captured(Path(spec['supervisor_file']),SUPERVISOR_SHA,'_session_pinned_supervisor_v2')
    for key in ('base_manifest_sha256','lifecycle_sha256'): observer.hash64(spec[key])
    for key in ('epoch','boot'): observer.uuid4(spec[key])
    control=observer.path(spec['control_root'])
    for key in ('base_manifest','observer_file','supervisor_file','lifecycle_file'):
        file=observer.path(spec[key])
        if file.is_relative_to(control): raise Refused('source/approval outside durable control required')
    if (observer.path(spec['lifecycle_file'])!=Path(__file__) or manifest.is_relative_to(control)
        or digest(root_bytes(Path(__file__)))!=spec['lifecycle_sha256']): raise Refused('actual lifecycle source required')
    base=observer.prepare(observer.path(spec['base_manifest']),spec['base_manifest_sha256'])
    if (control==base.control or control.is_relative_to(base.control) or base.control.is_relative_to(control)
        or control.is_relative_to(base.root) or base.root.is_relative_to(control)):
        raise Refused('separate versioned control required')
    supervisor.load_core(observer.path(base.spec['legacy_core']))
    result=Prepared();result.spec=spec;result.raw=raw;result.profile_sha=approved_sha;result.manifest=manifest
    result.control=control;result.epoch=spec['epoch'];result.boot=spec['boot'];result.base=base
    result.o=observer;result.a=supervisor;result.validate();return result

class Session:
    """Memory capability belongs to this live PID and thread, never disk input.

    All disk artifacts are immutable evidence/fences, never restart permission.
    The session.claim survives success, close, cancellation, and controller loss.
    """
    def __init__(self,profile):
        runtime();self.profile=profile;self.pid=os.getpid();self.tid=threading.get_native_id();self.thread=threading.current_thread()
        self.state='NEW';self.lock=None;self.fence=None;self.fence_raw=None;self.fence_identity=None
        self.base_lock=None;self.compat=None;self.compat_raw=None;self.compat_identity=None
        self.session_id=uuid4().hex;self.completed=[];self.mutex=threading.Lock()
    def event(self,name,operation=None): pass  # trusted test subclass only, not wire config
    def same(self):
        runtime()
        if (os.getpid()!=self.pid or threading.get_native_id()!=self.tid or threading.current_thread() is not self.thread):
            raise Refused('original live controller required')
    def owned_compat(self):
        if self.compat is None: return None
        info=os.fstat(self.compat)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600
            or info.st_nlink!=1 or (info.st_dev,info.st_ino)!=self.compat_identity):
            raise Uncertain('compatibility fence descriptor changed')
        return ({'device':info.st_dev,'inode':info.st_ino},self.compat_raw)
    def sync_dir(self,path):
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try: os.fsync(fd)
        finally: os.close(fd)
    def write_new(self,file,raw,phase):
        self.event(phase+'_BEFORE_OPEN')
        fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            os.fchmod(fd,0o600);info=os.fstat(fd)
            created={'device':info.st_dev,'inode':info.st_ino}
            self.event(phase+'_OPENED')
            offset=0
            while offset<len(raw):
                count=os.write(fd,raw[offset:])
                if count<=0: raise Uncertain('record write incomplete')
                offset+=count
            self.event(phase+'_WRITTEN');os.fsync(fd);self.event(phase+'_FILE_FSYNCED')
            self.sync_dir(file.parent);self.event(phase+'_DIR_FSYNCED')
        finally: os.close(fd)
        self.event(phase+'_CLOSED')
        self.profile.o.check_inode(file,created,0,0o600)
        if self.profile.o.root_read(file,8192)!=raw: raise Uncertain('created evidence changed')
        return created
    def check(self):
        self.same();p=self.profile;p.validate(self.owned_compat());a=p.a;o=p.o
        if self.base_lock is not None:
            if a._check_lock(self.base_lock,p.base.control/a.LOCK_NAME)!=(p.base.spec['lock_identity']['device'],p.base.spec['lock_identity']['inode']):
                raise Uncertain('pinned base lock changed')
        if self.lock is not None:
            if a._check_lock(self.lock,p.control/'session.lock')!=(p.spec['lock_identity']['device'],p.spec['lock_identity']['inode']):
                raise Uncertain('pinned lock changed')
        if self.fence is not None:
            info=os.fstat(self.fence)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600
                or (info.st_dev,info.st_ino)!=self.fence_identity or info.st_nlink!=1
                or o.root_read(p.control/'session.claim',4096)!=self.fence_raw
                or o.identity(p.control/'session.claim')!={'device':info.st_dev,'inode':info.st_ino}):
                raise Uncertain('session fence changed')
        operations=p.control/'operations'
        if {x.name for x in operations.iterdir()}!={row[0].name for row in self.completed}:
            raise Uncertain('unknown/incomplete operation; no adoption')
        for directory,claim_raw,complete_raw,unit,dir_id,claim_id,complete_id in self.completed:
            o.check_inode(directory,dir_id,0,0o700,stat.S_ISDIR)
            o.check_inode(directory/a.CLAIM_NAME,claim_id,0,0o600)
            o.check_inode(directory/'complete.json',complete_id,0,0o600)
            info=directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700
                or {x.name for x in directory.iterdir()}!={a.CLAIM_NAME,'complete.json'}
                or o.root_read(directory/a.CLAIM_NAME,4096)!=claim_raw
                or o.root_read(directory/'complete.json',8192)!=complete_raw
                or not a._empty(unit) or a._state(unit).get('ActiveState') not in ('inactive','failed')):
                raise Uncertain('completed evidence or cleanup changed')
    def begin(self,body):
        p=self.profile;a=p.a;o=p.o
        if self.state not in ('NEW','READY'): raise Refused('session is fenced or closed')
        if self.fence is None and os.path.lexists(p.control/'session.claim'):
            raise Refused('prior session fence exists; no adoption')
        self.check()
        if len(self.completed)>=MAX_CALLS: raise Refused('protective call bound; no automatic GC')
        if self.lock is None:
            # Any existing marker, including empty/partial/completed, blocks a
            # fresh controller. Complete disk evidence is NEVER adopted.
            if os.path.lexists(p.control/'session.claim'): raise Refused('prior session fence exists')
            self.base_lock=os.open(p.base.control/a.LOCK_NAME,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
            a._check_lock(self.base_lock,p.base.control/a.LOCK_NAME)
            try: fcntl.flock(self.base_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Refused('base-v2 controller owns lock') from None
            self.lock=os.open(p.control/'session.lock',os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
            a._check_lock(self.lock,p.control/'session.lock')
            try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Refused('other controller owns session lock') from None
            self.check()
            if os.path.lexists(p.control/'session.claim'): raise Refused('prior session fence exists')
            self.state='BUSY'
            self.fence_raw=canonical(dict(format='pult-observer-live-controller-fence-v1',session_id=self.session_id,
                profile_sha256=p.profile_sha,request_sha256=digest(body),epoch=p.epoch,boot=p.boot))
            fence_created=self.write_new(p.control/'session.claim',self.fence_raw,'SESSION_FENCE')
            self.fence=os.open(p.control/'session.claim',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
            info=os.fstat(self.fence);self.fence_identity=(fence_created['device'],fence_created['inode'])
            if (info.st_dev,info.st_ino)!=self.fence_identity: raise Uncertain('session fence replaced at reopen')
            # A permanent compatibility fence also blocks the unchanged v2
            # entry after this controller dies/releases the base lock. It is
            # created ONLY in an empty approved base control, never replaces
            # an old claim. It names no unused unit or writer credential.
            self.compat_raw=canonical(dict(format='pult-observer-session-compatibility-fence-v1',
                session_id=self.session_id,session_profile_sha256=p.profile_sha,session_fence_sha256=digest(self.fence_raw)))
            compat_created=self.write_new(p.base.control/a.CLAIM_NAME,self.compat_raw,'BASE_COMPAT_FENCE')
            self.compat=os.open(p.base.control/a.CLAIM_NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
            info=os.fstat(self.compat);self.compat_identity=(compat_created['device'],compat_created['inode'])
            if (info.st_dev,info.st_ino)!=self.compat_identity: raise Uncertain('compatibility fence replaced at reopen')
        self.check()
        self.state='BUSY';self.event('BEFORE_OPERATION_DIRECTORY')
        directory=p.control/'operations'/str(len(self.completed)+1).zfill(6)
        directory.mkdir(mode=0o700);dir_created=o.identity(directory);self.event('OPERATION_DIRECTORY_CREATED')
        o.check_inode(directory,dir_created,0,0o700,stat.S_ISDIR)
        self.sync_dir(directory.parent);self.event('OPERATION_DIRECTORY_FSYNCED')
        o.check_inode(directory,dir_created,0,0o700,stat.S_ISDIR)
        base=p.base
        view=SimpleNamespace(root=base.root,control=directory,caller_uid=base.caller_uid,caller_gid=base.caller_gid,
            writer_uid=base.writer_uid,writer_gid=base.writer_gid,expected_request=body,profile_sha=p.profile_sha,
            epoch=p.epoch,boot=p.boot,argv=base.argv,accept_reply=base.accept_reply,directory_identity=dir_created)
        self.event('BEFORE_OPERATION_CLAIM',view)
        claim=a.Claim.begin(view,body)
        try: self.event('OPERATION_CLAIM_DURABLE',view)
        except BaseException: claim.close();raise
        return view,claim
    def call(self,connection,manager=None):
        p=self.profile;a=p.a;caller=None;claim=None;metadata={'launched':False};acquired=False;touched=False
        try:
            try:
                self.same()
                body,caller=a.receive(connection,p);caller.check()
                if not self.mutex.acquire(blocking=False): raise Refused('same-controller call busy')
                acquired=True
                if self.state not in ('NEW','READY'): raise Refused('session fenced or closed')
                touched=True;view,claim=self.begin(body);caller.check()
                p.validate(self.owned_compat())
                p.o.check_inode(view.control,view.directory_identity,0,0o700,stat.S_ISDIR)
                claim_created={'device':claim.identity[0],'inode':claim.identity[1]}
                p.o.check_inode(view.control/a.CLAIM_NAME,claim_created,0,0o600)
                metadata['launched']=True
                answer,evidence=(manager or a.Manager()).run(view,body,caller);metadata.update(evidence)
                self.event('MANAGER_RETURNED',view);caller.check();p.validate(self.owned_compat())
                if p.base.accept_reply(body,answer)!=answer or answer in (a.UNKNOWN,a.REFUSED): raise Uncertain('nonpending observation required')
                claim.finish(view,metadata);self.event('OPERATION_FINISH_SYNCED',view)
                p.o.check_inode(view.control,view.directory_identity,0,0o700,stat.S_ISDIR)
                complete=canonical(dict(format='pult-observer-local-completion-v1',session_id=self.session_id,
                    sequence=len(self.completed)+1,profile_sha256=p.profile_sha,operation_claim_sha256=digest(claim.body),
                    unit=claim.unit,reply_sha256=digest(answer),manager_sha256=digest(canonical(evidence)),
                    status='LOCAL_OBSERVATION_CLEANED_NOT_RESTART_PERMISSION'))
                complete_created=self.write_new(view.control/'complete.json',complete,'COMPLETION')
                caller.check();p.validate(self.owned_compat());self.event('BEFORE_MEMORY_READY',view)
                o=p.o
                self.completed.append((view.control,claim.body,complete,claim.unit,view.directory_identity,
                    claim_created,complete_created));self.state='READY'
                self.check();self.event('AFTER_MEMORY_READY',view)
            except Exception as error:
                if touched: self.state='BLOCKED'
                refused=isinstance(error,(Refused,p.o.Refused,a.Refused,a._core().Refused,OSError,ValueError,TimeoutError))
                answer=REFUSED if refused and not metadata['launched'] else UNKNOWN
            except BaseException:
                if touched: self.state='BLOCKED'
                raise
            try:
                connection.settimeout(2);connection.sendall(answer);connection.shutdown(1)
            except OSError:
                if touched: self.state='BLOCKED'
                answer=UNKNOWN
            except BaseException:
                if touched: self.state='BLOCKED'
                raise
            return answer,metadata
        finally:
            try:
                try:
                    if claim is not None: claim.close()
                finally:
                    if caller is not None: caller.close()
            except BaseException:
                if touched: self.state='BLOCKED'
                raise
            finally:
                if acquired: self.mutex.release()
    def close(self):
        self.same();self.state='CLOSED'
        # No unlock on inherited object, no claim removal or automatic rearm.
        try:
            if self.fence is not None:
                fd=self.fence;self.fence=None;os.close(fd)
        finally:
            try:
                if self.lock is not None:
                    fd=self.lock;self.lock=None;os.close(fd)
            finally:
                try:
                    if self.compat is not None:
                        fd=self.compat;self.compat=None;os.close(fd)
                finally:
                    if self.base_lock is not None:
                        fd=self.base_lock;self.base_lock=None;os.close(fd)
