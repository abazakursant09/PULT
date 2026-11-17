"""Separate OFFLINE schema2 observer candidate. No install/write/recovery API.

Root, OS, interpreter and independently approved manifest are trust roots.
The caller selects nothing. A local observation is NOT remote freshness.
"""
import array
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import sys
import time
from types import ModuleType
from uuid import UUID

COLD_SHA='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'
CORE_SHA='961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7'
FORMAT='pult-offline-observer-profile-v2'
REQUEST='pult-offline-observer-request-v2'
REPLY='pult-offline-observer-reply-v2'
POLICY='PRESERVE_UNTIL_INDEPENDENT_REVIEW_NO_EXPIRY_V2'
BOUNDS={'max_receipts':1024,'max_state_bytes':2097152,'record_bytes':2048}
UNKNOWN=b'OBSERVER_V2_UNKNOWN\n'
FIELDS={'format','release_root','control_root','control_identity','lock_identity','control_epoch',
 'host_boot_id','principals','binding','cold_entry','legacy_core','cold_profile','cold_profile_sha256',
 'source_hashes','helper_config_sha256','owner','safety_policy','runtime_seconds','stop_seconds'}

class Refused(Exception): pass

def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')

def document(raw):
    def pairs(rows):
        value={}
        for key,item in rows:
            if key in value: raise Refused('duplicate field')
            value[key]=item
        return value
    value=json.loads(raw,object_pairs_hook=pairs)
    if type(value) is not dict or canonical(value)!=raw: raise Refused('canonical object required')
    return value

def digest(raw): return hashlib.sha256(raw).hexdigest()

def path(value):
    if type(value) is not str or '\0' in value: raise Refused('explicit path required')
    result=Path(value)
    if not result.is_absolute() or '..' in result.parts or str(result)!=value: raise Refused('normalized path required')
    return result

def runtime(root=False):
    if (sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12)
        or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.flags.optimize
        or Path(sys.executable).resolve()!=Path('/usr/bin/python3.12') or os.getuid()!=os.geteuid()
        or (root and os.getuid()!=0)):
        raise Refused('isolated nonoptimized fixed runtime required')

def ancestors(file,owners=(0,)):
    for parent in file.parents:
        info=parent.lstat();sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
            or (info.st_mode&0o022 and not sticky)
            or any(name.startswith('system.posix_acl_') for name in os.listxattr(parent,follow_symlinks=False))):
            raise Refused('controlled ancestor required')

def root_read(file,limit=16384,modes=(0o444,0o644,0o600)):
    ancestors(file)
    fd=os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1
            or stat.S_IMODE(before.st_mode) not in modes or not 0<=before.st_size<=limit
            or any(name.startswith('system.posix_acl_') for name in os.listxattr(fd))):
            raise Refused('bounded trusted file required')
        data=bytearray()
        while len(data)<=limit:
            chunk=os.read(fd,min(8192,limit+1-len(data)))
            if not chunk: break
            data.extend(chunk)
        after=os.fstat(fd)
        if len(data)!=before.st_size or any(getattr(before,k)!=getattr(after,k) for k in
                ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):
            raise Refused('file intake changed')
        return bytes(data)
    finally: os.close(fd)

def identity(file):
    info=file.lstat();return {'device':info.st_dev,'inode':info.st_ino}

def check_inode(file,wanted,uid,mode,kind=stat.S_ISREG,gid=None):
    if (type(wanted) is not dict or set(wanted)!={'device','inode'}
        or any(type(x) is not int or x<0 for x in wanted.values()) or wanted['inode']==0):
        raise Refused('explicit inode required')
    ancestors(file,(0,uid));info=file.lstat()
    if (not kind(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=mode
        or (gid is not None and info.st_gid!=gid) or (kind==stat.S_ISREG and info.st_nlink!=1)
        or identity(file)!=wanted
        or any(name.startswith('system.posix_acl_') for name in os.listxattr(file,follow_symlinks=False))):
        raise Refused('pinned private inode changed')

def uuid4(value):
    parsed=UUID(value) if type(value) is str else None
    if parsed is None or parsed.version!=4 or str(parsed)!=value: raise Refused('UUID4 required')

def hash64(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value): raise Refused('digest required')

def binding(value):
    if type(value) is not dict or set(value)!={'scope','generation','location'}: raise Refused('binding required')
    scope=value['scope'];generation=value['generation']
    if type(scope) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,64}',scope): raise Refused('scope required')
    uuid4(generation)
    if type(value['location']) is not str or not re.fullmatch(
        'https://s3\\.ru-3\\.storage\\.selcloud\\.ru/[a-z0-9][a-z0-9-]{1,61}[a-z0-9]/pult/erasure-v2/'+
        re.escape(scope)+'/'+generation+'/events/',value['location']): raise Refused('closed schema2 location required')

def request(spec):
    return canonical(dict(format=REQUEST,op='observe_local',**spec['binding']))

def response(body,observation,cold_profile_sha256):
    wanted=document(body)
    if set(wanted)!={'format','op','scope','generation','location'} or wanted['format']!=REQUEST or wanted['op']!='observe_local':
        raise Refused('observer request required')
    binding({k:wanted[k] for k in ('scope','generation','location')})
    hash64(cold_profile_sha256)
    value=dict(wanted,format=REPLY,request_sha256=digest(body),observation=observation,cold_profile_sha256=cold_profile_sha256)
    raw=canonical(value)
    if not valid_reply(body,raw,allow_pending=True): raise Refused('typed observation required')
    return raw

def valid_reply(body,raw,allow_pending=False,expected_profile_sha=None):
    try:
        if not 0<len(raw)<=2048: return False
        value=document(raw);wanted=document(body)
        if (wanted['format']!=REQUEST or wanted['op']!='observe_local'
            or set(wanted)!={'format','op','scope','generation','location'}): return False
        binding({k:wanted[k] for k in ('scope','generation','location')})
        observation=value['observation'];head=observation['head']
        if (type(observation) is not dict or set(observation)!={'status','head','revision','write_admitted','purge_or_restore_admitted'}
            or type(head) is not dict or set(head)!={'sequence','digest'}
            or type(head['sequence']) is not int or not 0<=head['sequence']<=1024
            or type(observation['revision']) is not int or not 0<=observation['revision']<=3072
            or observation['write_admitted'] is not False or observation['purge_or_restore_admitted'] is not False
            or observation['status'] not in (('OBSERVED_LOCAL_ONLY','PENDING_REQUIRES_REVIEW') if allow_pending else ('OBSERVED_LOCAL_ONLY',))):
            return False
        hash64(head['digest'])
        hash64(value['cold_profile_sha256'])
        if expected_profile_sha is not None and value['cold_profile_sha256']!=expected_profile_sha: return False
        # Prepared/dispatched pending metadata is diagnostic only, never success.
        if observation['status']=='OBSERVED_LOCAL_ONLY' and observation['revision']!=3*head['sequence']: return False
        if observation['status']=='PENDING_REQUIRES_REVIEW' and (head['sequence']>=1024 or
                observation['revision'] not in (3*head['sequence']+1,3*head['sequence']+2)): return False
        return value==dict(wanted,format=REPLY,request_sha256=digest(body),observation=observation,
            cold_profile_sha256=value['cold_profile_sha256'])
    except (Refused,ValueError,TypeError,KeyError,UnicodeError,RecursionError): return False

def process_start(pid,uid,gid):
    if type(pid) is not int or pid<=0: raise Refused('process required')
    fields=dict(line.split(':',1) for line in Path('/proc',str(pid),'status').read_text().splitlines() if ':' in line)
    if ([int(x) for x in fields['Uid'].split()]!=[uid]*4 or [int(x) for x in fields['Gid'].split()]!=[gid]*4
        or any(int(fields[k],16) for k in ('CapEff','CapPrm','CapInh','CapAmb'))
        or not set(map(int,fields['Groups'].split())).issubset({gid})):
        raise Refused('unprivileged process identity changed')
    # comm may contain spaces or parentheses: fields after its LAST closing ')'.
    return int(Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19])

def owner_check(owner):
    if (type(owner) is not dict or set(owner)!={'pid','start','uid','gid','socket','socket_identity','socket_gid'}
        or any(type(owner[k]) is not int or owner[k]<=0 for k in ('pid','start','uid','gid','socket_gid'))):
        raise Refused('owner identity required')
    if process_start(owner['pid'],owner['uid'],owner['gid'])!=owner['start']: raise Refused('owner start changed')
    sock=path(owner['socket'])
    check_inode(sock,owner['socket_identity'],owner['uid'],0o660,stat.S_ISSOCK,owner['socket_gid'])
    info=sock.parent.lstat()
    if info.st_uid!=owner['uid'] or info.st_gid!=owner['socket_gid'] or stat.S_IMODE(info.st_mode)!=0o750:
        raise Refused('private owner socket parent required')

def helper_config(spec):
    return canonical(dict(format='pult-offline-observer-helper-v2',owner=spec['owner'],
        helper_uid=spec['principals']['helper_uid'],helper_gid=spec['principals']['helper_gid'],
        request=document(request(spec)),host_boot_id=spec['host_boot_id'],cold_profile_sha256=spec['cold_profile_sha256']))

def control_identity(spec):
    # Full static manifest binding except the already inode-bound control paths.
    return canonical(dict(format='pult-observer-control-identity-v2',profile_sha256=digest(canonical(spec))))

class Prepared:
    def __repr__(self): return '<OfflineObserverV2 fixed local observation only>'
    def argv(self):
        return ('/usr/bin/python3.12','-I','-S','-B',str(self.root/'observer_v2.py'),
            '--helper',str(self.root/'helper.json'),'--sha256',self.spec['helper_config_sha256'])
    def accept_reply(self,body,raw):
        return raw if body==self.expected_request and valid_reply(body,raw,
            expected_profile_sha=self.spec['cold_profile_sha256']) else UNKNOWN
    def validate(self):
        runtime(root=True);spec=self.spec
        if (digest(canonical(spec))!=self.profile_sha or self.root!=path(spec['release_root'])
            or self.control!=path(spec['control_root']) or self.expected_request!=request(spec)
            or self.epoch!=spec['control_epoch'] or self.boot!=spec['host_boot_id']
            or self.caller_uid!=spec['principals']['caller_uid'] or self.caller_gid!=spec['principals']['caller_gid']
            or self.writer_uid!=spec['principals']['helper_uid'] or self.writer_gid!=spec['principals']['helper_gid']):
            raise Refused('in-memory approved profile changed')
        if digest(root_read(self.manifest))!=self.profile_sha: raise Refused('independent approval changed')
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=self.boot: raise Refused('new boot requires review')
        check_inode(self.control,spec['control_identity'],0,0o700,stat.S_ISDIR)
        check_inode(self.control/'observer-v2.lock',spec['lock_identity'],0,0o600)
        if root_read(self.control/'observer-v2.lock',1)!=b'': raise Refused('lock changed')
        if root_read(self.control/'observer-v2.identity',4096)!=control_identity(spec): raise Refused('control identity changed')
        allowed={'observer-v2.lock','observer-v2.identity','observer-v2.claim'}
        if not {p.name for p in self.control.iterdir()}.issubset(allowed): raise Refused('foreign control state; preserve all')
        if set(p.name for p in self.root.iterdir())!={'observer_v2.py','supervisor_v2.py','helper.json'}:
            raise Refused('closed helper release required')
        for name,sha in spec['source_hashes'].items():
            if digest(root_read(self.root/name,100000))!=sha: raise Refused('helper source changed')
        if root_read(self.root/'helper.json')!=helper_config(spec): raise Refused('helper binding changed')
        if digest(root_read(path(spec['legacy_core']),100000))!=CORE_SHA: raise Refused('legacy core changed')
        if digest(root_read(path(spec['cold_entry']),100000))!=COLD_SHA: raise Refused('cold entry changed')
        raw=root_read(path(spec['cold_profile']))
        if digest(raw)!=spec['cold_profile_sha256']: raise Refused('cold profile changed')
        cold=document(raw);ids=spec['principals'];owner=spec['owner'];b=spec['binding']
        if (cold['format']!='pult-schema2-cold-host-profile-v1' or cold['mode']!='OFFLINE_CURRENT_ONLY'
            or cold['scope']!=b['scope'] or cold['generation']!=b['generation'] or cold['bounds']!=BOUNDS
            or cold['custodian_uid']!=ids['custodian_uid'] or cold['socket_gid']!=ids['custodian_gid']
            or owner['uid']!=ids['custodian_uid'] or owner['gid']!=ids['custodian_gid']
            or owner['socket_gid']!=ids['helper_gid']
            or b['location']!=cold['s3']['endpoint']+'/'+cold['s3']['bucket']+'/pult/erasure-v2/'+b['scope']+'/'+b['generation']+'/events/'):
            raise Refused('cold binding mismatch')
        for field,key,mode in (('state_file','state_identity',0o600),('lease_file','lease_identity',0o600),
                ('reader_credential_file','reader_credential_identity',0o400)):
            check_inode(path(cold[field]),cold[key],ids['custodian_uid'],mode)
        # Intake checks complete pinned13-source/7-wheel/extracted-dependency tree;
        # returns captured bytes only. NO payload activate, SQL, reader or network.
        self.cold._intake(path(cold['release_root']))
        owner_check(owner)

def prepare(manifest,approved_sha):
    runtime(root=True);hash64(approved_sha);manifest=path(str(manifest))
    raw=root_read(manifest);spec=document(raw)
    if digest(raw)!=approved_sha: raise Refused('independent exact approval required')
    if (set(spec)!=FIELDS or spec['format']!=FORMAT or spec['safety_policy']!=POLICY
        or type(spec['runtime_seconds']) is not int or spec['runtime_seconds']!=40
        or type(spec['stop_seconds']) is not int or spec['stop_seconds']!=2): raise Refused('fixed v2 policy required')
    binding(spec['binding']);uuid4(spec['control_epoch']);uuid4(spec['host_boot_id'])
    ids=spec['principals'];names={'caller_uid','caller_gid','helper_uid','helper_gid','custodian_uid','custodian_gid'}
    if (type(ids) is not dict or set(ids)!=names or any(type(x) is not int or not 1000<=x<2**31 for x in ids.values())
        or len({ids[k] for k in ('caller_uid','helper_uid','custodian_uid')})!=3
        or ids['caller_gid']==ids['helper_gid']): raise Refused('separate principals required')
    hashes=spec['source_hashes']
    if type(hashes) is not dict or set(hashes)!={'observer_v2.py','supervisor_v2.py'}: raise Refused('exact source closure required')
    for value in (*hashes.values(),spec['cold_profile_sha256'],spec['helper_config_sha256']): hash64(value)
    if digest(helper_config(spec))!=spec['helper_config_sha256']: raise Refused('config approval mismatch')
    root=path(spec['release_root']);control=path(spec['control_root'])
    if (root==control or root.is_relative_to(control) or control.is_relative_to(root)
        or manifest.is_relative_to(root) or manifest.is_relative_to(control)): raise Refused('separate roots required')
    for name in ('cold_entry','legacy_core','cold_profile'):
        target=path(spec[name])
        if target.is_relative_to(root) or target.is_relative_to(control): raise Refused('separate cold/control sources required')
    if path(spec['owner']['socket']).is_relative_to(root) or path(spec['owner']['socket']).is_relative_to(control):
        raise Refused('separate custodian IPC required')
    source=root_read(path(spec['cold_entry']),100000)
    if digest(source)!=COLD_SHA: raise Refused('cold trust root refused')
    cold=ModuleType('_v2_observer_cold_intake');exec(compile(source,spec['cold_entry'],'exec'),cold.__dict__)
    prepared=Prepared();prepared.spec=spec;prepared.root=root;prepared.control=control;prepared.manifest=manifest
    prepared.profile_sha=approved_sha;prepared.epoch=spec['control_epoch'];prepared.boot=spec['host_boot_id'];prepared.cold=cold
    prepared.caller_uid=ids['caller_uid'];prepared.caller_gid=ids['caller_gid']
    prepared.writer_uid=ids['helper_uid'];prepared.writer_gid=ids['helper_gid']
    prepared.expected_request=request(spec);prepared.validate();return prepared

def receive(connection,peer,expected,limit=4096):
    deadline=time.monotonic()+2;body=bytearray()
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0: raise Refused('IPC deadline')
        connection.settimeout(remaining)
        packet,items,flags,_=connection.recvmsg(limit+1-len(body),1024,socket.MSG_CMSG_CLOEXEC)
        creds=[];wrong=False
        for level,kind,value in items:
            if level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS and len(value)==12:
                creds.append(struct.unpack('3i',value))
            else:
                wrong=True
                if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                    fds=array.array('i');fds.frombytes(value[:len(value)//4*4])
                    for fd in fds: os.close(fd)
        if wrong or flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC): raise Refused('IPC ancillary refused')
        if not packet: break
        if creds!=[peer]: raise Refused('IPC sender changed')
        body.extend(packet)
        if len(body)>limit: raise Refused('IPC bound')
    if expected is not None and bytes(body)!=expected: raise Refused('fixed IPC only')
    return bytes(body)

def peer_pin(connection,uid,gid,pid=None,start=None):
    peer=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    fd=connection.getsockopt(socket.SOL_SOCKET,77)
    try:
        os.set_inheritable(fd,False)
        if peer[0]<=0 or peer[1:]!=(uid,gid) or (pid is not None and peer[0]!=pid): raise Refused('IPC peer refused')
        poller=select.poll();poller.register(fd,select.POLLIN)
        if poller.poll(0): raise Refused('IPC peer exited')
        if start is not None and process_start(peer[0],uid,gid)!=start: raise Refused('IPC process changed')
        return peer,fd
    except BaseException: os.close(fd);raise

def serve_once(ready,connection,body,helper_uid,helper_gid):
    """Called by a trusted already prepared cold owner; never prepares a DB."""
    peer,fd=peer_pin(connection,helper_uid,helper_gid)
    try:
        receive(connection,peer,body)
        # Bind the ACTUAL prepared cold handle, never relabel its head with an
        # unrelated trusted-wire document. _check also rechecks approved bytes.
        ready._check();cold=document(ready._raw);wanted=document(body)
        location=cold['s3']['endpoint']+'/'+cold['s3']['bucket']+'/pult/erasure-v2/'+cold['scope']+'/'+cold['generation']+'/events/'
        if (wanted!=dict(format=REQUEST,op='observe_local',scope=cold['scope'],generation=cold['generation'],location=location)
            or cold['custodian_uid']!=os.getuid() or cold['socket_gid']!=os.getgid()
            or any(ready._owner.spec[k]!=cold[k] for k in ('state_file','state_identity','lease_file','lease_identity','custodian_uid','socket_gid'))):
            raise Refused('actual cold handle binding mismatch')
        observation=ready.observe();answer=response(body,observation,digest(ready._raw))
        poller=select.poll();poller.register(fd,select.POLLIN)
        if poller.poll(0): raise Refused('helper exited')
        connection.sendall(answer);connection.shutdown(socket.SHUT_WR)
        return observation['status']=='OBSERVED_LOCAL_ONLY'
    finally: os.close(fd)

def helper(config_file,approved_sha):
    runtime();hash64(approved_sha);raw=root_read(path(config_file))
    if digest(raw)!=approved_sha: raise Refused('fixed helper config refused')
    config=document(raw)
    if set(config)!={'format','owner','helper_uid','helper_gid','request','host_boot_id','cold_profile_sha256'} or config['format']!='pult-offline-observer-helper-v2':
        raise Refused('helper config required')
    uid=config['helper_uid'];gid=config['helper_gid']
    if os.getuid()!=uid or os.getgid()!=gid or not set(os.getgroups()).issubset({gid}): raise Refused('helper principal refused')
    process_start(os.getpid(),uid,gid)
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=config['host_boot_id']: raise Refused('helper boot changed')
    expected=canonical(config['request'])
    if sys.stdin.buffer.read(4097)!=expected: raise Refused('fixed stdin required')
    owner=config['owner'];owner_check(owner)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1);connection.settimeout(2)
        connection.connect(owner['socket'])
        peer,fd=peer_pin(connection,owner['uid'],owner['gid'],owner['pid'],owner['start'])
        try:
            owner_check(owner)
            connection.sendall(expected);connection.shutdown(socket.SHUT_WR)
            raw=receive(connection,peer,None,2048)
            if not valid_reply(expected,raw,allow_pending=True,expected_profile_sha=config['cold_profile_sha256']): raise Refused('typed reply refused')
        finally: os.close(fd)
    sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()

if __name__=='__main__':
    try:
        if len(sys.argv)!=5 or sys.argv[1]!='--helper' or sys.argv[3]!='--sha256': raise Refused('fixed entry required')
        helper(sys.argv[2],sys.argv[4])
    except BaseException: raise SystemExit(70)
