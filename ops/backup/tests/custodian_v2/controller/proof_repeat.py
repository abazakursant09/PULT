"""Native disposable fixture. No live resources, old source edits or recovery."""
import array
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import socket
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT=Path(__file__).resolve().parent
def load(file,name):
    spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module
v2=load(ROOT/'fixture_v2.py','immutable_v2_proof_fixture')
BASE=repeat=None

def configure(base,template):
    global BASE,repeat
    BASE=base;v2.configure(base,template);repeat=load(base/'repeatable_observer.py','reviewed_repeatable_observer')

class Case(v2.Case):
    def setUp(self):
        super().setUp();self.evidence_sockets=[]
        def close_evidence():
            for endpoint in self.evidence_sockets: endpoint.close()
        self.addCleanup(close_evidence)
    def start_owner(self,mode='normal',helper_mode='normal'):
        result=super().start_owner(mode)
        if helper_mode!='normal': self.config(helper_mode)
        self.rcontrol=self.root/'session-control';self.rcontrol.mkdir(mode=0o700)
        (self.rcontrol/'session.lock').write_bytes(b'');(self.rcontrol/'session.lock').chmod(0o600)
        (self.rcontrol/'operations').mkdir(mode=0o700)
        o=v2.observer
        self.rspec=dict(format=repeat.FORMAT,policy=repeat.POLICY,base_manifest=str(self.manifest),base_manifest_sha256=v2.old.sha(self.manifest),
            observer_file=str(BASE/'observer_v2.py'),supervisor_file=str(BASE/'supervisor_v2.py'),lifecycle_file=str(BASE/'repeatable_observer.py'),
            lifecycle_sha256=v2.old.sha(BASE/'repeatable_observer.py'),control_root=str(self.rcontrol),
            control_identity=o.identity(self.rcontrol),lock_identity=o.identity(self.rcontrol/'session.lock'),
            operations_identity=o.identity(self.rcontrol/'operations'),epoch=str(uuid4()),boot=self.spec['host_boot_id'],max_calls=64)
        self.rmanifest=self.root/'session-profile.json';self.approve_repeat();return result

    def approve_repeat(self):
        self.rmanifest.write_bytes(repeat.canonical(self.rspec));self.rmanifest.chmod(0o600)
        (self.rcontrol/'session.identity').write_bytes(repeat.identity_bytes(self.rspec));(self.rcontrol/'session.identity').chmod(0o600)
        self.rprofile=repeat.prepare(self.rmanifest,v2.old.sha(self.rmanifest))

    def loop(self,calls=1,uid=48001,body=None,runtime=3,phase=None,action='kill',on_event=None,cancel=False,fail_call=None,peer_death=False,delivery_cancel=False,preheld_mutex=False):
        path=self.root/'repeat-admission.sock'
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        listener.bind(str(path));path.chmod(0o666);listener.listen(4)
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        controller=os.fork()
        if controller==0:
            session=None
            try:
                parent.close();v2.old.close_except({listener.fileno(),child.fileno()})
                context=self.rprofile;a=context.a;call_number=0
                metrics={'reader_or_database_opens':0}
                forbidden={self.cold_profile['state_file'],self.cold_profile['reader_credential_file']}
                def audit(event,args):
                    if event=='open' and args and str(args[0]) in forbidden:
                        metrics['reader_or_database_opens']+=1;raise RuntimeError('ROOT_PRIVATE_CONTENT_OPEN')
                sys.addaudithook(audit)
                def notice(name):
                    v2.old.send(child,dict(event='PHASE',phase=name,call=call_number))
                    if phase==name and action=='raise': raise OSError('SYNTHETIC_PHASE_FAILURE')
                stage={'manager_returned':False}
                class ProofSession(repeat.Session):
                    def event(instance,name,operation=None):
                        if name=='BEFORE_OPERATION_DIRECTORY': stage['manager_returned']=False
                        if name=='MANAGER_RETURNED': stage['manager_returned']=True
                        notice(name)
                session=ProofSession(context)
                original_open,original_write,original_fsync=os.open,os.write,os.fsync
                def fd_path(fd):
                    try: return os.readlink('/proc/self/fd/'+str(fd))
                    except OSError: return ''
                def op_file(name): return str(name).startswith(str(self.rcontrol/'operations')+'/') and str(name).endswith('/observer-v2.claim')
                def opened(file,flags,*args,**kwargs):
                    fd=original_open(file,flags,*args,**kwargs)
                    if flags&os.O_CREAT and op_file(file):
                        try: notice('OP_CLAIM_OPENED')
                        except BaseException: os.close(fd);raise
                    return fd
                def written(fd,raw):
                    count=original_write(fd,raw)
                    if op_file(fd_path(fd)): notice('OP_CLAIM_WRITTEN')
                    return count
                def synced(fd):
                    target=fd_path(fd)
                    prefix='FINISH' if stage['manager_returned'] else 'OP_CLAIM'
                    if op_file(target): notice(prefix+'_BEFORE_FILE_FSYNC')
                    original_fsync(fd)
                    if op_file(target): notice(prefix+'_FILE_FSYNCED')
                    elif str(target).startswith(str(self.rcontrol/'operations')+'/') and Path(target).name.isdigit():
                        notice(prefix+'_DIRECTORY_FSYNCED')
                os.open,os.write,os.fsync=opened,written,synced
                class Manager(a.Manager):
                    stop=2
                    def started(manager,unit,initial):
                        v2.old.send(child,dict(event='UNIT_READY',unit=unit,state=initial,call=call_number))
                        if cancel: raise KeyboardInterrupt()
                    def run(manager,view,request,caller):
                        original=a.subprocess.Popen
                        def popen(argv,*args,**kwargs):
                            unit=None
                            if argv[0]=='/usr/bin/systemd-run':
                                unit=next(x.removeprefix('--unit=') for x in argv if x.startswith('--unit='))
                                v2.old.send(child,dict(event='REGISTER_UNIT',unit=unit,control=str(view.control),call=call_number))
                            if unit: argv=v2.old.scope_control.bound_command(argv)
                            process=original(argv,*args,**kwargs)
                            if unit:
                                try: fd=os.pidfd_open(process.pid)
                                except BaseException: process.kill();process.wait(timeout=2);raise
                                try: v2.old.send(child,dict(event='REGISTER_CLI',unit=unit,call=call_number),fd)
                                except BaseException: process.kill();process.wait(timeout=2);raise
                                finally: os.close(fd)
                            return process
                        a.subprocess.Popen=popen
                        try: result=super().run(view,request,caller)
                        finally: a.subprocess.Popen=original
                        if fail_call==call_number: return b'CORRUPTED_AFTER_REAL_MANAGER\n',result[1]
                        return result
                manager=Manager();manager.runtime=runtime
                for call_number in range(1,calls+1):
                    notice('BEFORE_ACCEPT_REQUEST_READY')
                    connection,_=listener.accept()
                    class DeliveryProxy:
                        def __getattr__(instance,name): return getattr(connection,name)
                        def sendall(instance,raw): raise KeyboardInterrupt()
                    with connection:
                        held=preheld_mutex and call_number==1
                        if held: session.mutex.acquire()
                        try:
                            endpoint=DeliveryProxy() if delivery_cancel and call_number==1 else connection
                            answer,metadata=session.call(endpoint,manager)
                        except KeyboardInterrupt:
                            if not delivery_cancel: raise
                            # Test host catches cancellation and tries the same
                            # Session again. This is NOT a fabricated call result.
                            answer=repeat.UNKNOWN;metadata={'interrupted_during_delivery':True}
                        finally:
                            if held: session.mutex.release()
                    v2.old.send(child,dict(event='RESULT',answer=answer.decode('ascii'),metadata=metadata,
                        metrics=metrics,state=session.state,completed=len(session.completed),call=call_number))
                session.close();listener.close();os._exit(0)
            except KeyboardInterrupt:
                if session is not None: session.close()
                os._exit(73)
            except BaseException:
                if session is not None: session.close()
                os._exit(71)
        child.close();listener.close()
        try: self.register_fork(controller)
        except BaseException: parent.close();path.unlink();raise
        replies=[];records=[];crashed=False;caller=None
        try:
            for number in range(1,calls+1):
                wire_parent,wire_child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
                self.evidence_sockets.extend((wire_parent,wire_child))
                caller=os.fork()
                if caller==0:
                    try:
                        wire_parent.close();v2.old.close_except({wire_child.fileno()});os.setgroups([]);os.setgid(48003);os.setuid(uid)
                        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
                            connection.settimeout(runtime+25);connection.connect(str(path));connection.sendall(self.request if body is None else body)
                            connection.shutdown(socket.SHUT_WR)
                            wire_child.sendall(v2.observer.canonical(dict(event='REQUEST_READY',bytes=len(self.request if body is None else body))))
                            received=bytearray()
                            transport='EOF'
                            while True:
                                try: packet=connection.recv(4097-len(received))
                                except ConnectionResetError:
                                    transport='RESET';break
                                if not packet: break
                                received.extend(packet)
                                if len(received)>4096: raise RuntimeError('CALLER_REPLY_BOUND')
                        wire_child.sendall(v2.observer.canonical(dict(wire=received.decode('ascii'),bytes=len(received),sha256=v2.observer.digest(received),transport=transport)))
                        wire_child.close()
                        os._exit(74 if transport=='RESET' else 0)
                    except BaseException: os._exit(72)
                wire_child.close()
                self.register_fork(caller);deadline=time.monotonic()+runtime+30;finished=False
                while time.monotonic()<deadline:
                    if not select.select([parent],[],[],max(0,deadline-time.monotonic()))[0]: break
                    packet,items,flags,_=parent.recvmsg(16384,socket.CMSG_SPACE(4),socket.MSG_CMSG_CLOEXEC)
                    if not packet: break
                    record=json.loads(packet);records.append(record);delivered=[]
                    for level,kind,value in items:
                        self.assertEqual((level,kind),(socket.SOL_SOCKET,socket.SCM_RIGHTS))
                        fds=array.array('i');fds.frombytes(value);delivered.extend(fds)
                    self.assertFalse(flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC))
                    if record['event']=='PHASE' and record['phase']=='BEFORE_ACCEPT_REQUEST_READY':
                        wire_parent.settimeout(8);ready=json.loads(wire_parent.recv(1024))
                        self.assertEqual(ready,dict(event='REQUEST_READY',bytes=len(self.request if body is None else body)))
                    if record['event']=='REGISTER_UNIT':
                        control=Path(record['control']);self.assertEqual(control.parent,self.rcontrol/'operations')
                        self.assertRegex(control.name,r'^[0-9]{6}$')
                        view=SimpleNamespace(control=control,expected_request=self.request,profile_sha=self.rprofile.profile_sha,
                            epoch=self.rprofile.epoch,boot=self.rprofile.boot)
                        self.assertEqual(self.rprofile.a._claim_record(view,self.request)['unit'],record['unit']);self.units.add(record['unit'])
                    elif record['event']=='REGISTER_CLI':
                        self.assertEqual(len(delivered),1);self.pins['cli:'+record['unit']]=delivered.pop()
                    elif record['event']=='RESULT':
                        self.assertEqual(record['metrics']['reader_or_database_opens'],0);replies.append(record);finished=True
                    choice=None
                    if record['event']=='PHASE' and record['phase']==phase and action=='kill': choice='crash'
                    if on_event: choice=on_event(self,record,caller,controller) or choice
                    if peer_death and record['event']=='UNIT_READY': signal.pidfd_send_signal(self.pins[caller],signal.SIGKILL)
                    if choice=='crash':
                        signal.pidfd_send_signal(self.pins[controller],signal.SIGKILL);crashed=True;break
                    self.assertFalse(delivered);parent.sendall(b'A')
                    if finished: break
                caller_status=self.reap(caller,runtime+25);caller=None
                wire_parent.settimeout(3)
                delivered_wire=wire_parent.recv(8192);wire_parent.close()
                wire=json.loads(delivered_wire) if delivered_wire else None
                if crashed or cancel: break
                self.assertTrue(finished,'NO_CONTROLLER_RESULT')
                if replies[-1]['metadata'].get('interrupted_during_delivery'):
                    self.assertIsNotNone(wire);self.assertEqual(wire['wire'],'')
                elif peer_death and os.waitstatus_to_exitcode(caller_status)==-signal.SIGKILL:
                    self.assertIsNone(wire)
                else:
                    self.assertIsNotNone(wire)
                    if uid!=self.rprofile.caller_uid and replies[-1]['answer']==repeat.REFUSED.decode() and not replies[-1]['metadata']['launched']:
                        self.assertIn(os.waitstatus_to_exitcode(caller_status),(0,74))
                        self.assertIn(wire['transport'],('EOF','RESET'))
                    else:
                        self.assertEqual(caller_status,0);self.assertEqual(wire['transport'],'EOF')
                    expected=replies[-1]['answer'];self.assertEqual(wire['wire'],expected)
                    self.assertEqual(wire['bytes'],len(expected.encode('ascii')))
                    self.assertEqual(wire['sha256'],v2.observer.digest(expected.encode('ascii')))
                    replies[-1]['caller_wire_bytes']=wire['bytes'];replies[-1]['caller_wire_sha256']=wire['sha256']
            status=self.reap(controller,8)
            self.assertEqual(os.waitstatus_to_exitcode(status),-signal.SIGKILL if crashed else (73 if cancel else 0))
        finally: parent.close();path.unlink()
        return replies,records

    def fresh_refused(self):
        before=(self.rcontrol/'session.claim').read_bytes();inode=(self.rcontrol/'session.claim').stat().st_ino;units=set(self.units)
        if (self.control/v2.a.CLAIM_NAME).exists():
            with self.assertRaises(repeat.Refused): repeat.prepare(self.rmanifest,v2.old.sha(self.rmanifest))
        else:
            self.rprofile=repeat.prepare(self.rmanifest,v2.old.sha(self.rmanifest))
        replies,_=self.loop();self.assertEqual(replies[0]['answer'],repeat.REFUSED.decode())
        self.assertFalse(replies[0]['metadata']['launched']);self.assertEqual(self.units,units)
        self.assertEqual((self.rcontrol/'session.claim').read_bytes(),before);self.assertEqual((self.rcontrol/'session.claim').stat().st_ino,inode)
