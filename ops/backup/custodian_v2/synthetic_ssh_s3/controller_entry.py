"""Root/OS one-shot bootstrap, never an SSH command or request parser.

The OS/root launcher supplies the nonsecret launch document and independent
digest. This process binds it to its own actual systemd invocation, publishes
one runtime profile/ingress config, then accepts one fixed nonroot trigger.
Neither SSH nor its caller chooses an event, profile, source, key or command.
No auto-restart/adoption/reset: existing runtime files or permanent fence fail.
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
import threading
import time
from types import ModuleType

FORMAT='pult-synthetic-one-shot-launch-v1'
MAX_LAUNCH=49152

class Refused(Exception):pass

def need(ok):
    if not ok:raise Refused('SYNTHETIC_ROOT_LAUNCH_REFUSED')

def canonical(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def stable(info):
    return tuple(getattr(info,k) for k in ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))

def normalized(value):
    need(type(value) is str and len(value)<=256 and re.fullmatch('/[A-Za-z0-9_./-]+',value) is not None)
    need(all(p not in ('','.','..') for p in value.split('/')[1:]));return Path(value)

def root_bytes(file,maximum):
    file=normalized(str(file))
    for parent in file.parents:
        info=parent.lstat();sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and (not info.st_mode&0o022 or sticky)
            and not any(n.startswith('system.posix_acl_') for n in os.listxattr(parent,follow_symlinks=False)))
    fd=os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
            and stat.S_IMODE(before.st_mode) in (0o444,0o644) and 0<before.st_size<=maximum
            and not any(n.startswith('system.posix_acl_') for n in os.listxattr(fd)))
        raw=os.pread(fd,maximum+1,0)
        need(len(raw)==before.st_size and stable(os.fstat(fd))==stable(before) and stable(file.lstat())==stable(before))
        return raw
    finally:os.close(fd)

def document(raw):
    def pairs(rows):
        out={}
        for k,v in rows:need(k not in out);out[k]=v
        return out
    need(type(raw) is bytes and 0<len(raw)<=MAX_LAUNCH)
    try:
        value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
        need(type(value) is dict and canonical(value)==raw)
        need(set(value)=={'format','entry_sha256','runtime_profile','controller_unit','action','ingress'})
        need(value['format']==FORMAT and re.fullmatch('[0-9a-f]{64}',value['entry_sha256']) is not None)
        normalized(value['runtime_profile'])
        need(type(value['controller_unit']) is str and re.fullmatch('pult-[a-z0-9-]{1,100}\\.service',value['controller_unit']) is not None)
        action=value['action'];need(type(action) is dict and action.get('controller','ABSENT') is None)
        ingress=value['ingress']
        need(type(ingress) is dict and set(ingress)=={'uid','gid','directory','socket_file','config_file','source'})
        need(all(type(ingress[k]) is int and 1000<=ingress[k]<2**31 for k in ('uid','gid')))
        for k in ('directory','socket_file','config_file'):normalized(ingress[k])
        directory=Path(ingress['directory'])
        need(Path(ingress['socket_file']).parent==directory and Path(ingress['config_file']).parent==directory
            and ingress['socket_file']!=ingress['config_file'] and len(os.fsencode(ingress['socket_file']))<=107)
        need(type(ingress['source']) is dict and set(ingress['source'])=={'path','sha256'})
        normalized(ingress['source']['path']);need(re.fullmatch('[0-9a-f]{64}',ingress['source']['sha256']) is not None)
        return value
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):raise Refused('SYNTHETIC_ROOT_LAUNCH_REFUSED') from None

def capture(row,name):
    need(name not in sys.modules)
    raw=root_bytes(row['path'],100000);need(sha(raw)==row['sha256'])
    module=ModuleType(name);module.__file__=row['path'];sys.modules[name]=module
    exec(compile(raw,row['path'],'exec',dont_inherit=True,optimize=0),module.__dict__)
    return module

def runtime():
    need(sys.platform=='linux' and sys.implementation.name=='cpython' and sys.version_info[:2]==(3,12)
        and sys.executable=='/usr/bin/python3.12' and sys.flags.isolated and sys.flags.no_site
        and sys.dont_write_bytecode and not sys.flags.optimize and os.getuid()==os.geteuid()==0
        and threading.current_thread() is threading.main_thread() and len(os.listdir('/proc/self/task'))==1)

def publish(file,body,common):
    # O_EXCL, no adopt/replace/unlink. An incomplete input remains a permanent
    # restart refusal, not a template that can silently authorize a second PUT.
    file=normalized(str(file));common.ancestors(file)
    fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o444)
    try:
        os.fchmod(fd,0o444);offset=0
        while offset<len(body):
            n=os.write(fd,body[offset:]);need(n>0);offset+=n
        os.fsync(fd)
    finally:os.close(fd)
    fd=os.open(file.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:os.fsync(fd)
    finally:os.close(fd)
    need(root_bytes(file,MAX_LAUNCH)==body)

class Caller:
    def __init__(self,connection,ingress):
        self.connection=connection;self.fd=None
        peer=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        need(peer[0]>0 and peer[1:]==(ingress['uid'],ingress['gid']))
        raw=connection.getsockopt(socket.SOL_SOCKET,77,4);need(len(raw)==4)
        self.fd=struct.unpack('i',raw)[0];need(self.fd>=0)
        try:
            os.set_inheritable(self.fd,False)
            self.peer=peer;self.start=self._start();self.check(waiting_trigger=True)
        except BaseException:self.close();raise
    def _start(self):
        raw=Path('/proc/'+str(self.peer[0])+'/stat').read_text().rsplit(') ',1)[1].split()
        need(raw[0] not in ('Z','T','t','X'));return int(raw[19])
    def check(self,waiting_trigger=False):
        need(self.fd is not None and not select.select([self.fd],[],[],0)[0] and self._start()==self.start)
        fields=dict(row.split(':',1) for row in Path('/proc/'+str(self.peer[0])+'/status').read_text().splitlines() if ':' in row)
        need(fields['Uid'].split()==[str(self.peer[1])]*4 and fields['Gid'].split()==[str(self.peer[2])]*4)
        need(set(fields['Groups'].split())<={str(self.peer[2])} and fields['NoNewPrivs'].strip()=='1')
        need(all(int(fields[k],16)==0 for k in ('CapEff','CapPrm','CapInh','CapAmb')))
        pidinfo=Path('/proc/self/fdinfo/'+str(self.fd)).read_text().splitlines()
        need([s.split(':',1)[1].strip() for s in pidinfo if s.startswith('Pid:')]==[str(self.peer[0])])
        poll=select.poll();poll.register(self.connection.fileno(),
            select.POLLHUP|select.POLLERR|(0 if waiting_trigger else select.POLLIN))
        need(not poll.poll(0))
    def receive(self,trigger):
        self.connection.settimeout(3)
        packet,ancillary,flags,_=self.connection.recvmsg(len(trigger)+1,socket.CMSG_SPACE(256),socket.MSG_CMSG_CLOEXEC)
        credentials=[];bad=False
        for level,kind,body in ancillary:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                fds=array.array('i');fds.frombytes(body[:len(body)//fds.itemsize*fds.itemsize])
                for fd in fds:os.close(fd)
                bad=True
            elif level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS and len(body)==12:
                credentials.append(struct.unpack('3i',body))
            else:bad=True
        # Linux may echo the requested MSG_CMSG_CLOEXEC in msg_flags. It is not
        # truncation/ancillary authority; all other unexpected flags still fail.
        need(not bad and not flags&~socket.MSG_CMSG_CLOEXEC and credentials==[self.peer] and packet==trigger)
        self.check()
    def close(self):
        fd,self.fd=self.fd,None
        if fd is not None:os.close(fd)

def run(file,approved):
    runtime();need(type(approved) is str and re.fullmatch('[0-9a-f]{64}',approved) is not None)
    raw=root_bytes(file,MAX_LAUNCH);need(sha(raw)==approved);value=document(raw)
    need(sha(root_bytes(__file__,100000))==value['entry_sha256'])
    common=capture(value['action']['sources']['profile'],'pult_synthetic_live_profile_v1')
    common.runtime();need(value['action']['format']==common.FORMAT and value['action']['mode']==common.MODE)
    action=value['action'];need(set(action)==common.FIELDS and set(action['sources'])==common.SOURCES)
    for row in action['sources'].values():need(sha(root_bytes(row['path'],100000))==row['sha256'])
    ingress=value['ingress'];need(ingress['uid'] not in (action['writer']['uid'],action['custodian']['uid']))
    observed=common.show(value['controller_unit'],'MainPID,InvocationID,ControlGroup,ActiveState')
    need(observed.get('MainPID')==str(os.getpid()) and observed.get('ActiveState')=='active'
        and observed.get('ControlGroup')=='/system.slice/'+value['controller_unit'])
    action['controller']=dict(unit=value['controller_unit'],invocation_id=observed['InvocationID'],
        boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),pid=os.getpid(),
        start_ticks=int(Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19]))
    # Validate the entire shape and separation BEFORE the first publication.
    template=object.__new__(common.Profile);template.raw=canonical(action);template.file=Path(value['runtime_profile'])
    template._shape(action)
    directory=Path(ingress['directory']);info=directory.lstat();common.ancestors(directory/'placeholder')
    need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and info.st_gid==ingress['gid']
        and stat.S_IMODE(info.st_mode)==0o750 and not list(directory.iterdir()))
    # Ingress-controlled location must not overlap actor state, keys or sources.
    host=common.document(root_bytes(action['custodian']['host_profile'],8192))
    protected=[Path(host['state_file']).parent,Path(action['custodian']['ipc_directory']),
        Path(action['writer']['credential_file']).parent,Path(host['reader_credential_file']).parent,
        Path(action['control']['root']),Path(action['admission']['root']),Path(value['runtime_profile']),
        *[Path(row['path']) for row in action['sources'].values()]]
    need(all(directory!=p and not directory.is_relative_to(p) and not p.is_relative_to(directory) for p in protected))
    publish(value['runtime_profile'],canonical(action),common)
    profile=common.Profile(value['runtime_profile'],sha(canonical(action)));profile.controller_guard()
    entry=capture(ingress['source'],'pult_fixed_synthetic_ssh_ingress')
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET|socket.SOCK_CLOEXEC)
    listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1);listener.settimeout(30)
    connection=None;caller=None
    try:
        listener.bind(ingress['socket_file']);os.chown(ingress['socket_file'],0,ingress['gid']);os.chmod(ingress['socket_file'],0o660)
        listener.listen(1);pin=os.lstat(ingress['socket_file'])
        config=dict(format=entry.FORMAT,caller_uid=ingress['uid'],caller_gid=ingress['gid'],
            controller={k:action['controller'][k] for k in ('pid','start_ticks','boot')},
            socket=dict(path=ingress['socket_file'],device=pin.st_dev,inode=pin.st_ino))
        config_raw=canonical(config);entry.parse_config(config_raw);publish(ingress['config_file'],config_raw,common)
        print(json.dumps(dict(format='pult-synthetic-ingress-published-v1',config_sha256=sha(config_raw)),sort_keys=True),flush=True)
        connection,_=listener.accept();listener.close()
        caller=Caller(connection,ingress);caller.receive(entry.TRIGGER)
        root=profile.capture('root')
        class BoundAction(root.Action):
            def check(self):super().check();caller.check()
        result=BoundAction(profile).run()
        need(result['status']=='COMMITTED_STORAGE_ONLY_AND_STOPPED' and result['whole_groups_empty']
            and result['permanent_fence_retained'] and result['purge'] is False and result['restore'] is False)
        caller.check();connection.settimeout(3)
        need(connection.send(entry.STORAGE_ONLY_REPLY)==len(entry.STORAGE_ONLY_REPLY));connection.shutdown(socket.SHUT_WR)
        # Keep the original root peer alive through ingress's final pidfd check.
        # Client writes nothing beyond its one trigger and closes after checking.
        need(connection.recv(1)==b'')
        return 0
    finally:
        if caller is not None:caller.close()
        if connection is not None:connection.close()
        listener.close()

def main():
    try:
        need(len(sys.argv)==3);return run(sys.argv[1],sys.argv[2])
    except BaseException:
        # No exception messages/locals, key bytes, paths or claimed completion.
        print('{"status":"UNKNOWN_RETAIN_DURABLE_STATE","purge":false,"restore":false}',flush=True)
        return 70

if __name__=='__main__':raise SystemExit(main())
