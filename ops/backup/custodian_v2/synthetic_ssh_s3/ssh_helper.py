"""One fixed synthetic SSH operation from a dedicated nonroot fresh helper.

Root-controlled OS wiring supplies config path and independent SHA. No request
selects a remote command, login, port, key, pin, event or transport. The helper
holds sealed key/pin snapshots; no plaintext temporary key or credential chain.
"""
import base64
import fcntl
import hashlib
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import select
import stat
import struct
import sys
import threading
import time
from types import ModuleType

FORMAT='pult-synthetic-fixed-ssh-helper-v1'
TRIGGER=b'RUN_ONE_SYNTHETIC_STORAGE_ACTION_V1\n'
REPLY=(b'{"format":"pult-synthetic-storage-only-reply-v1",'
       b'"purge":false,"restore":false,"status":"complete","storage_only":true}\n')

class Refused(Exception):pass
def need(ok):
    if not ok:raise Refused('SYNTHETIC_SSH_HELPER_REFUSED')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def stable(info):
    return tuple(getattr(info,k) for k in ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
def canonical(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')

def path(value):
    need(type(value) is str and len(value)<=256 and re.fullmatch('/[A-Za-z0-9_./-]+',value) is not None)
    need(all(n not in ('','.','..') for n in value.split('/')[1:]));return Path(value)

def document(raw):
    need(type(raw) is bytes and 0<len(raw)<=8192)
    def pairs(rows):
        out={}
        for k,v in rows:need(k not in out);out[k]=v
        return out
    try:
        v=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
        need(type(v) is dict and canonical(v)==raw and set(v)=={'format','client_uid','client_gid','custody_host',
            'server_key_sha256','client_key_sha256','identity_file','known_hosts_file','source'})
        need(v['format']==FORMAT and all(type(v[k]) is int and 1000<=v[k]<2**31 for k in ('client_uid','client_gid')))
        address=IPv4Address(v['custody_host']);need(type(v['custody_host']) is str and str(address)==v['custody_host'] and address.is_global and not address.is_multicast)
        for k in ('server_key_sha256','client_key_sha256'):need(type(v[k]) is str and re.fullmatch('SHA256:[A-Za-z0-9+/]{43}',v[k]) is not None)
        for k in ('identity_file','known_hosts_file'):path(v[k])
        need(type(v['source']) is dict and set(v['source'])=={'path','sha256'})
        path(v['source']['path']);need(re.fullmatch('[0-9a-f]{64}',v['source']['sha256']) is not None)
        need(len({v['identity_file'],v['known_hosts_file'],v['source']['path']})==3)
        return v
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):raise Refused('SYNTHETIC_SSH_HELPER_REFUSED') from None

def read_file(file,maximum,uid=0,private=False):
    file=path(str(file))
    for p in file.parents:
        info=p.lstat();sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0,uid) and (not info.st_mode&0o022 or sticky)
            and not any(n.startswith('system.posix_acl_') for n in os.listxattr(p,follow_symlinks=False)))
    fd=os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid==uid and before.st_nlink==1
            and stat.S_IMODE(before.st_mode) in ((0o400,) if private else (0o444,0o644))
            and 0<before.st_size<=maximum and not any(n.startswith('system.posix_acl_') for n in os.listxattr(fd)))
        raw=os.pread(fd,maximum+1,0);need(len(raw)==before.st_size and stable(os.fstat(fd))==stable(before) and stable(file.lstat())==stable(before))
        return raw
    finally:os.close(fd)

def fingerprint(kind,encoded):
    need(kind==b'ssh-ed25519')
    try:blob=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError):raise Refused('SYNTHETIC_SSH_HELPER_REFUSED') from None
    # OpenSSH binary public key: exact length-prefixed type and 32-byte key.
    need(len(blob)==51 and blob[:4]==struct.pack('>I',11) and blob[4:15]==b'ssh-ed25519'
        and blob[15:19]==struct.pack('>I',32))
    return 'SHA256:'+base64.b64encode(hashlib.sha256(blob).digest()).rstrip(b'=').decode('ascii')

def sealed(raw):
    fd=os.memfd_create('pult-synthetic-ssh-snapshot',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(fd,0o400);offset=0
        while offset<len(raw):
            n=os.write(fd,raw[offset:]);need(n>0);offset+=n
        os.lseek(fd,0,os.SEEK_SET)
        seals=fcntl.F_SEAL_SEAL|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_WRITE
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,seals);need(fcntl.fcntl(fd,fcntl.F_GET_SEALS)==seals)
        return fd
    except BaseException:os.close(fd);raise

def runtime():
    need(sys.platform=='linux' and sys.implementation.name=='cpython' and sys.version_info[:2]==(3,12)
        and sys.executable=='/usr/bin/python3.12' and sys.flags.isolated and sys.flags.no_site
        and sys.dont_write_bytecode and not sys.flags.optimize and threading.current_thread() is threading.main_thread()
        and len(os.listdir('/proc/self/task'))==1 and os.getuid()==os.geteuid()>=1000)

def emit_reply():
    fd=os.dup(1);blocking=None;offset=0;deadline=time.monotonic()+2
    try:
        os.set_inheritable(fd,False);blocking=os.get_blocking(fd);os.set_blocking(fd,False)
        while offset<len(REPLY):
            left=deadline-time.monotonic();need(left>0)
            _,writable,exceptional=select.select([],[fd],[fd],left)
            need(bool(writable) and not exceptional)
            try:n=os.write(fd,REPLY[offset:])
            except BlockingIOError:continue
            need(n>0);offset+=n
        need(time.monotonic()<=deadline)
    finally:
        try:
            if blocking is not None:os.set_blocking(fd,blocking)
        finally:os.close(fd)

def run(file,approval):
    runtime();need(type(approval) is str and re.fullmatch('[0-9a-f]{64}',approval) is not None)
    raw=read_file(file,8192);need(sha(raw)==approval);cfg=document(raw)
    need(os.getresuid()==(cfg['client_uid'],)*3 and os.getresgid()==(cfg['client_gid'],)*3 and set(os.getgroups())<={cfg['client_gid']})
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    need(status['Uid'].split()==[str(cfg['client_uid'])]*4 and status['Gid'].split()==[str(cfg['client_gid'])]*4
        and status['NoNewPrivs'].strip()=='1' and all(int(status[k],16)==0 for k in ('CapInh','CapPrm','CapEff','CapAmb')))
    source=cfg['source'];body=read_file(source['path'],30000);need(sha(body)==source['sha256'])
    transport=ModuleType('fixed_synthetic_bounded_ssh');transport.__file__=source['path']
    exec(compile(body,source['path'],'exec',dont_inherit=True,optimize=0),transport.__dict__)
    pin=read_file(cfg['known_hosts_file'],2048);line=pin[:-1] if pin.endswith(b'\n') else pin
    fields=line.split(b' ')
    need(len(fields)==3 and b'\n' not in line and b'\r' not in line and fields[0]==cfg['custody_host'].encode('ascii')
        and fingerprint(fields[1],fields[2])==cfg['server_key_sha256'])
    identity=read_file(cfg['identity_file'],16384,uid=cfg['client_uid'],private=True)
    fds=[]
    try:
        keyfd=sealed(identity);fds.append(keyfd);pinfd=sealed(pin);fds.append(pinfd)
        keypath='/proc/'+str(os.getpid())+'/fd/'+str(keyfd);pinpath='/proc/'+str(os.getpid())+'/fd/'+str(pinfd)
        inspection=transport._run_child(b'',('/usr/bin/ssh-keygen','-y','-P','','-f',keypath),timeout=5.0)
        need(inspection is not None and inspection[1]==0)
        public=inspection[0].strip().split();need(len(public)==2 and fingerprint(*public)==cfg['client_key_sha256'])
        argv=list(transport.build_ssh_argv(cfg['custody_host']))
        argv[argv.index('-i')+1]=keypath
        argv[argv.index('UserKnownHostsFile='+transport.KNOWN_HOSTS)]='UserKnownHostsFile='+pinpath
        answer=transport._run_child(TRIGGER,tuple(argv),timeout=80.0)
        need(answer is not None and answer[1]==0 and answer[0]==REPLY)
        need(read_file(file,8192)==raw and sha(read_file(source['path'],30000))==source['sha256'])
        emit_reply()
        return 0
    finally:
        for fd in reversed(fds):os.close(fd)

def main():
    try:need(len(sys.argv)==3);return run(sys.argv[1],sys.argv[2])
    except BaseException:return 70

if __name__=='__main__':raise SystemExit(main())
