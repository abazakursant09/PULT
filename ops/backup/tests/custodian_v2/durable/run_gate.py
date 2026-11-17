"""Root test orchestrator; isolated nonroot workers, synthetic data, no install."""
import sys
sys.dont_write_bytecode=True
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import grp
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parent
if len(sys.argv)==3 and sys.argv[1]=='--child':
    # The copied child runs below /tmp, not below the repository hierarchy.
    # Repository-only source maps are not read by child_main.
    SOURCE=ROOT.parent
    REPO=ROOT.parent
else:
    SOURCE=ROOT.parents[2]/'custodian_v2'
    REPO=ROOT.parents[4]
UID=47003
GID=47004
def frozen_source(name):
    return SOURCE/'frozen'/name if name=='ops/backup/erasure_journal_s3.py' else REPO/name
CODEC=SOURCE/'codec/event_codec.py'
STORE=SOURCE/'store/event_store.py'
HELPER=ROOT.parent/'support.py'
OWNED=SOURCE/'ownership'
PINS={
 'codec':'c33ad596b91fe927df85fca61ae32d35bb1cf3a1d02da2b09dadb69fc361d04c',
 'store':'d8fe11c109c7779522049428e3d95da40c026aea3bf8b42d1799a6f622e031c0',
 'helper':'d343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d',
 'owner':'a9d71db50be14bd9d57c2535748907a26bc75eabfe3af9d8e203f77a6f434bc3',
 'host':'da3754a860495a5c193daaf27cde3c04b95c55fc58d37dea60af37a58626e1e8',
 'ops/backup/erasure_anchor.py':'b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8',
 'ops/backup/erasure_journal.py':'94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98',
 'ops/backup/erasure_journal_s3.py':'2886ec08fe17eb409d8d4a8c597015454c21f3ef703c674a7848f30b63bc544f',
 'ops/canary/canary.py':'aa4b357acc6e908341c5523db8f93a32fdf3c0305a7ddc36c4c68842fbe3ee4f',
 'ops/backup/erasure_custodian.py':'3eb793a1ef1002c6def97b52c579b2ff051e834b44b802a75aca09d640a691d3',
 'ops/backup/erasure_custodian_host.py':'d7bdd8a858417ea20666bdb6fe5840ead124dec2a98d98ebb6e0d74eedfcdb24',
 'ops/backup/custodian/requirements.lock':'cd1f0a681aae91a132427b88897782b0f2085c8a415cd39812f8e7a059361f08',
}
SOURCES={name:ROOT/name for name in ('anchor_v2.py','protocol_v2.py','test_anchor_v2.py','crash_worker.py','run_gate.py')}
SOURCES.update({name:SOURCE/'candidate'/name for name in ('anchor_v2.py','protocol_v2.py')})
SOURCES.update(codec=CODEC,store=STORE,helper=HELPER,owner=OWNED/'owned_host.py',host=OWNED/'host_core.py')
SOURCES.update({name:frozen_source(name) for name in PINS if name.startswith('ops/')})

def hashes(): return {name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in SOURCES.items()}

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module

def child_main(root):
    # This is a test-only entry, not a service/profile activation interface.
    assert os.getuid()==UID and os.geteuid()==UID and os.getgid()==GID
    sys.path[:0]=[str(root/name) for name in ('candidate','codec','store','ownership','frozen','deps')]
    class Result(unittest.TestResult):
        def startTest(self,test):
            super().startTest(test)
            self.started=time.monotonic()
            print('TEST_STARTED '+test._testMethodName,file=sys.__stdout__,flush=True)
        def _exc_info_to_string(self,error,test): return 'details_suppressed'
        def addSkip(self,test,reason): super().addSkip(test,'reason_suppressed')
        def stopTest(self,test):
            super().stopTest(test)
            print('TEST_FINISHED '+str(self.testsRun)+' '+test._testMethodName+' '+str(round(time.monotonic()-self.started,3)),file=sys.__stdout__,flush=True)
    result=Result();suite_completed=False;count=0;socket_attempts=[];issues=[]
    import socket
    saved=[]
    def deny(*args,**kwargs): socket_attempts.append(True);raise RuntimeError('network forbidden')
    try:
        for target,name in ((socket.socket,'connect'),(socket.socket,'connect_ex'),(socket,'create_connection'),(socket,'getaddrinfo')):
            saved.append((target,name,getattr(target,name)));setattr(target,name,deny)
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            for package in ('httpx','httpcore','anyio','certifi','h11','idna','typing_extensions'):
                module=__import__(package)
                assert Path(module.__file__).resolve().is_relative_to(root/'deps')
            module=__import__('test_anchor_v2')
            loader=unittest.TestLoader();suite=loader.loadTestsFromModule(module);count=suite.countTestCases()
            assert not loader.errors and count==32
            suite.run(result);suite_completed=True
    except BaseException: issues.append('child_setup_or_execution_failed_details_suppressed')
    finally:
        for target,name,original in reversed(saved): setattr(target,name,original)
    def names(rows):
        return [getattr(getattr(test,'test_case',test),'_testMethodName','suite_fixture') for test,_ in rows]
    report={'tests':result.testsRun,'discovered':count,'errors':len(result.errors),'failures':len(result.failures),
        'skips':len(result.skipped),'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),
        'error_tests':names(result.errors),'failure_tests':names(result.failures),'suite_completed':suite_completed,
        'issues':issues,'socket_attempts':len(socket_attempts),'uid':os.getuid(),'gid':os.getgid()}
    report['success']=(not issues and suite_completed and result.testsRun==count and count==32 and result.wasSuccessful()
        and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses and not socket_attempts)
    with (root/'data/child_result.json').open('x') as output: json.dump(report,output,sort_keys=True,indent=2)
    return 0 if report['success'] else 1

def main():
    if sys.argv[1:]!=['--reviewed-by-main']:
        print('{"outcome":"NOT_RUN","reason":"main_review_required"}');return 2
    assert sys.platform=='linux' and os.geteuid()==0 and sys.version_info[:2]==(3,12)
    assert os.getpid()==1  # private PID namespace kills any orphan test worker on exit
    assert [line.split(':',1)[0].strip() for line in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']
    try: pwd.getpwuid(UID)
    except KeyError: pass
    else: raise RuntimeError('synthetic UID conflict')
    try: grp.getgrgid(GID)
    except KeyError: pass
    else: raise RuntimeError('synthetic GID conflict')
    before=hashes();assert all(before[name]==digest for name,digest in PINS.items())
    helper=load(HELPER,'verified_v2_wheel_fixture')
    before.update(helper.input_hashes())
    started=time.monotonic();child_record=None;exit_code=None;wheels=[];temporary_clean=False
    with tempfile.TemporaryDirectory(prefix='pult-v2-anchor-',dir='/tmp') as directory:
        root=Path(directory).resolve();assert root.parent==Path('/tmp');root.chmod(0o755)
        for name in ('candidate','codec','store','ownership','frozen','deps','data'):
            (root/name).mkdir(mode=0o755)
        for name in ('anchor_v2.py','protocol_v2.py','test_anchor_v2.py','crash_worker.py','run_gate.py'):
            shutil.copyfile(SOURCES[name],root/'candidate'/name)
        shutil.copyfile(CODEC,root/'codec/event_codec.py');shutil.copyfile(STORE,root/'store/event_store.py')
        for name in ('owned_host.py','host_core.py'): shutil.copyfile(OWNED/name,root/'ownership'/name)
        for name in PINS:
            if name.startswith('ops/'):
                target=root/'frozen'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(frozen_source(name),target)
        snapshot={name:root/'candidate'/name for name in ('anchor_v2.py','protocol_v2.py','test_anchor_v2.py','crash_worker.py','run_gate.py')}
        snapshot.update(codec=root/'codec/event_codec.py',store=root/'store/event_store.py',
            owner=root/'ownership/owned_host.py',host=root/'ownership/host_core.py')
        snapshot.update({name:root/'frozen'/name for name in PINS if name.startswith('ops/')})
        assert all(hashlib.sha256(path.read_bytes()).hexdigest()==before[name] for name,path in snapshot.items())
        wheels=helper.unpack_verified_wheels(root/'deps');assert len(wheels)==7
        for path in root.rglob('*'): path.chmod(0o755 if path.is_dir() else 0o644)
        (root/'data').chmod(0o700);os.chown(root/'data',UID,GID)
        child=subprocess.Popen((sys.executable,'-I','-S','-B',str(root/'candidate/run_gate.py'),'--child',str(root)),
            user=UID,group=GID,extra_groups=[],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},start_new_session=True)
        try:
            # A full 512-event history repeatedly validates every prior receipt.
            # This is a bounded correctness gate, not a production latency SLO.
            stdout,stderr=child.communicate(timeout=600);exit_code=child.returncode
        except subprocess.TimeoutExpired:
            # This group belongs only to this still-live directly owned test child.
            if child.poll() is None: os.killpg(child.pid,signal.SIGKILL)
            stdout,stderr=child.communicate(timeout=10);exit_code=child.returncode
        result_file=root/'data/child_result.json'
        if result_file.is_file() and result_file.stat().st_size<65536:
            child_record=json.loads(result_file.read_bytes())
        # No raw stdout/stderr/exception bodies are forwarded.
        progress=sum(line.startswith(b'TEST_FINISHED ') for line in stdout.splitlines())
        started_tests=[line.split()[1].decode('ascii') for line in stdout.splitlines()
            if line.startswith(b'TEST_STARTED test_') and len(line.split())==2]
        timings=[line.decode('ascii') for line in stdout.splitlines()
            if line.startswith(b'TEST_FINISHED ') and len(line.split())==4]
        print('CHILD_FINISHED exit='+str(exit_code)+' completed_notices='+str(progress),flush=True)
        remaining=[]
        for process in Path('/proc').iterdir():
            if not process.name.isdigit() or int(process.name)==os.getpid(): continue
            try: command=(process/'cmdline').read_bytes().split(b'\0')
            except (FileNotFoundError,ProcessLookupError): continue
            if any(str(root).encode() in part for part in command): remaining.append(int(process.name))
        if remaining:
            # All PIDs are in the private namespace; only our exact snapshot paths match.
            for pid in remaining:
                try: os.kill(pid,signal.SIGKILL)
                except ProcessLookupError: pass
    temporary_clean=not root.exists()
    after=hashes();after.update(helper.input_hashes());stable=before==after
    success=(exit_code==0 and child_record is not None and child_record.get('success') is True
        and not remaining and stable and temporary_clean)
    report={'format':'pult-v2-anchor-local-proof-v1','outcome':'PASS' if success else 'FAIL',
        'proof_scope':'NATIVE_SYNTHETIC_DURABLE_PROTOCOL_NOT_LIVE_S3_IPC_OR_PLACEMENT',
        'child':child_record,'observed_child_exit':exit_code,'elapsed_seconds':round(time.monotonic()-started,3),
        'last_started_test':started_tests[-1] if started_tests else None,'test_timings':timings,
        'source_hashes_before':before,'source_hashes_after':after,'sources_unchanged':stable,
        'remaining_owned_worker_count_before_cleanup':len(remaining),'private_pid_namespace':True,
        'dependency_wheels':wheels,'native_test_tree_removed':temporary_clean,'new_paid_resources':0,'additional_spend_rub':0}
    target=ROOT/('RUN_'+str(time.time_ns())+'.json')
    with target.open('x') as output: json.dump(report,output,sort_keys=True,indent=2);output.write('\n')
    print(json.dumps({'outcome':report['outcome'],'report':str(target),'child':child_record,
        'sources_unchanged':stable,'elapsed_seconds':report['elapsed_seconds']},sort_keys=True),flush=True)
    return 0 if success else 1

if __name__=='__main__':
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_test_runtime_refused"}');raise SystemExit(2)
    if len(sys.argv)==3 and sys.argv[1]=='--child': raise SystemExit(child_main(Path(sys.argv[2]).resolve()))
    raise SystemExit(main())
