"""Private current-only root supervisor candidate. NOT a deployed privilege API.

Root, OS, manager and this code/profile are trusted. The only untrusted input
is one connected Unix stream. No paths, commands, options or binding are taken
from its sender. Deployment/listener ownership is a separate gate.
"""
import array
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import selectors
import socket
import stat
import struct
import subprocess
import time
import uuid

MAX_REQUEST=4096
MAX_REPLY=2048
IO_SECONDS=2.0
RUNTIME_SECONDS=40
STOP_SECONDS=2
PREFIX='pult-call-gate-'
UNKNOWN=b'CALL_UNKNOWN\n'
REFUSED=b'CALL_REFUSED\n'

class Refused(Exception): pass
class Uncertain(Exception): pass

class Caller:
    def __init__(self,connection):
        self.connection=connection
        # Linux UAPI SO_PEERPIDFD=77: get a pidfd for the socket's pinned peer,
        # not pidfd_open(numeric_pid) after a PID-reuse window. No fallback.
        self.fd=connection.getsockopt(socket.SOL_SOCKET,77)
        os.set_inheritable(self.fd,False)
    def check(self):
        poller=select.poll();poller.register(self.fd,select.POLLIN)
        if poller.poll(0): raise Uncertain('caller exited')
        poller=select.poll();poller.register(self.connection,select.POLLHUP|select.POLLERR)
        if poller.poll(0): raise Uncertain('caller disconnected')
    def close(self): os.close(self.fd)

def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')

def _root_ancestors(path):
    if not path.is_absolute() or '..' in path.parts: raise Refused('trusted path required')
    for parent in path.parents:
        info=parent.lstat()
        sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or (info.st_mode&0o022 and not sticky):
            raise Refused('trusted ancestor required')

def root_read(path,limit):
    _root_ancestors(path)
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1 or before.st_mode&0o022 or before.st_size>limit:
            raise Refused('trusted regular file required')
        data=bytearray()
        while len(data)<=limit:
            part=os.read(fd,min(65536,limit+1-len(data)))
            if not part: break
            data.extend(part)
        after=os.fstat(fd)
        if len(data)>limit or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise Refused('bounded stable file required')
        return bytes(data)
    finally: os.close(fd)

CLAIM_NAME='operation.claim'

def _sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)

def _claim_record(profile,body):
    path=profile.control/CLAIM_NAME
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
        raise Uncertain('private claim required')
    raw=root_read(path,2048);value=json.loads(raw)
    if (type(value) is not dict or set(value)!={'format','operation_id','request_sha256','unit'} or
            value['format']!='pult-current-operation-claim-v1' or canonical(value)!=raw or
            type(value['operation_id']) is not str or not re.fullmatch('[0-9a-f]{32}',value['operation_id']) or
            value['unit']!=PREFIX+value['operation_id']+'.service' or
            value['request_sha256']!=hashlib.sha256(body).hexdigest()):
        raise Uncertain('exact operation claim required')
    return value

class Claim:
    """Root-private process-crash fence, not an automatic recovery protocol.

    Existing/partial/unknown claims are NEVER cleared by PID, time or unit state.
    Holding flock alone is insufficient: this path outlives its controller.
    """
    @classmethod
    def begin(cls,profile,body):
        claim=cls();claim.path=profile.control/CLAIM_NAME;claim.fd=None
        token=uuid.uuid4().hex;claim.unit=PREFIX+token+'.service'
        claim.body=canonical({'format':'pult-current-operation-claim-v1','operation_id':token,
            'request_sha256':hashlib.sha256(body).hexdigest(),'unit':claim.unit})
        try:
            claim.fd=os.open(claim.path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        except FileExistsError: raise Refused('unresolved operation exists') from None
        try:
            os.fchmod(claim.fd,0o600)
            offset=0
            while offset<len(claim.body):
                count=os.write(claim.fd,claim.body[offset:])
                if count<=0: raise Uncertain('claim write incomplete')
                offset+=count
            os.fsync(claim.fd);_sync_directory(profile.control)
            info=os.fstat(claim.fd);claim.identity=(info.st_dev,info.st_ino)
            return claim
        except BaseException:
            claim.close()  # leave any partial marker; never launch/fall back
            raise

    def finish(self,profile,metadata):
        if (metadata.get('unit')!=self.unit or metadata.get('cleanup_confirmed') is not True or
                metadata.get('cgroup_empty') is not True or not _empty(self.unit) or
                _state(self.unit).get('ActiveState') not in ('inactive','failed')):
            raise Uncertain('operation completion not confirmed')
        descriptor=os.fstat(self.fd);current=self.path.lstat()
        if (not stat.S_ISREG(current.st_mode) or current.st_uid!=0 or current.st_nlink!=1 or
                stat.S_IMODE(current.st_mode)!=0o600 or (current.st_dev,current.st_ino)!=self.identity or
                (descriptor.st_dev,descriptor.st_ino)!=self.identity or descriptor.st_nlink!=1 or
                os.pread(self.fd,len(self.body)+1,0)!=self.body or root_read(self.path,2048)!=self.body):
            raise Uncertain('operation claim ownership changed')
        # Directory is root-private; concurrent legitimate calls hold the same
        # flock. Root/OS tampering is outside this trust model, never anti-root.
        os.unlink(self.path);_sync_directory(profile.control)

    def close(self):
        if self.fd is not None:
            fd=self.fd;self.fd=None;os.close(fd)

@dataclass(frozen=True)
class Profile:
    # Constructed only by trusted local wiring, never from socket bytes or env.
    root: Path
    control: Path
    caller_uid: int
    caller_gid: int
    writer_uid: int
    writer_gid: int
    source_hashes: tuple
    expected_request: bytes

    def validate(self):
        if os.geteuid()!=0: raise Refused('trusted supervisor required')
        for value in (self.caller_uid,self.caller_gid,self.writer_uid,self.writer_gid):
            if type(value) is not int or not 1000<=value<2**31: raise Refused('explicit principal required')
        if self.caller_uid==self.writer_uid: raise Refused('separate principals required')
        for directory in (self.root,self.control):
            _root_ancestors(directory/'entry')
            info=directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                raise Refused('trusted directory required')
        if stat.S_IMODE(self.control.stat().st_mode)!=0o700: raise Refused('private control required')
        for name,digest in self.source_hashes:
            if name.startswith('/') or '..' in Path(name).parts or not name.startswith('payload/'):
                raise Refused('invalid trusted source profile')
            if hashlib.sha256(root_read(self.root/name,600000)).hexdigest()!=digest:
                raise Refused('source hash mismatch')
        # Binding is a fixed trusted current-only document, not a general parser.
        request=json.loads(self.expected_request)
        if set(request)!={'format','scope','generation','location','op'} or request['format']!='pult-erasure-remote-command-prototype-v1' or request['op']!='current' or canonical(request)!=self.expected_request:
            raise Refused('current-only profile required')
        root_read(self.root/'config/writer.json',8192)
        interpreter=self.root/'payload/core/venv/bin/python'
        _root_ancestors(interpreter)
        target=interpreter.resolve(strict=True)
        if target!=Path('/usr/bin/python3.12') or target.stat().st_uid!=0 or target.stat().st_mode&0o022:
            raise Refused('trusted fixed interpreter required')
        # Never read/print a writer credential in root: the helper owns its guard.
        credential=self.root/'secrets/writer.json'
        _root_ancestors(credential)
        info=credential.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=self.writer_uid or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o400:
            raise Refused('separate private writer credential required')

    def argv(self):
        return (str(self.root/'payload/core/venv/bin/python'),'-I','-S','-B',
            str(self.root/'payload/launcher.py'),'--role','writer','--config',
            str(self.root/'config/writer.json'),'--credential-file',
            str(self.root/'secrets/writer.json'),'--once')

def receive(connection,profile):
    if connection.family!=socket.AF_UNIX or connection.getsockopt(socket.SOL_SOCKET,socket.SO_TYPE)!=socket.SOCK_STREAM:
        raise Refused('Unix stream required')
    if connection.getsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED)!=1:
        raise Refused('sender credentials required before receive')
    peer=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    if peer[0]<=0 or peer[1:]!=(profile.caller_uid,profile.caller_gid):
        raise Refused('caller refused')
    caller=Caller(connection)
    try:
        body=_receive_body(connection,profile,peer,caller)
        caller.check()
        return body,caller
    except BaseException:
        caller.close();raise

def _receive_body(connection,profile,peer,caller):
    deadline=time.monotonic()+IO_SECONDS
    body=bytearray()
    while True:
        caller.check()
        left=deadline-time.monotonic()
        if left<=0: raise Refused('input deadline')
        connection.settimeout(left)
        packet,ancillary,flags,_=connection.recvmsg(MAX_REQUEST+1-len(body),1024,socket.MSG_CMSG_CLOEXEC)
        credentials=[]; wrong=False
        for level,kind,value in ancillary:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                # Reject AND close delivered FDs, including a truncated set.
                descriptors=array.array('i'); descriptors.frombytes(value[:len(value)//descriptors.itemsize*descriptors.itemsize])
                for fd in descriptors: os.close(fd)
                wrong=True
            elif level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS and len(value)==12:
                credentials.append(struct.unpack('3i',value))
            else: wrong=True
        if wrong or flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC): raise Refused('ancillary refused')
        if not packet:
            # EOF may include synthetic (0,0,0) credentials; it carries no input.
            break
        if credentials!=[peer]: raise Refused('sender changed')
        body.extend(packet)
        if len(body)>MAX_REQUEST: raise Refused('input bound')
    if bytes(body)!=profile.expected_request: raise Refused('fixed observation only')
    return bytes(body)

def _cmd(argv,timeout=5,check=True):
    result=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},
                          timeout=timeout,check=False)
    if check and result.returncode: raise Uncertain('manager command refused')
    return result

def _state(unit):
    fields='LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,MainPID,ControlGroup,RuntimeMaxUSec,TimeoutStopUSec,ExitType,KillMode,KillSignal,FinalKillSignal,SendSIGKILL,Restart,NRestarts,User,Group,NoNewPrivileges,ProtectControlGroups,PrivateNetwork,CapabilityBoundingSet'
    output=_cmd(('/usr/bin/systemctl','show',unit,'--no-pager','--property='+fields)).stdout
    return dict(line.split('=',1) for line in output.decode().splitlines() if '=' in line)

def _empty(unit):
    if not re.fullmatch(PREFIX+r'[0-9a-f]{32}\.service',unit): raise Uncertain('unit identity')
    group=Path('/sys/fs/cgroup/system.slice')/unit
    try:
        return 'populated 0' in (group/'cgroup.events').read_text().splitlines()
    except FileNotFoundError:
        # PID1 may remove an empty cgroup between existence and events reads.
        # Only confirmed absence is empty; a present group with missing state
        # is uncertainty, never an inferred successful cleanup.
        if not group.exists(): return True
        raise Uncertain('cgroup state unavailable') from None

class Manager:
    """Only trusted code may construct/subclass. No socket-controlled command API."""
    runtime=RUNTIME_SECONDS
    stop=STOP_SECONDS

    def started(self,unit,initial):
        pass  # Trusted local proof may fault-inject here; not a request option.

    def cleanup(self,unit):
        _cmd(('/usr/bin/systemctl','stop',unit),timeout=self.stop+5,check=False)
        after=_state(unit)
        if after.get('ActiveState') not in ('inactive','failed') or not _empty(unit):
            _cmd(('/usr/bin/systemctl','kill','--kill-whom=all','--signal=SIGKILL',unit),check=False)
            _cmd(('/usr/bin/systemctl','stop',unit),timeout=self.stop+5,check=False)
            after=_state(unit)
        if after.get('ActiveState') not in ('inactive','failed') or not _empty(unit):
            raise Uncertain('cleanup not confirmed')
        _cmd(('/usr/bin/systemctl','reset-failed',unit),check=False)
        after=_state(unit)
        if after.get('ActiveState') not in ('inactive','failed') or not _empty(unit):
            raise Uncertain('cleanup not confirmed')

    def run(self,profile,body,caller):
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
            'Environment':'PATH=/usr/bin:/bin LANG=C.UTF-8 SSH_ORIGINAL_COMMAND=pult-erasure-storage-v1'}
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
            try:
                if created: self.cleanup(unit); clean=True
                if process is not None:
                    limit=time.monotonic()+2
                    while selector.get_map():
                        if time.monotonic()>limit: raise Uncertain('pipe EOF deadline')
                        pump()
                    process.wait(timeout=max(.01,limit-time.monotonic()))
            finally:
                selector.close()
                if process is not None:
                    for name in ('stdin','stdout','stderr'):
                        stream=getattr(process,name)
                        if not stream.closed: stream.close()
                    if process.poll() is None: process.kill();process.wait(timeout=2)
        # No candidate bytes are exposed until cleanup has succeeded.
        caller.check()
        stdout=bytes(buffers['stdout']);stderr=bytes(buffers['stderr'])
        metadata={'unit':unit,'manager':final,'cleanup_confirmed':clean,'cgroup_empty':_empty(unit),
                  'elapsed_seconds':round(time.monotonic()-started,3),
                  'pipe_eof_confirmed':True,'supervisor_child_exit':process.returncode,
                  'stdout_bytes':len(stdout),'stderr_bytes':len(stderr)}
        good=final.get('Result')=='success' and final.get('ExecMainCode')=='1' and final.get('ExecMainStatus')=='0' and final.get('SubState')=='exited'
        if not good or stderr or offset!=len(body) or process.returncode!=0: return UNKNOWN,metadata
        return accept_reply(profile,body,stdout),metadata

def accept_reply(profile,body,stdout):
    try:
        if not 0<len(stdout)<=MAX_REPLY: return UNKNOWN
        value=json.loads(stdout)
        request=json.loads(profile.expected_request)
        expected=dict(request,request_sha256=hashlib.sha256(body).hexdigest(),status='OBSERVED_ONLY',head=value['head'])
        head=value['head']
        if canonical(value)!=stdout or value!=expected or type(head) is not dict or set(head)!={'sequence','digest'} or type(head['sequence']) is not int or not 0<=head['sequence']<=128 or type(head['digest']) is not str or not re.fullmatch('[0-9a-f]{64}',head['digest']):
            return UNKNOWN
        return stdout
    except (ValueError,TypeError,KeyError,RecursionError): return UNKNOWN

def call(connection,profile,manager=None):
    """Single authenticated current observation, no public root-launch entry."""
    manager=manager or Manager()
    lock=None; caller=None; claim=None; metadata={'launched':False}
    try:
        try:
            # Peer/input checks precede file validation, lock, unit and credentials.
            body,caller=receive(connection,profile)
            profile.validate()
            caller.check()
            lock=os.open(profile.control/'admission.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            info=os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
                raise Refused('private lock required')
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Refused('busy') from None
            caller.check()
            claim=Claim.begin(profile,body)
            caller.check()
            metadata['launched']=True
            answer,evidence=manager.run(profile,body,caller); metadata.update(evidence)
            caller.check()
            if accept_reply(profile,body,answer)==UNKNOWN:
                raise Uncertain('valid observation required before claim release')
            claim.finish(profile,metadata)
        except (Refused,OSError,ValueError,TimeoutError): answer=REFUSED if not metadata['launched'] else UNKNOWN
        except Exception: answer=UNKNOWN
        try:
            connection.settimeout(IO_SECONDS)
            connection.sendall(answer)
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            answer=UNKNOWN  # Never retry operation or delivery after disconnect.
        return answer,metadata
    finally:
        if claim is not None: claim.close()
        if lock is not None: os.close(lock)
        if caller is not None: caller.close()
