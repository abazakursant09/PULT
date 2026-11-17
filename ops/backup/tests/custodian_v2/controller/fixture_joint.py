"""Root-only synthetic harness. No production profile/factory or activation."""
import array
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import select
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import unittest
from uuid import UUID,uuid4

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[4]
TESTS=REPO/'ops/backup/tests/custodian_v2'
SOURCE=REPO/'ops/backup/custodian_v2'
ADMISSION=SOURCE/'controller/frozen/admission.py'
ADMISSION_SHA='961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7'
BASE=TEMPLATE=COLD_WORKER=WORKER=None
IPC_PARENT=None

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
if sha(ADMISSION)!=ADMISSION_SHA: raise RuntimeError('ADMISSION_PIN')
a=load(ADMISSION,'joint_pinned_admission')
cold_tests=load(TESTS/'cold/test_cold_host.py','joint_pinned_cold_fixture')
scope_control=load(ROOT/'scope_control.py','owned_controller_ci_scope')

def configure(base,template):
    global BASE,TEMPLATE,COLD_WORKER,WORKER,IPC_PARENT
    BASE,TEMPLATE=base,template;COLD_WORKER=TESTS/'cold/cold_worker.py';WORKER=base/'joint_worker.py'
    IPC_PARENT=Path(scope_control.read_scope()['ipc'])
    cold_tests.configure(BASE,TEMPLATE,COLD_WORKER)

def close_except(keep):
    for name in os.listdir('/proc/self/fd'):
        fd=int(name)
        if fd>2 and fd not in keep:
            try: os.close(fd)
            except OSError: pass

def send(channel,value,fd=None):
    items=[] if fd is None else [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))]
    channel.sendmsg([a.canonical(value)],items)
    channel.settimeout(5)
    if channel.recv(1)!=b'A': raise RuntimeError('PARENT_OWNERSHIP_ACK')

class ProofManager(a.Manager):
    runtime=3
    stop=1
    def __init__(self,channel,runtime): self.channel=channel;self.runtime=runtime
    def run(self,profile,body,caller):
        original=a.subprocess.Popen
        def observed(argv,*args,**kwargs):
            unit=None
            if argv[0]=='/usr/bin/systemd-run':
                unit=next(item.removeprefix('--unit=') for item in argv if item.startswith('--unit='))
                send(self.channel,{'event':'REGISTER_UNIT','unit':unit})
            if unit: argv=scope_control.bound_command(argv)
            process=original(argv,*args,**kwargs)
            if unit:
                try: fd=os.pidfd_open(process.pid)
                except BaseException:
                    process.kill();process.wait(timeout=2);raise
                try: send(self.channel,{'event':'REGISTER_CLI','unit':unit},fd)
                except BaseException: process.kill();process.wait(timeout=2);raise
                finally: os.close(fd)
            return process
        a.subprocess.Popen=observed
        try: return super().run(profile,body,caller)
        finally: a.subprocess.Popen=original
    def started(self,unit,initial):
        send(self.channel,{'event':'UNIT_READY','unit':unit,'state':initial})

class JointCase(unittest.TestCase):
    def setUp(self):
        self.owner=None;self.owner_fd=None;self.events=[];self.units=set();self.pins={};self.children=set()
        self.cold=cold_tests.ColdHostTests('test_fresh_process_genesis_and_cleanup')
        self.root,self.cold_profile=self.cold.case()
        self.before=cold_tests.unchanged_bytes(self.root)
        self.addCleanup(self.cold_cleanup_checked)
        self.ipc=IPC_PARENT/('j-'+uuid4().hex[:16])
        self.assertLessEqual(len(str(self.ipc/'cold.sock').encode()),107)
        self.assertLessEqual(len(str(self.root/'repeat-admission.sock').encode()),107)
        self.ipc.mkdir(mode=0o755);os.chown(self.ipc,47003,65534);self.ipc.chmod(0o750)
        self.addCleanup(self.cleanup_joint)
        self.control=self.root/'control';self.control.mkdir(mode=0o700)
        payload=self.root/'payload';(payload/'core/venv/bin').mkdir(parents=True)
        shutil.copyfile(WORKER,payload/'launcher.py')
        os.symlink('/usr/bin/python3.12',payload/'core/venv/bin/python')
        (self.root/'config').mkdir()
        credential=self.root/'secrets/writer.json'
        # Use a SEPARATE directory: existing cold secrets/ is custodian0700.
        # Frozen Profile requires secrets/writer.json, so move that old private
        # directory intact and update only the fresh synthetic approved profile.
        old_secret=self.root/'secrets';private_secret=self.root/'cold-secrets'
        old_secret.rename(private_secret);old_secret.mkdir(mode=0o755)
        self.cold_profile['reader_credential_file']=str(private_secret/'reader.json')
        cold_tests.approve(self.root,self.cold_profile)
        # The old cold worker expects secrets/reader.json, so probes below use
        # the same immutable cold entry directly, not that old worker harness.
        credential.write_bytes(b'SYNTHETIC_UNUSED_WRITER_KEY\n');os.chown(credential,65534,65534);credential.chmod(0o400)
        self.request=a.canonical(dict(format='pult-erasure-remote-command-prototype-v1',
            scope=self.cold_profile['scope'],generation=self.cold_profile['generation'],
            location='https://s3.ru-3.storage.selcloud.ru/fake-cold-bucket/pult/erasure-v2/'+self.cold_profile['scope']+'/'+self.cold_profile['generation']+'/events/',op='current'))
        (self.root/'expected-request.json').write_bytes(self.request)
        self.profile=a.Profile(self.root,self.control,48001,48003,65534,65534,
            (('payload/launcher.py',sha(payload/'launcher.py')),),self.request)
        self.config('normal');self.before=cold_tests.unchanged_bytes(self.root)

    def config(self,mode):
        value=dict(format='pult-cold-stop-test-config-v1',socket=str(self.ipc/'cold.sock'),
            owner_uid=47003,request=json.loads(self.request),mode=mode)
        (self.root/'config/writer.json').write_bytes(a.canonical(value))

    def cold_cleanup_checked(self):
        self.assertTrue(self.cold.doCleanups(),'NESTED_COLD_CLEANUP_FAILED')

    def register_fork(self,pid):
        # A direct, unreaped fork child cannot be recycled before our waitpid.
        # Failure aborts verification; it is NOT a fallback peer-authentication.
        try: fd=os.pidfd_open(pid)
        except BaseException:
            try: os.kill(pid,signal.SIGKILL)
            except ProcessLookupError: pass
            limit=time.monotonic()+5
            while time.monotonic()<limit:
                found,_=os.waitpid(pid,os.WNOHANG)
                if found: break
                time.sleep(.01)
            else: raise AssertionError('UNREGISTERED_OWN_CHILD_EXIT_UNCONFIRMED')
            raise
        self.children.add(pid);self.pins[pid]=fd

    def owner_line(self,timeout=8):
        body=bytearray();limit=time.monotonic()+timeout
        while len(body)<8192:
            if not select.select([self.owner.stdout],[],[],max(0,limit-time.monotonic()))[0]: break
            part=os.read(self.owner.stdout.fileno(),1)
            if part==b'\n':
                value=json.loads(body);self.events.append(value);return value
            if not part: break
            body.extend(part)
        raise AssertionError('OWNER_NOTICE_BOUND_OR_DEADLINE')

    def start_owner(self,mode='normal'):
        self.owner=subprocess.Popen(['/usr/bin/python3.12','-I','-S','-B',str(WORKER),
            '--owner',str(self.root),str(self.ipc),mode],user=47003,group=47004,extra_groups=[],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd='/',
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},close_fds=True,umask=0o077)
        try: self.owner_fd=os.pidfd_open(self.owner.pid)
        except BaseException:
            self.owner.kill();self.owner.wait(timeout=5);raise
        record=self.owner_line();self.assertEqual(record['event'],'OWNER_READY')
        self.assertTrue(record['held']);self.assertGreater(record['metrics']['sql'],0)
        self.assertEqual(record['metrics']['credential_opens'],1)
        self.assertEqual(record['metrics']['order_violations'],0)
        sock=self.ipc/'cold.sock';os.chown(sock,47003,65534);sock.chmod(0o660)
        return record

    def stop_owner(self,uncertain=False):
        self.owner.stdin.write(b'CLOSE\n');self.owner.stdin.flush()
        record=self.owner_line()
        if uncertain:
            self.assertEqual(record['event'],'OWNER_RETAINED');self.assertTrue(record['held']);return record
        self.assertEqual(record['event'],'OWNER_CLOSED');self.assertFalse(record['held'])
        self.owner.wait(timeout=5);self.assertEqual(self.owner.returncode,0)
        self.assertEqual(self.owner.stderr.read(),b'');return record

    def kill_owner(self):
        signal.pidfd_send_signal(self.owner_fd,signal.SIGKILL);self.owner.wait(timeout=5)
        self.assertEqual(self.owner.returncode,-signal.SIGKILL)

    def probe(self):
        # Reuse the checked cold worker with its expected secrets/reader.json path
        # via a temporary fixture-only symlink would be invalid. Instead invoke
        # our fixed test worker's distinct probe role, reading approved metadata.
        result=subprocess.run(['/usr/bin/python3.12','-I','-S','-B',str(WORKER),
            '--probe',str(self.root)],user=47003,group=47004,extra_groups=[],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},timeout=10,close_fds=True)
        self.assertEqual(result.returncode,0);self.assertFalse(result.stderr)
        return json.loads(result.stdout)

    def exchange(self,mode='normal',uid=48001,gid=48003,body=None,runtime=3,on_ready=None):
        self.config(mode);path=self.root/'admission.sock'
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        listener.bind(str(path));path.chmod(0o666);listener.listen(1)
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        controller=os.fork()
        if controller==0:
            try:
                parent.close();close_except({listener.fileno(),child.fileno()})
                metrics={'reader_or_database_opens':0}
                forbidden={self.cold_profile['state_file'],self.cold_profile['reader_credential_file']}
                def audit(event,args):
                    if event=='open' and args and str(args[0]) in forbidden:
                        metrics['reader_or_database_opens']+=1;raise RuntimeError('ROOT_SECRET_OR_DB_OPEN')
                sys.addaudithook(audit)
                connection,_=listener.accept();listener.close()
                with connection: answer,metadata=a.call(connection,self.profile,ProofManager(child,runtime))
                send(child,{'event':'RESULT','answer':answer.decode('ascii'),'metadata':metadata,'metrics':metrics})
                os._exit(0)
            except BaseException: os._exit(71)
        child.close();listener.close()
        try: self.register_fork(controller)
        except BaseException:
            parent.close();path.unlink();raise
        caller=os.fork()
        if caller==0:
            try:
                close_except(set());os.setgroups([]);os.setgid(gid);os.setuid(uid)
                connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.settimeout(runtime+15)
                connection.connect(str(path));connection.sendall(self.request if body is None else body)
                connection.shutdown(socket.SHUT_WR)
                while connection.recv(4096): pass
                os._exit(0)
            except BaseException: os._exit(72)
        try: self.register_fork(caller)
        except BaseException:
            parent.close();path.unlink();raise
        reply=None;records=[];crashed=False;deadline=time.monotonic()+runtime+20
        try:
            while time.monotonic()<deadline:
                if not select.select([parent],[],[],max(0,deadline-time.monotonic()))[0]: break
                packet,items,flags,_=parent.recvmsg(16384,socket.CMSG_SPACE(4),socket.MSG_CMSG_CLOEXEC)
                if not packet: break
                record=json.loads(packet);records.append(record)
                delivered=[]
                for level,kind,value in items:
                    self.assertEqual((level,kind),(socket.SOL_SOCKET,socket.SCM_RIGHTS))
                    fds=array.array('i');fds.frombytes(value);delivered.extend(fds)
                self.assertFalse(flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC))
                if record['event']=='REGISTER_UNIT':
                    claim=a._claim_record(self.profile,self.request);self.assertEqual(claim['unit'],record['unit'])
                    self.units.add(record['unit']);self.assertFalse(delivered)
                elif record['event']=='REGISTER_CLI':
                    self.assertEqual(len(delivered),1);self.pins['cli:'+record['unit']]=delivered.pop()
                elif record['event']=='UNIT_READY' and on_ready:
                    action=on_ready(self,record,caller,controller)
                    if action=='crash':
                        signal.pidfd_send_signal(self.pins[controller],signal.SIGKILL);crashed=True;break
                elif record['event']=='RESULT': reply=record
                self.assertFalse(delivered);parent.sendall(b'A')
                if reply is not None: break
        finally: parent.close();path.unlink()
        controller_status=self.reap(controller,timeout=5)
        caller_status=self.reap(caller,timeout=runtime+15)
        if not crashed:
            self.assertIsNotNone(reply,'CONTROLLER_NO_RESULT')
            self.assertEqual(reply['metrics']['reader_or_database_opens'],0)
            self.assertEqual(controller_status,0,'CONTROLLER_EXIT')
            if reply['answer'] not in ('CALL_UNKNOWN\n','CALL_REFUSED\n'):
                self.assertEqual(caller_status,0,'POSITIVE_CALLER_DELIVERY')
        return reply,records

    def reap(self,pid,timeout=5):
        limit=time.monotonic()+timeout
        while time.monotonic()<limit:
            found,status=os.waitpid(pid,os.WNOHANG)
            if found: self.children.discard(pid);return status
            time.sleep(.01)
        raise AssertionError('OWNED_CHILD_WAIT_TIMEOUT')

    def seed_head(self,count):
        self.assertIsNone(self.owner)
        profile=self.cold_profile;head=json.loads((self.root/'expected.json').read_bytes())['head']
        connection=sqlite3.connect(profile['state_file'])
        for number in range(1,count+1):
            base=head;pair=(number-1)//2
            event={'purge_id':str(UUID(int=1000+pair,version=4)),
                'subject_id':str(UUID(int=2000+pair,version=4)),
                'phase':'intent' if number%2 else 'complete'}
            value=dict(format='pult-erasure-event-v2',scope=profile['scope'],generation=profile['generation'],
                sequence=number,previous=base['digest'],event=event)
            value['digest']=hashlib.sha256(a.canonical(value)).hexdigest();head={'sequence':number,'digest':value['digest']}
            body=a.canonical(value)
            connection.execute('INSERT INTO attempts_v2 VALUES(?,?,?,?,?,?,?)',
                (str(uuid4()),base['sequence'],base['digest'],number,head['digest'],body,'COMMITTED'))
        connection.execute('UPDATE anchor_v2 SET seq=?,hash=?,revision=?',(count,head['digest'],count*3))
        connection.commit();connection.close()
        (self.root/'expected.json').write_bytes(a.canonical(dict(head=head,revision=count*3,status='OBSERVED_LOCAL_ONLY')))
        self.before=cold_tests.unchanged_bytes(self.root)

    def unchanged(self):
        self.assertEqual(cold_tests.unchanged_bytes(self.root),self.before)
        for record in self.events:
            metrics=record.get('metrics',{})
            self.assertEqual(metrics.get('order_violations',0),0)
            self.assertEqual(metrics.get('network_denied',0),0)

    def seed_pending(self):
        self.assertIsNone(self.owner)
        profile=self.cold_profile;expected=json.loads((self.root/'expected.json').read_bytes())
        value=dict(format='pult-erasure-event-v2',scope=profile['scope'],generation=profile['generation'],
            sequence=1,previous=expected['head']['digest'],event=dict(purge_id=str(uuid4()),subject_id=str(uuid4()),phase='intent'))
        value['digest']=hashlib.sha256(a.canonical(value)).hexdigest();attempt=str(uuid4());body=a.canonical(value)
        connection=sqlite3.connect(profile['state_file'])
        connection.execute('INSERT INTO attempts_v2 VALUES(?,?,?,?,?,?,?)',
            (attempt,0,expected['head']['digest'],1,value['digest'],body,'DISPATCHED'))
        connection.execute('UPDATE anchor_v2 SET revision=2,pending=?',(attempt,))
        connection.commit();connection.close()
        (self.root/'objects/00000000000000000001.json').write_bytes(body)
        expected.update(revision=2,status='PENDING_REQUIRES_REVIEW')
        (self.root/'expected.json').write_bytes(a.canonical(expected))
        self.before=cold_tests.unchanged_bytes(self.root)

    def cleanup_joint(self):
        errors=[]
        for pid in list(self.children):
            try: signal.pidfd_send_signal(self.pins[pid],signal.SIGKILL);self.reap(pid)
            except ProcessLookupError: pass
            except BaseException: errors.append('CHILD_CLEANUP')
        for unit in self.units:
            try:
                a.Manager().cleanup(unit)
                if not a._empty(unit): raise RuntimeError()
            except BaseException: errors.append('OWNED_UNIT_CLEANUP')
        for fd in self.pins.values():
            try:
                poller=select.poll();poller.register(fd,select.POLLIN)
                if not poller.poll(0):
                    try: signal.pidfd_send_signal(fd,signal.SIGKILL)
                    except ProcessLookupError: pass
                if not poller.poll(3000): errors.append('PINNED_PROCESS_EXIT_UNCONFIRMED')
            except BaseException: errors.append('PINNED_PROCESS_CLEANUP')
            finally: os.close(fd)
        if self.owner is not None:
            try:
                if self.owner.poll() is None:
                    signal.pidfd_send_signal(self.owner_fd,signal.SIGKILL)
                self.owner.wait(timeout=5)
                for stream in (self.owner.stdin,self.owner.stdout,self.owner.stderr): stream.close()
                if self.owner_fd is not None: os.close(self.owner_fd)
            except BaseException: errors.append('OWNER_CLEANUP')
        try: self.unchanged()
        except BaseException: errors.append('SYNTHETIC_STATE_OR_ORDER_CHANGED')
        if self.ipc.parent!=IPC_PARENT or not re.fullmatch('j-[0-9a-f]{16}',self.ipc.name):
            errors.append('IPC_SCOPE')
        else: shutil.rmtree(self.ipc)
        self.assertFalse(errors,'JOINT_CLEANUP_REFUSED')
