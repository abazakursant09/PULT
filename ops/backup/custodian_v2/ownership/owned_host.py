"""Single-owner host guard candidate. Existing state only; no CLI or installer."""
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType
import weakref

GUARD_SHA='da3754a860495a5c193daaf27cde3c04b95c55fc58d37dea60af37a58626e1e8'
CORE_PINS={
 'ops/backup/erasure_custodian_host.py':GUARD_SHA,
 'ops/backup/erasure_custodian.py':'3eb793a1ef1002c6def97b52c579b2ff051e834b44b802a75aca09d640a691d3',
 'ops/backup/erasure_anchor.py':'b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8',
 'ops/backup/erasure_journal.py':'94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98',
 'ops/backup/erasure_journal_s3.py':'2886ec08fe17eb409d8d4a8c597015454c21f3ef703c674a7848f30b63bc544f',
 'ops/canary/canary.py':'aa4b357acc6e908341c5523db8f93a32fdf3c0305a7ddc36c4c68842fbe3ee4f',
 'ops/backup/custodian/requirements.lock':'cd1f0a681aae91a132427b88897782b0f2085c8a415cd39812f8e7a059361f08',
}
_LIVE=weakref.WeakSet()
_FAILED_OWNERS=set()  # retain uncertain leases through process exit/fork cleanup

class OwnershipError(Exception): pass

def _canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')

def _identity(info): return {'device':info.st_dev,'inode':info.st_ino}

def _root_source(path,limit):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise OwnershipError('absolute trusted source required')
    for parent in path.parents:
        st=parent.lstat();sticky=st.st_uid==0 and bool(st.st_mode&stat.S_ISVTX)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=0 or (st.st_mode&0o022 and not sticky):
            raise OwnershipError('untrusted source ancestor')
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1
                or stat.S_IMODE(before.st_mode) not in (0o444,0o644) or not 0<before.st_size<=limit):
            raise OwnershipError('bounded root source required')
        body=bytearray()
        while len(body)<=limit:
            part=os.read(fd,min(8192,limit+1-len(body)))
            if not part: break
            body.extend(part)
        after=os.fstat(fd)
        keys=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        if len(body)!=before.st_size or any(getattr(before,k)!=getattr(after,k) for k in keys):
            raise OwnershipError('source changed')
        return bytes(body)
    finally: os.close(fd)

def _local_filesystem(fd):
    # Linux statfs starts with signed long f_type. Oversized buffer avoids
    # depending on trailing ABI fields. Only approved local ext4/XFS supported.
    libc=ctypes.CDLL(None,use_errno=True);buffer=ctypes.create_string_buffer(256)
    libc.fstatfs.argtypes=(ctypes.c_int,ctypes.c_void_p);libc.fstatfs.restype=ctypes.c_int
    if libc.fstatfs(fd,buffer)!=0: raise OwnershipError('filesystem validation unavailable')
    kind=ctypes.c_long.from_buffer(buffer).value
    if kind not in (0xEF53,0x58465342): raise OwnershipError('unsupported custodian filesystem')
    return kind

def _principal(uid,gid):
    if sys.platform!='linux' or uid==0 or os.getuid()!=uid or os.geteuid()!=uid:
        raise OwnershipError('dedicated custodian required')
    fields=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if ([int(x) for x in fields['Uid'].split()]!=[uid]*4 or [int(x) for x in fields['Gid'].split()]!=[gid]*4
            or not set(os.getgroups()).issubset({gid}) or fields['Threads'].strip()!='1'
            or any(int(fields[k],16) for k in ('CapEff','CapPrm','CapInh','CapAmb'))):
        raise OwnershipError('single unprivileged custodian required')
    if ctypes.CDLL(None,use_errno=True).prctl(38,1,0,0,0)!=0:
        raise OwnershipError('no-new-privileges required')

def _validate_spec(host,raw,config_raw,config):
    value=host._json(raw)
    if (set(value)!={'format','state_file','state_identity','lease_file','lease_identity','custodian_uid','socket_gid','host_config_sha256'}
            or value['format']!='pult-custodian-ownership-v1' or _canonical(value)!=raw
            or value['host_config_sha256']!=hashlib.sha256(config_raw).hexdigest()
            or value['custodian_uid']!=config['custodian_uid'] or type(value['custodian_uid']) is not int
            or value['socket_gid']!=config['socket_gid'] or type(value['socket_gid']) is not int
            or value['state_file']!=config['state_file']):
        raise OwnershipError('ownership/config binding mismatch')
    state=host._path(value['state_file']);lease=host._path(value['lease_file'])
    if str(state)!=value['state_file'] or str(lease)!=value['lease_file'] or lease!=state.parent/'owner.lock':
        raise OwnershipError('fixed sibling ownership lock required')
    for name in ('state_identity','lease_identity'):
        pair=value[name]
        if (type(pair) is not dict or set(pair)!={'device','inode'} or
                any(type(x) is not int or x<0 for x in pair.values()) or pair['inode']==0):
            raise OwnershipError('explicit existing inode identity required')
    if not all(1000<=value[name]<2**31 for name in ('custodian_uid','socket_gid')):
        raise OwnershipError('dedicated non-system principal required')
    return value

class Lease:
    def __init__(self,host,spec):
        self.fd=None;self.pid=os.getpid();self.host=host;self.spec=spec
        uid,gid=spec['custodian_uid'],spec['socket_gid'];_principal(uid,gid)
        self._paths()
        path=Path(spec['lease_file'])
        fd=os.open(path,os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=uid or info.st_gid!=gid
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size!=0 or _identity(info)!=spec['lease_identity']):
                raise OwnershipError('existing private ownership inode required')
            _local_filesystem(fd)
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise OwnershipError('custodian already owned') from None
            self.fd=fd;self.check();_LIVE.add(self)
        except BaseException:
            self.fd=None;os.close(fd);raise

    def _paths(self):
        spec=self.spec;uid,gid=spec['custodian_uid'],spec['socket_gid']
        state=Path(spec['state_file']);lease=Path(spec['lease_file'])
        self.host._ancestors(state,{0,uid});self.host._ancestors(lease,{0,uid})
        directory=state.parent.lstat()
        if directory.st_uid!=uid or directory.st_gid!=gid or stat.S_IMODE(directory.st_mode)!=0o700:
            raise OwnershipError('private custodian directory required')
        st=state.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=uid or st.st_gid!=gid or stat.S_IMODE(st.st_mode)!=0o600
                or st.st_nlink!=1 or not 0<st.st_size<=2*1024*1024 or _identity(st)!=spec['state_identity']):
            raise OwnershipError('existing custodian state identity required')

    def check(self):
        if self.fd is None or os.getpid()!=self.pid: raise OwnershipError('ownership no longer held')
        _principal(self.spec['custodian_uid'],self.spec['socket_gid']);self._paths()
        current=Path(self.spec['lease_file']).lstat();held=os.fstat(self.fd)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink!=1 or current.st_uid!=self.spec['custodian_uid']
                or current.st_gid!=self.spec['socket_gid'] or stat.S_IMODE(current.st_mode)!=0o600 or current.st_size!=0
                or _identity(current)!=self.spec['lease_identity'] or _identity(held)!=self.spec['lease_identity']
                or held.st_dev!=Path(self.spec['state_file']).stat().st_dev):
            raise OwnershipError('ownership path or descriptor changed')

    def close(self):
        if self.fd is not None:
            fd=self.fd;self.fd=None
            # Never LOCK_UN: an inherited child FD must not unlock its parent.
            os.close(fd)
        _FAILED_OWNERS.discard(self)

def _after_fork():
    for lease in tuple(_LIVE): lease.close()

if hasattr(os,'register_at_fork'): os.register_at_fork(after_in_child=_after_fork)

def _guard_anchor(anchor,lease):
    from ops.backup.erasure_anchor import AnchorError
    original=anchor._connection
    @contextmanager
    def connection(*args,**kwargs):
        try: lease.check()
        except (OwnershipError,OSError): raise AnchorError('custodian ownership unavailable; no retry') from None
        with original(*args,**kwargs) as current: yield current
        try: lease.check()
        except (OwnershipError,OSError): raise AnchorError('custodian ownership response uncertain; no retry') from None
    anchor._connection=connection

class OwnedHost:
    def __init__(self,lease,server,reader):
        self._lease=lease;self._server=server;self._reader=reader;self._closed=False;self._close_failed=False
    def __repr__(self): return '<OwnedHost private custodian; no reset authority>'
    def serve(self): self._lease.check();self._server.serve()
    def close(self):
        if self._closed: return
        if self._close_failed: raise OwnershipError('cleanup uncertain; process exit required')
        try:
            self._reader.close();self._lease.check();self._lease.close()
        except BaseException:
            self._close_failed=True
            _FAILED_OWNERS.add(self._lease)
            # Deliberately retain ownership until this process exits.
            raise OwnershipError('cleanup uncertain; process exit required') from None
        self._closed=True

def prepare_host(guard_file,config_file,reader_credential_file,ownership_file,approved_sha256):
    """Trusted service entry supplies paths/SHA; no caller-controlled factory.

    Acquire existing exclusive lease BEFORE build_host/SQLite/credential load;
    hold through service and reader.close. Never create/unlink/reset any state.
    """
    lease=None;reader=None;build_started=False
    try:
        if type(approved_sha256) is not str or len(approved_sha256)!=64 or any(c not in '0123456789abcdef' for c in approved_sha256):
            raise OwnershipError('independent ownership approval required')
        code=_root_source(guard_file,65536)
        if hashlib.sha256(code).hexdigest()!=GUARD_SHA: raise OwnershipError('unapproved host guard')
        host=ModuleType('_pult_owned_host_guard');host.__file__=str(guard_file)
        exec(compile(code,str(guard_file),'exec'),host.__dict__)
        config_raw=host._read_file(config_file);config=host.parse_config(config_raw)
        raw=host._read_file(ownership_file)
        if hashlib.sha256(raw).hexdigest()!=approved_sha256: raise OwnershipError('ownership approval mismatch')
        spec=_validate_spec(host,raw,config_raw,config)
        lease=Lease(host,spec)
        core=Path(guard_file).parents[2]
        for name,digest in CORE_PINS.items():
            if hashlib.sha256(_root_source(core/name,600000)).hexdigest()!=digest:
                raise OwnershipError('closed core source profile changed')
        build_started=True
        server,reader=host.build_host(config_file,reader_credential_file,_config_snapshot=config_raw,
            _ownership_guard=lambda anchor:_guard_anchor(anchor,lease))
        anchor=server.custodian._anchor
        if (anchor._path!=Path(spec['state_file']) or anchor.scope!=config['scope']
                or anchor.generation!=config['generation']
                or anchor.location!=config['s3']['endpoint']+'/'+config['s3']['bucket']+'/'+config['s3']['key']):
            raise OwnershipError('built anchor differs from approved snapshot')
        lease.check()
        return OwnedHost(lease,server,reader)
    except BaseException as error:
        # build_host owns its internal reader until return. If it raises, this
        # caller cannot know whether that internal reader.close also failed.
        cleanup_ok=not build_started
        if reader is not None:
            try:
                reader.close();lease.check();cleanup_ok=True
            except BaseException: cleanup_ok=False
        if lease is not None:
            if cleanup_ok: lease.close()
            else: _FAILED_OWNERS.add(lease)
        if isinstance(error,(KeyboardInterrupt,SystemExit)): raise
        raise OwnershipError('owned custodian preparation refused') from None
