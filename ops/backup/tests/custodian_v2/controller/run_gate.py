"""Repository-native real-systemd synthetic proof; no deployment authority."""
import sys
sys.dont_write_bytecode=True
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import grp
import shutil
import subprocess
import tempfile
import time
import traceback
import unittest

ROOT=Path(__file__).resolve().parent
TESTS=ROOT.parent
SOURCE=ROOT.parents[2]/'custodian_v2'
CONTROLLER=SOURCE/'controller'
REPO=ROOT.parents[4]
CATALOG_SHA='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'
COLD_SHA='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'
HELPER_SHA='d343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d'
COLD_TEST_SHA='ce95c0db5e8c9277ff822d21de8947679f553112c1c768ddf3bf2a97f34c4767'
SOURCES={
 'observer_v2.py':'65462111526213daa1e62b3cbe81e146e8277f7eb5d521b70266f28046972739',
 'supervisor_v2.py':'3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e',
 'repeatable_observer.py':'de0c1caeaa00cabc2e4aa324e595bf6708668d719841f92bf85b3715be107aad',
 'frozen/admission.py':'961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7'}
NATIVE_FILES=('fixture_joint.py','fixture_v2.py','joint_worker.py','owner_worker.py',
 'proof_repeat.py','test_repeat.py','run_gate.py','verify_receipt.py','ci_gate.py','scope_control.py','supervision_proof.py')
WORKER_PINS={
 'joint_worker.py':'2ee178a01ec2631d4891e7a509cb275f3419b78e8ae9f0f69f9b8cb64c7057a8',
 'owner_worker.py':'e9c337673e5c8e08f7f104154d1ebc2b3dbb379d4544a553916db77e546b0ed5',
 'test_repeat.py':'d8e0e07083d1254a73d407d9e8bc30ff0b6c33b88f558d16d5acff874a98cfe7'}

def sha(file): return hashlib.sha256(file.read_bytes()).hexdigest()
def load(file,name):
    spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

def inventory():
    result=subprocess.run(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend','pult-call-gate-*'],capture_output=True,timeout=10)
    if result.returncode or result.stderr: raise RuntimeError('UNIT_INVENTORY_UNAVAILABLE')
    return result.stdout.strip()

class Result(unittest.TestResult):
    def _exc_info_to_string(self,error,test):
        kind,value,tb=error
        return kind.__name__+' '+str([(Path(row.filename).name,row.lineno,row.name) for row in traceback.extract_tb(tb)])
    def addSkip(self,test,reason): super().addSkip(test,'reason_suppressed')
    def startTest(self,test): super().startTest(test);self.started=time.monotonic()
    def stopTest(self,test):
        super().stopTest(test)
        print('TEST_FINISHED '+str(self.testsRun)+' '+test._testMethodName+' '+str(round(time.monotonic()-self.started,3)),flush=True)

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_test_runtime_refused"}');return 2
    if sys.argv[1:]!=['--reviewed-by-main']:
        print('{"outcome":"NOT_RUN","reason":"main_review_required"}');return 2
    if (sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12)
        or os.geteuid()!=0 or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
        or Path('/proc/1/comm').read_text().strip()!='systemd'
        or not Path('/sys/fs/cgroup/cgroup.controllers').exists()):
        print('{"outcome":"NOT_RUN","reason":"native_root_systemd_cgroup_required"}');return 2
    assert [line.split(':',1)[0].strip() for line in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']
    for fn,number in ((pwd.getpwuid,48001),(pwd.getpwuid,48002),(pwd.getpwuid,47003),(grp.getgrgid,48003),(grp.getgrgid,47004)):
        try: fn(number)
        except KeyError: pass
        else: raise RuntimeError('SYNTHETIC_IDENTITY_CONFLICT')
    assert pwd.getpwuid(65534).pw_name=='nobody' and grp.getgrgid(65534).gr_name=='nogroup'
    assert not inventory() and not list(Path('/run/systemd/system').glob('pult-call-gate-*'))
    assert sha(SOURCE/'source-profile.json')==CATALOG_SHA and sha(SOURCE/'cold/cold_host.py')==COLD_SHA
    assert sha(TESTS/'support.py')==HELPER_SHA and sha(TESTS/'cold/test_cold_host.py')==COLD_TEST_SHA
    assert all(sha(CONTROLLER/name)==wanted for name,wanted in SOURCES.items())
    assert all(sha(ROOT/name)==wanted for name,wanted in WORKER_PINS.items())
    draft=json.loads((CONTROLLER/'source-profile.json').read_bytes())
    assert draft['sources']==SOURCES and draft['status']=='DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED'
    assert draft['operator_approval'] is None and draft['admitted_operations']==[]
    helper=load(TESTS/'support.py','controller_verified_wheel_fixture')
    scope_control=load(ROOT/'scope_control.py','native_gate_owned_scope')
    scope=scope_control.read_scope()
    catalogue=json.loads((SOURCE/'source-profile.json').read_bytes())
    files={'ops/backup/tests/custodian_v2/controller/'+name:ROOT/name for name in NATIVE_FILES}
    files.update({str((CONTROLLER/name).relative_to(REPO)):CONTROLLER/name for name in SOURCES})
    for file in (CONTROLLER/'source-profile.json',SOURCE/'source-profile.json',SOURCE/'cold/cold_host.py',
            TESTS/'support.py',TESTS/'cold/test_cold_host.py',TESTS/'cold/cold_worker.py'):
        files[str(file.relative_to(REPO))]=file
    for name,wanted in catalogue['sources'].items():
        file=helper.source_path(name);assert sha(file)==wanted;files[str(file.relative_to(REPO))]=file
    before={name:sha(file) for name,file in files.items()};before.update(helper.input_hashes())
    started=time.monotonic();result=Result();issues=[];count=0;completed=False
    with tempfile.TemporaryDirectory(prefix='native-',dir=scope['work']) as directory:
        base=Path(directory).resolve();assert base.parent==Path(scope['work']);base.chmod(0o755)
        template=base/'template';release=template/'release';release.mkdir(parents=True);(template/'entry').mkdir()
        for name,wanted in catalogue['sources'].items():
            target=release/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(helper.source_path(name),target)
            assert sha(target)==wanted
        shutil.copyfile(SOURCE/'source-profile.json',release/'source-profile.json')
        shutil.copyfile(SOURCE/'cold/cold_host.py',template/'entry/cold_host.py')
        for name in ('observer_v2.py','supervisor_v2.py','repeatable_observer.py'):
            shutil.copyfile(CONTROLLER/name,base/name);assert sha(base/name)==SOURCES[name]
        shutil.copyfile(CONTROLLER/'frozen/admission.py',base/'legacy_admission.py')
        assert sha(base/'legacy_admission.py')==SOURCES['frozen/admission.py']
        for name in ('owner_worker.py','joint_worker.py'): shutil.copyfile(ROOT/name,base/name)
        shutil.copyfile(CONTROLLER/'observer_v2.py',template/'entry/observer_v2.py')
        (template/'entry/observer.sha256').write_text(SOURCES['observer_v2.py']+'\n')
        (release/'deps').mkdir();(release/'wheels').mkdir();helper.unpack_verified_wheels(release/'deps')
        for name,body in helper.wheel_blobs().items(): (release/'wheels'/name).write_bytes(body)
        for file in base.rglob('*'): file.chmod(0o755 if file.is_dir() else 0o644)
        try:
            api=load(ROOT/'proof_repeat.py','proof_repeat');api.configure(base,template)
            tests=load(ROOT/'test_repeat.py','repo_native_controller_tests')
            loader=unittest.TestLoader();suite=loader.loadTestsFromModule(tests);count=suite.countTestCases()
            assert not loader.errors and count==52
            suite.run(result);completed=True
        except BaseException as exc:
            issues.append(type(exc).__name__+' '+str([(Path(row.filename).name,row.lineno,row.name) for row in traceback.extract_tb(exc.__traceback__)]))
        units_after=inventory()
    after={name:sha(file) for name,file in files.items()};after.update(helper.input_hashes())
    success=(completed and count==result.testsRun==52 and result.wasSuccessful() and not result.skipped
        and not result.expectedFailures and not result.unexpectedSuccesses and not issues and not units_after
        and before==after and not base.exists())
    report=dict(format='pult-controller-ci-native-proof-v1',outcome='PASS' if success else 'FAIL',full_gate=True,
        scope='LOCAL_MOCK_ONLY_ONE_LIVE_CONTROLLER_NOT_RESTART_DEPLOYMENT_OR_REMOTE_FRESHNESS',
        tests=result.testsRun,discovered=count,errors=len(result.errors),failures=len(result.failures),skips=len(result.skipped),
        expected_failures=len(result.expectedFailures),unexpected_successes=len(result.unexpectedSuccesses),
        errors_detail=[(test._testMethodName,detail) for test,detail in result.errors],
        failures_detail=[(test._testMethodName,detail) for test,detail in result.failures],issues=issues,
        source_hashes_before=before,source_hashes_after=after,sources_unchanged=before==after,
        units_after_empty=not units_after,native_test_tree_removed=not base.exists(),elapsed_seconds=round(time.monotonic()-started,3),
        new_paid_resources=0,additional_spend_rub=0)
    file=ROOT/('RUN_'+str(time.time_ns())+'.json')
    with file.open('x') as output: json.dump(report,output,sort_keys=True,indent=2);output.write('\n')
    print(json.dumps({name:report[name] for name in ('outcome','full_gate','tests','errors','failures','skips','errors_detail','failures_detail','issues','sources_unchanged','units_after_empty','elapsed_seconds')}),flush=True)
    print('RECEIPT '+str(file),flush=True)
    return 0 if success else 1

if __name__=='__main__': raise SystemExit(main())
