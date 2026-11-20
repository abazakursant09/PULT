"""TEST ONLY: exact new child + signed Unix mock S3, no live TLS/IAM claim.

No deployed profile selects this file. The synthetic fixture independently pins
this adapter and the exact child bytes, then drops credentials before Python.
Only HTTPTransport is replaced after the frozen source intake has activated;
all real Lease/SQLite/IPC/type/lifetime/actor/systemd checks remain enabled.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
from types import ModuleType

HERE=Path(__file__).resolve().parent
CHILD_SHA='671948eddd110a0b8e77072bf4ffa83dacf9caed50dbd34624cb239dc0418b91'

def need(ok):
    if not ok:raise RuntimeError('NATIVE_ACTOR_TEST_ADAPTER_REFUSED')

def main():
    need(len(sys.argv)==4 and sys.argv[1] in ('custodian','writer'))
    raw=(HERE/'action_child.py').read_bytes();need(hashlib.sha256(raw).hexdigest()==CHILD_SHA)
    child=ModuleType('exact_child_under_native_test');child.__file__=str(HERE/'action_child.py')
    exec(compile(raw,child.__file__,'exec',dont_inherit=True,optimize=0),child.__dict__)
    original=child.bootstrap
    def bootstrap(file,digest):
        profile=original(file,digest);capture=profile.capture
        def capture_test(name):
            module=capture(name)
            if name=='host' and not getattr(module,'_fixture_transport_installed',False):
                module._fixture_transport_installed=True;previous=module.capture_payload
                def payload(*args,**kwargs):
                    result=previous(*args,**kwargs)
                    import httpx,ssl,select
                    common=sys.modules['pult_synthetic_live_profile_v1']
                    data=common.document(common.root_bytes(HERE/'native-bridge.json',4096))
                    need(set(data)=={'path','identity','pid','start_ticks'})
                    def exchange(request):
                        profile.actor_guard(sys.argv[1])
                        need(request.url.host=='1.1.1.1' and request.extensions.get('sni_hostname')=='s3.ru-3.storage.selcloud.ru')
                        path=Path(data['path']);info=path.lstat()
                        need(info.st_uid==0 and stat.S_ISSOCK(info.st_mode)
                            and dict(device=info.st_dev,inode=info.st_ino)==data['identity'])
                        with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as connection:
                            connection.settimeout(3);connection.connect(str(path))
                            pid,uid,gid=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                            fd=struct.unpack('i',connection.getsockopt(socket.SOL_SOCKET,77,4))[0]
                            os.set_inheritable(fd,False)
                            try:
                                start=int(Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split()[19])
                                need(pid==data['pid'] and uid==gid==0 and start==data['start_ticks'] and not select.select([fd],[],[],0)[0])
                                # Test-only URL projection; original signed Host
                                # and all headers/body are verified by root mock.
                                url=str(request.url).replace('https://1.1.1.1','https://s3.ru-3.storage.selcloud.ru',1)
                                body=common.canonical(dict(method=request.method,url=url,
                                    headers=list(request.headers.multi_items()),body=request.read().hex()))
                                need(len(body)<=16384 and connection.send(body)==len(body))
                                reply,ancillary,flags,_=connection.recvmsg(16385,socket.CMSG_SPACE(64),socket.MSG_CMSG_CLOEXEC)
                                for level,kind,value in ancillary:
                                    if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                                        for n in range(0,len(value)-3,4):os.close(struct.unpack('i',value[n:n+4])[0])
                                need(not ancillary and not flags&~socket.MSG_CMSG_CLOEXEC and len(reply)<=16384)
                                value=common.document(reply)
                            finally:os.close(fd)
                        profile.actor_guard(sys.argv[1])
                        if value=={'error':'mock_io_failed'}:raise httpx.ReadError('synthetic mock unknown',request=request)
                        return httpx.Response(value['status'],content=bytes.fromhex(value['body']),headers=value['headers'],request=request)
                    def factory(**options):
                        need(set(options)=={'verify','trust_env','retries'} and options['trust_env'] is False and options['retries']==0)
                        need(isinstance(options['verify'],ssl.SSLContext) and options['verify'].check_hostname
                            and options['verify'].verify_mode==ssl.CERT_REQUIRED)
                        return httpx.MockTransport(exchange)
                    httpx.HTTPTransport=factory
                    return result
                module.capture_payload=payload
            return module
        profile.capture=capture_test
        return profile
    child.bootstrap=bootstrap;child.main()

if __name__=='__main__':main()
