"""Private PID/network namespace synthetic IPC gate. No installation or live I/O."""
import sys
sys.dont_write_bytecode=True
import contextlib
import array
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import grp
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import traceback
import unittest

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2'
TESTS=REPO/'ops/backup/tests/custodian_v2'
CATALOG_SHA='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'
HELPER_SHA='d343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d'
ROOT=FIXTURE=None
SUPERVISOR_SHA='d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b'
SCOPE_SHA='d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'

def sha(file): return hashlib.sha256(file.read_bytes()).hexdigest()
def load(file,name):
    spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

def remove_case(path,identity):
    if (path.parent!=ROOT/'data' or path.resolve()!=path or path.is_symlink()
        or (path.stat().st_dev,path.stat().st_ino)!=identity): raise RuntimeError('CASE_CLEANUP_IDENTITY_REFUSED')
    shutil.rmtree(path)

def line(process):
    raw=b'';deadline=time.monotonic()+8
    while b'\n' not in raw and len(raw)<=4096:
        remaining=deadline-time.monotonic()
        if remaining<=0 or not select.select([process.stdout],[],[],remaining)[0]: raise RuntimeError('WORKER_READY_TIMEOUT')
        part=os.read(process.stdout.fileno(),1)
        if not part: raise RuntimeError('WORKER_NOT_READY')
        raw+=part
    return json.loads(raw)

def fixture(root,mode):
    FIXTURE.Fixture.bootstrap(root);root.chmod(0o755)
    state=root/'private-state'
    for path in (state,state/'anchor.sqlite',state/'owner.lock'): os.chown(path,47003,47004)
    (root/'fixture.json').chmod(0o644)
    (root/'ipc').mkdir(mode=0o750);os.chown(root/'ipc',47003,48003);(root/'ipc').chmod(0o750)
    for role,uid,gid,key in (('reader',47003,47004,FIXTURE.READER_KEY),('writer',48002,48003,FIXTURE.WRITER_KEY)):
        directory=root/(role+'-private');directory.mkdir(mode=0o700);os.chown(directory,uid,gid)
        file=directory/'key.json';file.write_bytes(json.dumps(key).encode());file.chmod(0o400);os.chown(file,uid,gid)
    return config(root,mode)

def config(root,mode):
    value=dict(mode=mode,spec=json.loads((root/'fixture.json').read_bytes()),
        broker_path=str(root/'broker.sock'),owner_path=str(root/'ipc/owner.sock'),
        reader_key=str(root/'reader-private/key.json'),writer_key=str(root/'writer-private/key.json'),
        endpoint=FIXTURE.ENDPOINT,region='ru-3',bucket=FIXTURE.BUCKET,
        binding={'scope':FIXTURE.BINDING.scope,'generation':FIXTURE.BINDING.generation},
        event={'purge_id':FIXTURE.event().purge_id,'subject_id':FIXTURE.event().subject_id,'phase':'intent'},
        attempt=FIXTURE.ATTEMPT,expected_head={'sequence':FIXTURE.reference().sequence,'digest':FIXTURE.reference().digest},
        writer_uid=48002,writer_gid=48003,owner_uid=47003,owner_gid=47004)
    (root/'config.json').write_bytes(json.dumps(value,sort_keys=True).encode());(root/'config.json').chmod(0o644)
    return value

def calls(root):
    file=root/'calls.jsonl'
    return [] if not file.exists() else [json.loads(row) for row in file.read_text().splitlines()]

def database(root):
    connection=sqlite3.connect(root/'private-state/anchor.sqlite')
    try:
        meta=connection.execute('SELECT seq,hash,revision,pending FROM anchor_v2').fetchone()
        phases=connection.execute('SELECT phase FROM attempts_v2 ORDER BY target_seq').fetchall()
        return meta,[row[0] for row in phases]
    finally: connection.close()

def execute(root,mode):
    config(root,mode);children=[];pins={};records={};codes={}
    def start(role,uid=None,gid=None):
        args=dict(stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},close_fds=True,start_new_session=True)
        if uid is not None: args.update(user=uid,group=gid,extra_groups=[])
        child=subprocess.Popen(['/usr/bin/python3.12','-I','-S','-B',str(ROOT/'candidate/worker.py'),role,str(ROOT),str(root)],**args)
        children.append(child)
        try: pins[child]=os.pidfd_open(child.pid)
        except BaseException: child.kill();child.wait(timeout=3);raise
        return child
    try:
        broker=start('broker');assert line(broker)['event']=='BROKER_READY'
        owner=start('owner',47003,47004);assert line(owner)['event']=='OWNER_READY'
        writer=start('writer',48004 if mode=='wrong_uid' else 48002,48003)
        out,err=writer.communicate(timeout=15);codes['writer']=writer.returncode
        assert not err and writer.returncode==0
        rows=[json.loads(row) for row in out.splitlines()];assert len(rows)==1 and rows[0]['event']=='WRITER_DONE'
        records['writer']=rows[0]
        out,err=owner.communicate(timeout=12);codes['owner']=owner.returncode
        assert not err
        rows=[json.loads(row) for row in out.splitlines()]
        if mode.startswith('kill_after_'):
            assert owner.returncode==-signal.SIGKILL and not rows
        else:
            assert owner.returncode==0 and len(rows)==1 and rows[0]['event']=='OWNER_DONE'
            records['owner']=rows[0]
        broker.stdin.write(b'STOP\n');broker.stdin.flush()
        out,err=broker.communicate(timeout=5);codes['broker']=broker.returncode
        assert broker.returncode==0 and not err
        return records,codes
    finally:
        for child in reversed(children):
            if child.poll() is None: signal.pidfd_send_signal(pins[child],signal.SIGKILL)
            child.communicate(timeout=5)
            if child in pins: os.close(pins[child])

CASES={
 'happy':('COMMITTED',3,1,1), 'repeat_client':('COMMITTED',3,1,1),
 'bad_format':(None,0,0,0),'extra_prepare':(None,0,0,0),
 'oversize_prepare':(None,0,0,0),'noncanonical_prepare':(None,0,0,0),
 'duplicate_prepare':(None,0,0,0),'rights_prepare':(None,0,0,0),
 'inherited_fd':(None,0,0,0),'wrong_binding':(None,0,0,0),
 'stale_base':(None,0,0,0),'wrong_uid':(None,0,0,0),
 'wrong_dispatch':('PREPARED',1,0,0),'wrong_confirm':('DISPATCHED',2,1,0),
 'bad_prepared':('PREPARED',1,0,0),'bool_prepared':('PREPARED',1,0,0),
 'bad_dispatched':('DISPATCHED',2,0,0),'lost_committed':('COMMITTED',3,1,1),
 'bad_commit':('COMMITTED',3,1,1),'read_fail':('DISPATCHED',2,1,1),
 'tamper':('DISPATCHED',2,1,1),'write_412':('DISPATCHED',2,1,0),
 'write_500':('DISPATCHED',2,1,0),'write_unknown':('DISPATCHED',2,1,0),
 'kill_after_prepared':('PREPARED',1,0,0),
 'kill_after_dispatched':('DISPATCHED',2,None,0),
 'timeout_after_prepared':('PREPARED',1,0,0),'child_ack_prepared':('PREPARED',1,0,0),
}

class NativeTests(unittest.TestCase):
    def case(self,mode):
        root=Path(tempfile.mkdtemp(prefix='case-',dir=ROOT/'data'));root.rmdir()
        fixture(root,mode);identity=(root.stat().st_dev,root.stat().st_ino);self.addCleanup(remove_case,root,identity)
        before=sha(root/'private-state/anchor.sqlite');key_before={role:sha(root/(role+'-private/key.json')) for role in ('reader','writer')}
        records,codes=execute(root,mode);meta,phases=database(root);phase,revision,puts,gets=CASES[mode]
        self.assertEqual(phases,[] if phase is None else [phase]);self.assertEqual(meta[2],revision)
        self.assertEqual(meta[0],int(phase=='COMMITTED'));self.assertEqual(meta[3],FIXTURE.ATTEMPT if phase in ('PREPARED','DISPATCHED') else None)
        expected_body,target=FIXTURE.reference(FIXTURE.reference(),FIXTURE.event())
        expected_head=target if phase=='COMMITTED' else FIXTURE.reference()
        self.assertEqual(meta[1],expected_head.digest)
        attempts=calls(root);actual_puts=sum(row['method']=='PUT' for row in attempts);actual_gets=sum(row['method']=='GET' for row in attempts)
        self.assertLessEqual(actual_puts,1);self.assertLessEqual(actual_gets,1)
        if puts is not None: self.assertEqual(actual_puts,puts)
        else: self.assertIn(actual_puts,(0,1))
        self.assertEqual(actual_gets,gets)
        self.assertTrue(all(row['uid']==(48002 if row['method']=='PUT' else 47003) for row in attempts))
        objects={file.name:file.read_bytes() for file in (root/'objects').iterdir()}
        if phase=='COMMITTED' or mode in ('wrong_confirm','read_fail','tamper','write_unknown'):
            self.assertEqual(len(objects),1)
        elif mode=='kill_after_dispatched': self.assertEqual(len(objects),actual_puts)
        else: self.assertEqual(objects,{})
        if objects: self.assertEqual(objects,{'00000000000000000001.json':expected_body})
        if phase is None: self.assertEqual(sha(root/'private-state/anchor.sqlite'),before)
        writer=records['writer'];answer=writer['outcome']
        expected='COMMITTED_STORAGE_ONLY' if mode in ('happy','repeat_client') else 'UNKNOWN'
        self.assertEqual(answer['status'],expected);self.assertIs(answer['purge_or_restore_admitted'],False)
        if expected=='COMMITTED_STORAGE_ONLY':
            self.assertEqual(json.dumps(answer['head'],sort_keys=True),json.dumps({'sequence':target.sequence,'digest':target.digest},sort_keys=True))
        else: self.assertIsNone(answer['head'])
        self.assertTrue(writer['reader_denied']);self.assertTrue(writer['db_denied'])
        if mode=='repeat_client': self.assertTrue(writer['repeat_refused'])
        if 'owner' in records:
            self.assertTrue(records['owner']['held_before_close']);self.assertEqual(records['owner']['rights_fd_delta'],0)
            self.assertTrue(records['owner']['writer_denied'])
        self.assertEqual({role:sha(root/(role+'-private/key.json')) for role in key_before},key_before)
        if mode in ('wrong_confirm','lost_committed'):
            old=sha(root/'private-state/anchor.sqlite');count=len(calls(root))
            replay,_=execute(root,'happy')
            self.assertEqual(replay['writer']['outcome']['status'],'UNKNOWN')
            self.assertEqual(len(calls(root)),count);self.assertEqual(sha(root/'private-state/anchor.sqlite'),old)
            self.assertEqual({file.name:file.read_bytes() for file in (root/'objects').iterdir()},objects)
    def test_exact_wire_types_and_parser_negatives(self):
        import staged_ipc as ipc
        for raw in (b'{}',b'{"x":1,"x":1}\n',b'[]\n',b'x'*4097,b'{"x":NaN}\n'):
            with self.assertRaises(ipc.Refused): ipc.decode(raw)
        self.assertFalse(ipc.exact({'n':True},{'n':1}));self.assertFalse(ipc.exact({'n':1.0},{'n':1}))
        self.assertEqual(ipc.decode(b'{"n":1}\n'),{'n':1})
    def test_dead_connector_retained_socket_cannot_be_adopted_by_numeric_pid_lookup(self):
        import staged_ipc as ipc
        root=Path(tempfile.mkdtemp(prefix='dead-',dir=ROOT/'data'));root.chmod(0o755)
        identity=(root.stat().st_dev,root.stat().st_ino);self.addCleanup(remove_case,root,identity);path=root/'peer.sock'
        os.chown(root,47003,48003);root.chmod(0o750)
        go_read,go_write=os.pipe();ready_read,ready_write=os.pipe();result_read,result_write=os.pipe()
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        children={};retained=[]
        def reap(pid):
            _,status=os.waitpid(pid,0);fd=children.pop(pid);os.close(fd);return status
        try:
            owner=os.fork()
            if owner==0:
                try:
                    parent.close();child.close();os.close(go_write);os.close(ready_read);os.close(result_read)
                    os.setgroups([]);os.setgid(47004);os.setuid(47003)
                    listener=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
                    listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1);listener.bind(str(path));path.chmod(0o666);listener.listen(1)
                    os.write(ready_write,b'R');connection,_=listener.accept()
                    if os.read(go_read,1)!=b'G': os._exit(72)
                    # Model an unreliable/recycled numeric /proc lookup. A
                    # socket-derived pidfd must refuse before trusting this.
                    ipc.process_start=lambda pid:1
                    before=len(os.listdir('/proc/self/fd'));channel=None;accepted=False
                    try: channel=ipc.Channel(connection,48002,48003);channel.receive();accepted=True
                    except Exception: pass
                    finally:
                        if channel is not None: channel.close()
                    delta=len(os.listdir('/proc/self/fd'))-before
                    os.write(result_write,json.dumps({'accepted':accepted,'fd_delta':delta}).encode())
                    connection.close();listener.close();os._exit(0)
                except BaseException: os._exit(73)
            children[owner]=os.pidfd_open(owner)
            self.assertTrue(select.select([ready_read],[],[],5)[0]);self.assertEqual(os.read(ready_read,1),b'R')
            writer=os.fork()
            if writer==0:
                try:
                    parent.close();os.setgroups([]);os.setgid(48003);os.setuid(48002)
                    connection=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);connection.connect(str(path))
                    connection.send(b'{"probe":true}\n')
                    child.sendmsg([b'F'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[connection.fileno()]))])
                    connection.close();child.close();os._exit(0)
                except BaseException: os._exit(74)
            children[writer]=os.pidfd_open(writer);child.close()
            self.assertTrue(select.select([parent],[],[],5)[0])
            message,items,flags,_=parent.recvmsg(1,socket.CMSG_SPACE(4),socket.MSG_CMSG_CLOEXEC)
            valid_items=True
            for level,kind,body in items:
                if (level,kind)==(socket.SOL_SOCKET,socket.SCM_RIGHTS):
                    descriptors=array.array('i');descriptors.frombytes(body[:len(body)//descriptors.itemsize*descriptors.itemsize]);retained.extend(descriptors)
                else: valid_items=False
            self.assertEqual(message,b'F');self.assertFalse(flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC));self.assertTrue(valid_items)
            self.assertEqual(len(retained),1);self.assertEqual(reap(writer),0)
            os.write(go_write,b'G');self.assertTrue(select.select([result_read],[],[],5)[0])
            self.assertEqual(json.loads(os.read(result_read,1024)),{'accepted':False,'fd_delta':0});self.assertEqual(reap(owner),0)
        finally:
            for pid,fd in children.items():
                try: signal.pidfd_send_signal(fd,signal.SIGKILL)
                except ProcessLookupError: pass
                os.waitpid(pid,0);os.close(fd)
            for fd in retained+[go_read,go_write,ready_read,ready_write,result_read,result_write]:
                try: os.close(fd)
                except OSError: pass
            parent.close();child.close()

for mode in CASES:
    def method(self,mode=mode): self.case(mode)
    setattr(NativeTests,'test_native_'+mode,method)

class Result(unittest.TestResult):
    def _exc_info_to_string(self,error,test):
        kind,value,tb=error
        return kind.__name__+' '+str([(Path(row.filename).name,row.lineno,row.name) for row in traceback.extract_tb(tb)])
    def addSkip(self,test,reason): super().addSkip(test,'reason_suppressed')
    def stopTest(self,test):
        super().stopTest(test);print('TEST_FINISHED '+str(self.testsRun)+' '+test._testMethodName,flush=True)

def main():
    global ROOT,FIXTURE
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_ipc_gate_refused"}');return 2
    if sys.argv[1:]!=['--reviewed-by-main']: return 2
    if sys.platform=='linux' and os.geteuid()==0 and os.getpid()!=1:
        # The unchanged controller supervisor bounds this entire PID namespace.
        os.execv('/usr/bin/unshare',['unshare','--pid','--fork','--mount-proc','--net',
            '/usr/bin/python3.12','-I','-S','-B',str(HERE/'run_gate.py'),'--reviewed-by-main'])
    assert sys.platform=='linux' and os.geteuid()==0 and os.getpid()==1 and sys.version_info[:2]==(3,12)
    assert [row.split(':',1)[0].strip() for row in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']
    for fn,num in ((pwd.getpwuid,47003),(pwd.getpwuid,48002),(pwd.getpwuid,48004),(grp.getgrgid,47004),(grp.getgrgid,48003)):
        try: fn(num)
        except KeyError: pass
        else: raise RuntimeError('SYNTHETIC_IDENTITY_CONFLICT')
    assert sha(SOURCE/'source-profile.json')==CATALOG_SHA and sha(TESTS/'support.py')==HELPER_SHA
    assert sha(TESTS/'controller/ci_gate.py')==SUPERVISOR_SHA and sha(TESTS/'controller/scope_control.py')==SCOPE_SHA
    control=load(TESTS/'controller/scope_control.py','staged_owned_ci_scope');scope=control.read_scope()
    helper=load(TESTS/'support.py','ipc_verified_fixture');catalog=json.loads((SOURCE/'source-profile.json').read_bytes())
    inputs={'source:'+name:helper.source_path(name) for name in catalog['sources']}
    for name,file in inputs.items(): assert sha(file)==catalog['sources'][name.removeprefix('source:')]
    inputs.update({name:HERE/name for name in ('run_gate.py','worker.py','ci_gate.py','verify_receipt.py')})
    inputs['staged_ipc.py']=SOURCE/'staged/staged_ipc.py';inputs['draft']=SOURCE/'staged/source-profile.json'
    inputs['catalogue']=SOURCE/'source-profile.json';inputs['cold_metadata']=SOURCE/'cold/cold_host.py'
    inputs['supervisor']=TESTS/'controller/ci_gate.py';inputs['scope']=TESTS/'controller/scope_control.py'
    draft=json.loads(inputs['draft'].read_bytes())
    assert draft['status']=='DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED' and draft['operator_approval'] is None
    assert draft['admitted_operations']==[] and draft['sources']=={'staged_ipc.py':sha(inputs['staged_ipc.py'])}
    inputs['fixture']=TESTS/'durable/crash_worker.py';inputs['helper']=TESTS/'support.py'
    before={name:sha(file) for name,file in inputs.items()};before.update(helper.input_hashes())
    started=time.monotonic();result=Result();issues=[];count=0
    with tempfile.TemporaryDirectory(prefix='p-',dir=scope['work']) as directory:
        ROOT=Path(directory).resolve();assert ROOT.parent==Path(scope['work']) and ROOT.name.startswith('p-');ROOT.chmod(0o755)
        for name in ('candidate','codec','store','ownership','frozen','deps','data'): (ROOT/name).mkdir(mode=0o755)
        for name in catalog['sources']:
            target=ROOT/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(helper.source_path(name),target)
        shutil.copyfile(inputs['staged_ipc.py'],ROOT/'candidate/staged_ipc.py')
        shutil.copyfile(HERE/'worker.py',ROOT/'candidate/worker.py')
        shutil.copyfile(TESTS/'durable/crash_worker.py',ROOT/'candidate/crash_worker.py')
        helper.unpack_verified_wheels(ROOT/'deps')
        for path in ROOT.rglob('*'): path.chmod(0o755 if path.is_dir() else 0o644)
        sys.path[:0]=[str(ROOT/name) for name in ('candidate','codec','store','ownership','frozen','deps')]
        try:
            FIXTURE=__import__('crash_worker')
            loader=unittest.TestLoader();suite=loader.loadTestsFromTestCase(NativeTests);count=suite.countTestCases()
            assert not loader.errors and count==30
            suite.run(result)
        except BaseException as error: issues.append(type(error).__name__)
        remaining=[p.name for p in Path('/proc').iterdir() if p.name.isdigit() and int(p.name)!=os.getpid()]
    after={name:sha(file) for name,file in inputs.items()};after.update(helper.input_hashes())
    passed=(count==result.testsRun==30 and result.wasSuccessful() and not result.skipped and not result.expectedFailures
        and not result.unexpectedSuccesses and not issues and not remaining and before==after and not ROOT.exists())
    report=dict(format='pult-staged-ipc-native-proof-v1',outcome='PASS' if passed else 'FAIL',full_gate=True,
        scope='LOCAL_SYNTHETIC_AUTHENTICATED_IPC_NOT_LIVE_S3_HOST_ENTRY_OR_OUTER_WRITE_STOP',tests=result.testsRun,discovered=count,
        errors=len(result.errors),failures=len(result.failures),skips=len(result.skipped),
        expected_failures=len(result.expectedFailures),unexpected_successes=len(result.unexpectedSuccesses),
        errors_detail=[(test._testMethodName,value) for test,value in result.errors],
        failures_detail=[(test._testMethodName,value) for test,value in result.failures],issues=issues,
        source_hashes_before=before,source_hashes_after=after,sources_unchanged=before==after,
        private_pid_namespace=True,remaining_owned_processes=remaining,native_test_tree_removed=not ROOT.exists(),
        elapsed_seconds=round(time.monotonic()-started,3),new_paid_resources=0,additional_spend_rub=0)
    file=HERE/('RUN_'+str(time.time_ns())+'.json')
    with file.open('x') as output: json.dump(report,output,sort_keys=True,indent=2);output.write('\n')
    print(json.dumps({k:report[k] for k in ('outcome','tests','errors','failures','skips','issues','elapsed_seconds','sources_unchanged')}),flush=True)
    print('RECEIPT '+str(file),flush=True);return 0 if passed else 1

if __name__=='__main__': raise SystemExit(main())
