"""Private native root test orchestration; child cold entries use synthetic UID."""
import sys
sys.dont_write_bytecode=True
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
import tarfile
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT.parents[2]/'custodian_v2'
REPO=ROOT.parents[4]
CATALOG=SOURCE/'source-profile.json'
CATALOG_SHA='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'
HELPER=ROOT.parent/'support.py'
HELPER_SHA='d343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d'
ORIGINS={
 'candidate':SOURCE/'candidate',
 'codec':SOURCE/'codec',
 'store':SOURCE/'store',
 'ownership':SOURCE/'ownership',
 'frozen':REPO,
}

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

class Result(unittest.TestResult):
    def _exc_info_to_string(self,error,test): return 'details_suppressed'
    def addSkip(self,test,reason): super().addSkip(test,'reason_suppressed')
    def stopTest(self,test):
        super().stopTest(test);print('TEST_FINISHED '+str(self.testsRun),flush=True)

def main():
    if sys.argv[1:]!=['--reviewed-by-main']:
        print('{"outcome":"NOT_RUN","reason":"main_review_required"}');return 2
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_test_runtime_refused"}');return 2
    assert sys.platform=='linux' and sys.version_info[:2]==(3,12) and os.geteuid()==0 and os.getpid()==1
    assert [line.split(':',1)[0].strip() for line in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']
    for database,identity in ((pwd.getpwuid,47003),(grp.getgrgid,47004)):
        try: database(identity)
        except KeyError: pass
        else: raise RuntimeError('synthetic identity conflict')
    assert sha(CATALOG)==CATALOG_SHA and sha(HELPER)==HELPER_SHA
    catalogue=json.loads(CATALOG.read_bytes());assert len(catalogue['sources'])==13
    files={name:ROOT/name for name in ('cold_host.py','cold_worker.py','test_cold_host.py','run_gate.py','SCOPE.txt')}
    files['cold_host.py']=SOURCE/'cold/cold_host.py'
    files.pop('SCOPE.txt')
    files.update(catalogue=CATALOG,helper=HELPER)
    for name,digest in catalogue['sources'].items():
        prefix,relative=name.split('/',1);source=ORIGINS[prefix]/relative
        if name=='frozen/ops/backup/erasure_journal_s3.py': source=SOURCE/name
        assert sha(source)==digest;files['source:'+name]=source
    before={name:sha(path) for name,path in files.items()}
    helper=load(HELPER,'verified_cold_wheel_fixture')
    before.update(helper.input_hashes())
    started=time.monotonic();result=Result();count=0;completed=False;issues=[];remaining=[];wheels=[];diagnostics=[]
    with tempfile.TemporaryDirectory(prefix='pult-schema2-cold-',dir='/tmp') as directory:
        base=Path(directory).resolve();assert base.parent==Path('/tmp');base.chmod(0o755)
        template=base/'template';release=template/'release'
        release.mkdir(parents=True);(template/'entry').mkdir()
        for name in catalogue['sources']:
            target=release/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(files['source:'+name],target);assert sha(target)==catalogue['sources'][name]
        shutil.copyfile(CATALOG,release/'source-profile.json')
        shutil.copyfile(files['cold_host.py'],template/'entry/cold_host.py')
        assert sha(template/'entry/cold_host.py')==before['cold_host.py']
        (release/'deps').mkdir();(release/'wheels').mkdir()
        wheels=helper.unpack_verified_wheels(release/'deps');assert len(wheels)==7
        for wheel in wheels:
            wheel_body=(helper.WHEEL_DIR/wheel['wheel']).read_bytes()
            assert hashlib.sha256(wheel_body).hexdigest()==wheel['sha256']
            (release/'wheels'/wheel['wheel']).write_bytes(wheel_body)
        worker=base/'cold_worker.py';shutil.copyfile(ROOT/'cold_worker.py',worker)
        assert sha(worker)==before['cold_worker.py']
        for path in base.rglob('*'): path.chmod(0o755 if path.is_dir() else 0o644)
        try:
            tests=load(ROOT/'test_cold_host.py','cold_host_reviewed_tests')
            tests.configure(base,template,worker)
            loader=unittest.TestLoader();suite=loader.loadTestsFromModule(tests);count=suite.countTestCases()
            assert not loader.errors and count==21
            suite.run(result);completed=True
            diagnostics=tests.DIAGNOSTIC_FAILURES[:4]
        except BaseException: issues.append('test_load_or_execution_error_details_suppressed')
        for process in Path('/proc').iterdir():
            if not process.name.isdigit() or int(process.name)==os.getpid(): continue
            try: command=(process/'cmdline').read_bytes().split(b'\0')
            except (FileNotFoundError,ProcessLookupError): continue
            if any(str(base).encode() in item for item in command): remaining.append(int(process.name))
        for pid in remaining:
            try:
                fd=os.pidfd_open(pid);signal.pidfd_send_signal(fd,signal.SIGKILL);os.close(fd)
                os.waitpid(pid,0)
            except (ChildProcessError,ProcessLookupError): pass
    after={name:sha(path) for name,path in files.items()};after.update(helper.input_hashes());stable=before==after
    def names(rows):
        return [getattr(getattr(test,'test_case',test),'_testMethodName','suite_fixture') for test,_ in rows]
    success=(not issues and completed and count==result.testsRun and count==21 and result.wasSuccessful()
        and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
        and not remaining and stable and not base.exists())
    report={'format':'pult-schema2-cold-host-proof-v1','outcome':'PASS' if success else 'FAIL',
        'proof_scope':'LOCAL_SYNTHETIC_COLD_OFFLINE_CURRENT_ONLY_NOT_DEPLOYMENT_OR_NETWORK',
        'tests':result.testsRun,'discovered':count,'errors':len(result.errors),'failures':len(result.failures),
        'skips':len(result.skipped),'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),
        'error_tests':names(result.errors),'failure_tests':names(result.failures),'issues':issues,
        'suite_completed':completed,'source_hashes_before':before,'source_hashes_after':after,'sources_unchanged':stable,
        'verified_dependency_wheels':wheels,'private_pid_and_net_namespace':True,
        'remaining_owned_workers_before_cleanup':len(remaining),'native_test_tree_removed':not base.exists(),
        'elapsed_seconds':round(time.monotonic()-started,3),'new_paid_resources':0,'additional_spend_rub':0,
        'failure_details':'omitted; no credentials/payload/raw exception or subtest values exported'}
    report['diagnostic_failure_metrics']=diagnostics
    target=ROOT/('RUN_'+str(time.time_ns())+'.json')
    with target.open('x') as output: json.dump(report,output,sort_keys=True,indent=2);output.write('\n')
    print(json.dumps({key:report[key] for key in ('outcome','tests','errors','failures','skips','error_tests','failure_tests','issues','sources_unchanged','elapsed_seconds')},sort_keys=True),flush=True)
    print('RECEIPT '+str(target),flush=True)
    return 0 if success else 1

if __name__=='__main__': raise SystemExit(main())
