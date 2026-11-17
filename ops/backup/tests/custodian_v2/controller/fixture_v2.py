"""Disposable native fixture wiring. No deployment/source mutation of v1."""
import array
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

HERE=Path(__file__).resolve().parent
def load(file,name):
    spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module
old=load(HERE/'fixture_joint.py','v2_immutable_fixture')
BASE=observer=a=None

def configure(base,template):
    global BASE,observer,a
    BASE=base;old.configure(base,template)
    observer=load(base/'observer_v2.py','v2_reviewed_observer')
    a=load(base/'supervisor_v2.py','v2_reviewed_supervisor');a.load_core(base/'legacy_admission.py')

class Case(old.JointCase):
    def setUp(self):
        self.spec=None
        super().setUp()
        self.vroot=self.root/'v2-helper';self.vroot.mkdir(mode=0o755)
        self.control=self.root/'v2-control';self.control.mkdir(mode=0o700)
        (self.control/'observer-v2.lock').write_bytes(b'');(self.control/'observer-v2.lock').chmod(0o600)
        self.spec=None;self.manifest=self.root/'observer-profile.json'
        self.request=observer.request({'binding':dict(scope=self.cold_profile['scope'],generation=self.cold_profile['generation'],
            location='https://s3.ru-3.storage.selcloud.ru/fake-cold-bucket/pult/erasure-v2/'+self.cold_profile['scope']+'/'+self.cold_profile['generation']+'/events/')})
        (self.root/'expected-request.json').write_bytes(self.request)

    def start_owner(self,mode='normal'):
        self.owner=subprocess.Popen(['/usr/bin/python3.12','-I','-S','-B',str(BASE/'owner_worker.py'),
            '--owner',str(self.root),str(self.ipc),mode],user=47003,group=47004,extra_groups=[],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd='/',
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},close_fds=True,umask=0o077)
        try: self.owner_fd=os.pidfd_open(self.owner.pid)
        except BaseException: self.owner.kill();self.owner.wait(timeout=5);raise
        record=self.owner_line();self.assertEqual(record['event'],'OWNER_READY');self.assertTrue(record['held'])
        self.assertEqual(record['metrics']['order_violations'],0)
        sock=self.ipc/'cold.sock';os.chown(sock,47003,65534);sock.chmod(0o660)
        self.spec=dict(format=observer.FORMAT,release_root=str(self.vroot),control_root=str(self.control),
            control_identity=observer.identity(self.control),lock_identity=observer.identity(self.control/'observer-v2.lock'),
            control_epoch=str(uuid4()),host_boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            principals=dict(caller_uid=48001,caller_gid=48003,helper_uid=65534,helper_gid=65534,custodian_uid=47003,custodian_gid=47004),
            binding={k:json.loads(self.request)[k] for k in ('scope','generation','location')},
            cold_entry=str(self.root/'entry/cold_host.py'),legacy_core=str(BASE/'legacy_admission.py'),
            cold_profile=str(self.root/'profile.json'),cold_profile_sha256=old.sha(self.root/'profile.json'),
            source_hashes={},helper_config_sha256='',owner=dict(pid=self.owner.pid,start=observer.process_start(self.owner.pid,47003,47004),
                uid=47003,gid=47004,socket=str(sock),socket_identity=observer.identity(sock),socket_gid=65534),
            safety_policy=observer.POLICY,runtime_seconds=40,stop_seconds=2)
        self.config('normal');return record

    def approve(self):
        self.spec['helper_config_sha256']=observer.digest(observer.helper_config(self.spec))
        (self.vroot/'helper.json').write_bytes(observer.helper_config(self.spec))
        self.manifest.write_bytes(observer.canonical(self.spec));self.manifest.chmod(0o600)
        (self.control/'observer-v2.identity').write_bytes(observer.control_identity(self.spec))
        (self.control/'observer-v2.identity').chmod(0o600)
        self.profile=observer.prepare(self.manifest,old.sha(self.manifest))

    def config(self,mode):
        if self.spec is None: return
        if (self.control/a.CLAIM_NAME).exists(): return  # NEVER reapprove an existing operation.
        raw=(BASE/'observer_v2.py').read_bytes()
        if mode!='normal':
            line=b'        helper(sys.argv[2],sys.argv[4])'
            self.assertEqual(raw.count(line),1)
            faults={
                'hang':b'\n        import signal\n        signal.signal(signal.SIGTERM,signal.SIG_IGN)\n        while True: signal.pause()',
                'descendant':b'\n        import signal\n        signal.signal(signal.SIGTERM,signal.SIG_IGN)\n        os.fork()\n        while True: signal.pause()',
                'bad_reply':b'\n        print("EXTRA_INVALID_FRAME",flush=True)',
                'oversize':b'\n        print("x"*2049,flush=True)',
                'nonzero':b'\n        raise SystemExit(8)'}
            raw=raw.replace(line,line+faults[mode])
        (self.vroot/'observer_v2.py').write_bytes(raw)
        shutil.copyfile(BASE/'supervisor_v2.py',self.vroot/'supervisor_v2.py')
        self.spec['source_hashes']={name:old.sha(self.vroot/name) for name in ('observer_v2.py','supervisor_v2.py')}
        self.approve()

    def served(self,accepted=True):
        record=self.owner_line();self.assertEqual(record['event'],'OWNER_SERVED');self.assertTrue(record['held'])
        self.assertEqual(record['metrics']['order_violations'],0)
        self.assertGreater(record['metrics']['credential_reads'],0)
        if accepted: self.assertGreater(record['metrics']['accepted'],0)
        else: self.assertGreater(record['metrics']['rejected'],0)
        return record

    def exchange(self,mode='normal',uid=48001,gid=48003,body=None,runtime=3,on_ready=None,finish_fault=False,cancel=False):
        if mode!='normal': self.config(mode)
        path=self.root/'admission-v2.sock'
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        listener.bind(str(path));path.chmod(0o666);listener.listen(1)
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        controller=os.fork()
        if controller==0:
            try:
                parent.close();old.close_except({listener.fileno(),child.fileno()})
                metrics={'reader_or_database_opens':0};forbidden={self.cold_profile['state_file'],self.cold_profile['reader_credential_file']}
                def audit(event,args):
                    if event=='open' and args and str(args[0]) in forbidden:
                        metrics['reader_or_database_opens']+=1;raise RuntimeError('ROOT_SECRET_OR_DB_OPEN')
                sys.addaudithook(audit)
                manager=self.manager(child,runtime,finish_fault,cancel)
                connection,_=listener.accept();listener.close()
                with connection: answer,metadata=a.call(connection,self.profile,manager)
                old.send(child,dict(event='RESULT',answer=answer.decode('ascii'),metadata=metadata,metrics=metrics))
                os._exit(0)
            except KeyboardInterrupt: os._exit(73)
            except BaseException: os._exit(71)
        child.close();listener.close()
        try: self.register_fork(controller)
        except BaseException: parent.close();path.unlink();raise
        caller=os.fork()
        if caller==0:
            try:
                old.close_except(set());os.setgroups([]);os.setgid(gid);os.setuid(uid)
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
                    connection.settimeout(runtime+20);connection.connect(str(path));connection.sendall(self.request if body is None else body)
                    connection.shutdown(socket.SHUT_WR)
                    while connection.recv(4096): pass
                os._exit(0)
            except BaseException: os._exit(72)
        try: self.register_fork(caller)
        except BaseException: parent.close();path.unlink();raise
        reply=None;records=[];crashed=False;deadline=time.monotonic()+runtime+25
        try:
            while time.monotonic()<deadline:
                if not select.select([parent],[],[],max(0,deadline-time.monotonic()))[0]: break
                packet,items,flags,_=parent.recvmsg(16384,socket.CMSG_SPACE(4),socket.MSG_CMSG_CLOEXEC)
                if not packet: break
                record=json.loads(packet);records.append(record);delivered=[]
                for level,kind,value in items:
                    self.assertEqual((level,kind),(socket.SOL_SOCKET,socket.SCM_RIGHTS))
                    fds=array.array('i');fds.frombytes(value);delivered.extend(fds)
                self.assertFalse(flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC))
                if record['event']=='REGISTER_UNIT':
                    self.assertEqual(a._claim_record(self.profile,self.request)['unit'],record['unit']);self.units.add(record['unit'])
                elif record['event']=='REGISTER_CLI':
                    self.assertEqual(len(delivered),1);self.pins['cli:'+record['unit']]=delivered.pop()
                elif record['event']=='UNIT_READY' and on_ready:
                    if on_ready(self,record,caller,controller)=='crash':
                        signal.pidfd_send_signal(self.pins[controller],signal.SIGKILL);crashed=True;break
                elif record['event']=='RESULT': reply=record
                self.assertFalse(delivered);parent.sendall(b'A')
                if reply is not None: break
        finally: parent.close();path.unlink()
        status=self.reap(controller,5);caller_status=self.reap(caller,runtime+20)
        if cancel:
            self.assertEqual(os.waitstatus_to_exitcode(status),73);return reply,records
        if not crashed:
            self.assertEqual(status,0,'CONTROLLER_EXIT');self.assertIsNotNone(reply)
            self.assertEqual(reply['metrics']['reader_or_database_opens'],0)
            if reply['answer'] not in (a.UNKNOWN.decode(),a.REFUSED.decode()): self.assertEqual(caller_status,0)
        return reply,records

    def manager(self,channel,runtime,finish_fault,cancel):
        class ProofManager(a.Manager):
            stop=2
            def started(manager,unit,initial):
                old.send(channel,dict(event='UNIT_READY',unit=unit,state=initial))
                if cancel: raise KeyboardInterrupt()
            def run(manager,profile,body,caller):
                original=a.subprocess.Popen
                def observed(argv,*args,**kwargs):
                    unit=None
                    if argv[0]=='/usr/bin/systemd-run':
                        unit=next(x.removeprefix('--unit=') for x in argv if x.startswith('--unit='))
                        old.send(channel,dict(event='REGISTER_UNIT',unit=unit))
                    if unit: argv=old.scope_control.bound_command(argv)
                    process=original(argv,*args,**kwargs)
                    if unit:
                        try: fd=os.pidfd_open(process.pid)
                        except BaseException: process.kill();process.wait(timeout=2);raise
                        try: old.send(channel,dict(event='REGISTER_CLI',unit=unit),fd)
                        except BaseException: process.kill();process.wait(timeout=2);raise
                        finally: os.close(fd)
                    return process
                a.subprocess.Popen=observed
                try: result=super().run(profile,body,caller)
                finally: a.subprocess.Popen=original
                if finish_fault:
                    def fail(path): raise OSError('SYNTHETIC_FINISH_FSYNC_FAILURE')
                    a._sync_directory=fail  # new controller namespace ONLY, after actual manager cleanup
                return result
        manager=ProofManager();manager.runtime=runtime;return manager
