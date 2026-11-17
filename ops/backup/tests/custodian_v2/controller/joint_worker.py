"""Disposable test workers, not a service/installer or deployable bridge."""
import array
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import sqlite3
import stat
import struct
import sys
import time
from types import ModuleType

COLD_SHA='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'

def wire(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode()

def event(name,**data):
    print(json.dumps(dict(event=name,**data),sort_keys=True),flush=True)

def owner(root,ipc,mode):
    """Fresh dedicated UID process, real checked cold entry, fake existing data."""
    secret=Path(json.loads((root/'profile.json').read_bytes())['reader_credential_file'])
    lock=root/'private-state/owner.lock'
    metrics={'sql':0,'credential_opens':0,'credential_reads':0,'order_violations':0,
        'network_denied':0,'connections':0,'accepted':0,'rejected':0,'owner_pid':os.getpid()}
    lease_fds=[];original_flock=fcntl.flock;original_read=os.read
    def held():
        wanted=lock.stat()
        for fd in lease_fds:
            try:
                info=os.fstat(fd)
                if (info.st_dev,info.st_ino)==(wanted.st_dev,wanted.st_ino): return True
            except OSError: pass
        return False
    def require_owner():
        if not held():
            metrics['order_violations']+=1;raise RuntimeError('OWNER_ORDER')
    def audit(name,args):
        if name=='sqlite3.connect': metrics['sql']+=1;require_owner()
        if name=='open' and args and str(args[0])==str(secret):
            metrics['credential_opens']+=1;require_owner()
        if name=='socket.__new__' and args[1]!=socket.AF_UNIX:
            metrics['network_denied']+=1;raise RuntimeError('NETWORK_DENIED')
    def flock(fd,operation):
        result=original_flock(fd,operation)
        if operation&fcntl.LOCK_EX and operation&fcntl.LOCK_NB:
            info=os.fstat(fd);wanted=lock.stat()
            if (info.st_dev,info.st_ino)==(wanted.st_dev,wanted.st_ino): lease_fds.append(fd)
        return result
    def read(fd,size):
        try: is_secret=os.readlink('/proc/self/fd/'+str(fd))==str(secret)
        except OSError: is_secret=False
        if is_secret: metrics['credential_reads']+=1;require_owner()
        return original_read(fd,size)
    sys.addaudithook(audit);fcntl.flock=flock;os.read=read
    source=(root/'entry/cold_host.py').read_bytes()
    if hashlib.sha256(source).hexdigest()!=COLD_SHA: raise RuntimeError('COLD_PIN')
    cold=ModuleType('_joint_checked_cold');cold.__file__=str(root/'entry/cold_host.py')
    exec(compile(source,cold.__file__,'exec'),cold.__dict__)
    ready=cold.prepare(str(root/'release'),str(root/'profile.json'),(root/'approval.txt').read_text().strip())
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    path=ipc/'cold.sock';listener.bind(str(path));listener.listen(4)
    event('OWNER_READY',held=held(),metrics=metrics)
    owner_deadline=time.monotonic()+120
    try:
        while True:
            if time.monotonic()>owner_deadline: raise RuntimeError('OWNER_TEST_DEADLINE')
            readable,_,_=select.select([listener,sys.stdin],[],[],2)
            if not readable: continue
            if sys.stdin in readable:
                if sys.stdin.readline().strip()=='CLOSE': break
                raise RuntimeError('OWNER_CONTROL')
            connection,_=listener.accept();metrics['connections']+=1
            with connection:
                connection.settimeout(2)
                peer=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                body=bytearray();valid=peer[1:]==(65534,65534)
                while True:
                    packet,items,flags,_=connection.recvmsg(4097-len(body),1024,socket.MSG_CMSG_CLOEXEC)
                    creds=[]
                    for level,kind,value in items:
                        if level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS and len(value)==12:
                            creds.append(struct.unpack('3i',value))
                        else:
                            valid=False
                            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                                fds=array.array('i');fds.frombytes(value[:len(value)//4*4])
                                for fd in fds: os.close(fd)
                    if flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC): valid=False
                    if not packet: break
                    if creds!=[peer]: valid=False
                    body.extend(packet)
                    if len(body)>4096: valid=False;break
                expected=(root/'expected-request.json').read_bytes()
                if not valid or bytes(body)!=expected:
                    metrics['rejected']+=1;connection.sendall(b'REFUSED\n')
                else:
                    observation=ready.observe()
                    if observation['status']!='OBSERVED_LOCAL_ONLY':
                        metrics['rejected']+=1;connection.sendall(b'PENDING\n')
                    else:
                        metrics['accepted']+=1
                        # TEST-ONLY old current envelope; not schema2 RPC/admission.
                        answer=dict(json.loads(expected),status='OBSERVED_ONLY',
                            request_sha256=hashlib.sha256(expected).hexdigest(),head=observation['head'])
                        connection.sendall(wire(answer))
                connection.shutdown(socket.SHUT_WR)
            event('OWNER_SERVED',held=held(),metrics=metrics)
    finally:
        listener.close()
        if mode=='uncertain_close':
            def broken(): raise RuntimeError('FAKE_READER_CLOSE_FAILURE')
            ready._reader.close=broken
        try: ready.close()
        except cold.ColdRefused:
            event('OWNER_RETAINED',held=held(),metrics=metrics)
            # No ready acknowledgement after uncertain cleanup. Only test-parent
            # controlled process exit may release the ownership in this branch.
            while True: signal.pause()
        event('OWNER_CLOSED',held=held(),metrics=metrics)

def writer():
    """Invoked only by the frozen manager's fixed test profile argv."""
    if (sys.argv[1:3]!=['--role','writer'] or sys.argv[3]!='--config'
            or sys.argv[5]!='--credential-file' or sys.argv[7:]!=['--once']
            or os.getuid()!=65534 or os.getgid()!=65534 or not set(os.getgroups()).issubset({65534})
            or os.environ.get('SSH_ORIGINAL_COMMAND')!='pult-erasure-storage-v1'):
        raise RuntimeError('TEST_WRITER_ENTRY')
    config=Path(sys.argv[4]);raw=config.read_bytes();value=json.loads(raw)
    if wire(value)!=raw or set(value)!={'format','socket','owner_uid','request','mode'} or value['format']!='pult-cold-stop-test-config-v1':
        raise RuntimeError('TEST_CONFIG')
    # The fake credential argv exists only to exercise the unchanged v1 metadata
    # guard. This observer test never opens it or a custodian key/database.
    body=wire(value['request'])
    # Consume the SAME bounded stream sent by the manager before contacting
    # the owner. Reading only config could race fast exit against pipe input.
    received=sys.stdin.buffer.read(4097)
    if len(received)>4096 or received!=body: raise RuntimeError('TEST_FIXED_STDIN')
    connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.settimeout(2)
    connection.connect(value['socket'])
    peer=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    if peer[1]!=value['owner_uid']: raise RuntimeError('OWNER_UID')
    connection.sendall(body);connection.shutdown(socket.SHUT_WR)
    answer=bytearray()
    while True:
        chunk=connection.recv(2049-len(answer))
        if not chunk: break
        answer.extend(chunk)
        if len(answer)>2048: raise RuntimeError('REPLY_BOUND')
    connection.close()
    mode=value['mode']
    if mode in ('hang','descendant'):
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        if mode=='descendant':
            child=os.fork()
            if child==0:
                while True: signal.pause()
        while True: signal.pause()
    if mode=='bad_reply': answer=b'INVALID_TEST_REPLY\n'
    elif mode=='oversize': answer=b'x'*2049
    elif mode=='nonzero': raise SystemExit(8)
    elif mode!='normal': raise RuntimeError('TEST_MODE')
    sys.stdout.buffer.write(answer);sys.stdout.buffer.flush()

def probe(root):
    metrics={'sql':0,'key':0}
    secret=json.loads((root/'profile.json').read_bytes())['reader_credential_file']
    def audit(name,args):
        if name=='sqlite3.connect': metrics['sql']+=1
        if name=='open' and args and str(args[0])==secret: metrics['key']+=1
    sys.addaudithook(audit)
    source=(root/'entry/cold_host.py').read_bytes()
    if hashlib.sha256(source).hexdigest()!=COLD_SHA: raise RuntimeError('COLD_PIN')
    cold=ModuleType('_joint_probe_cold');cold.__file__=str(root/'entry/cold_host.py')
    exec(compile(source,cold.__file__,'exec'),cold.__dict__)
    try:
        ready=cold.prepare(str(root/'release'),str(root/'profile.json'),(root/'approval.txt').read_text().strip())
    except cold.ColdRefused:
        print(json.dumps(dict(ok=False,**metrics)),flush=True);return
    observed=ready.observe();ready.close()
    print(json.dumps(dict(ok=True,sequence=observed['head']['sequence'],revision=observed['revision'],**metrics)),flush=True)

if __name__=='__main__':
    try:
        if sys.flags.optimize: raise RuntimeError('NO_OPTIMIZED_TEST_WORKER')
        if len(sys.argv)==5 and sys.argv[1]=='--owner': owner(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4])
        elif len(sys.argv)==3 and sys.argv[1]=='--probe': probe(Path(sys.argv[2]))
        elif sys.argv[1:3]==['--role','writer']: writer()
        else: raise RuntimeError('TEST_ENTRY')
    except SystemExit: raise
    except BaseException:
        # No raw exceptions, payload, credentials or arbitrary path diagnostics.
        event('WORKER_FAILURE');raise SystemExit(70)
