"""Native fake-data proof driver. NOT a deployed custodian entry/listener."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import sys
import time
from types import ModuleType

COLD_SHA='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'

def event(name,**data): print(json.dumps(dict(event=name,**data),sort_keys=True),flush=True)

def owner(root,ipc,mode):
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
        if not held(): metrics['order_violations']+=1;raise RuntimeError('OWNER_ORDER')
    def audit(name,args):
        if name=='sqlite3.connect': metrics['sql']+=1;require_owner()
        if name=='open' and args and str(args[0])==str(secret): metrics['credential_opens']+=1;require_owner()
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
    cold=ModuleType('_v2_proof_checked_cold');cold.__file__=str(root/'entry/cold_host.py')
    exec(compile(source,cold.__file__,'exec'),cold.__dict__)
    # The native root fixture copies/pins this reviewed protocol before exec.
    protocol_source=(root/'entry/observer_v2.py').read_bytes()
    if hashlib.sha256(protocol_source).hexdigest()!=(root/'entry/observer.sha256').read_text().strip():
        raise RuntimeError('OBSERVER_PIN')
    protocol=ModuleType('_v2_proof_protocol');exec(compile(protocol_source,'observer_v2.py','exec'),protocol.__dict__)
    ready=cold.prepare(str(root/'release'),str(root/'profile.json'),(root/'approval.txt').read_text().strip())
    expected=(root/'expected-request.json').read_bytes()
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    listener.bind(str(ipc/'cold.sock'));listener.listen(4)
    event('OWNER_READY',held=held(),metrics=metrics);deadline=time.monotonic()+150
    try:
        while True:
            if time.monotonic()>deadline: raise RuntimeError('OWNER_TEST_DEADLINE')
            readable,_,_=select.select([listener,sys.stdin],[],[],2)
            if not readable: continue
            if sys.stdin in readable:
                if sys.stdin.readline().strip()=='CLOSE': break
                raise RuntimeError('OWNER_CONTROL')
            connection,_=listener.accept();metrics['connections']+=1
            with connection:
                try:
                    accepted=protocol.serve_once(ready,connection,expected,65534,65534)
                    metrics['accepted' if accepted else 'rejected']+=1
                except (protocol.Refused,OSError,ValueError):
                    metrics['rejected']+=1
            event('OWNER_SERVED',held=held(),metrics=metrics)
    finally:
        listener.close()
        if mode=='uncertain_close':
            def broken(): raise RuntimeError('FAKE_READER_CLOSE_FAILURE')
            ready._reader.close=broken
        try: ready.close()
        except cold.ColdRefused:
            event('OWNER_RETAINED',held=held(),metrics=metrics)
            while True: signal.pause()
        event('OWNER_CLOSED',held=held(),metrics=metrics)

if __name__=='__main__':
    try:
        if sys.flags.optimize or len(sys.argv)!=5 or sys.argv[1]!='--owner': raise RuntimeError('TEST_ENTRY')
        owner(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4])
    except BaseException:
        event('WORKER_FAILURE');raise SystemExit(70)
