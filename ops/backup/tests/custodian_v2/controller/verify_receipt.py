"""Read-only proof receipt checker; never an operation/recovery authority."""
import ast
import hashlib
import json
import math
import os
from pathlib import Path,PurePosixPath
import re
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2'
COLD_SHA='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'
CATALOG_SHA='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'
NATIVE_FILES=('fixture_joint.py','fixture_v2.py','joint_worker.py','owner_worker.py',
 'proof_repeat.py','test_repeat.py','run_gate.py','verify_receipt.py','ci_gate.py','scope_control.py','supervision_proof.py')
SUMMARY_FIELDS={'format','outcome','full_gate','scope','tests','discovered','errors','failures','skips',
 'expected_failures','unexpected_successes','errors_detail','failures_detail','issues','source_hashes_before',
 'source_hashes_after','sources_unchanged','units_after_empty','native_test_tree_removed','elapsed_seconds',
 'new_paid_resources','additional_spend_rub'}

class InvalidReceipt(Exception): pass

def require(value):
    if not value: raise InvalidReceipt('receipt verification refused')

def valid_summary(report):
    try:
        require(type(report) is dict and set(report)==SUMMARY_FIELDS and report['format']=='pult-controller-ci-native-proof-v1')
        require(report['outcome']=='PASS' and report['full_gate'] is True)
        require(report['scope']=='LOCAL_MOCK_ONLY_ONE_LIVE_CONTROLLER_NOT_RESTART_DEPLOYMENT_OR_REMOTE_FRESHNESS')
        for name in ('new_paid_resources','additional_spend_rub'): require(type(report[name]) is int and report[name]==0)
        elapsed=report['elapsed_seconds'];require(type(elapsed) in (int,float) and math.isfinite(elapsed) and 0<=elapsed<=900)
        require(type(report['tests']) is int and type(report['discovered']) is int and report['tests']==report['discovered']==52)
        for name in ('errors','failures','skips','expected_failures','unexpected_successes'):
            require(type(report[name]) is int and report[name]==0)
        for name in ('issues','errors_detail','failures_detail'): require(report[name]==[])
        for name in ('sources_unchanged','units_after_empty','native_test_tree_removed'): require(report[name] is True)
        before=report['source_hashes_before'];after=report['source_hashes_after']
        require(type(before) is dict and bool(before) and before==after)
        require(all(type(name) is str and type(value) is str and re.fullmatch('[0-9a-f]{64}',value) for name,value in before.items()))
        return True
    except (InvalidReceipt,KeyError,TypeError,ValueError): return False

def digest(file): return hashlib.sha256(file.read_bytes()).hexdigest()

def expected_paths():
    require(digest(SOURCE/'source-profile.json')==CATALOG_SHA and digest(SOURCE/'cold/cold_host.py')==COLD_SHA)
    catalogue=json.loads((SOURCE/'source-profile.json').read_bytes())
    paths={str((ROOT/name).relative_to(REPO)):ROOT/name for name in NATIVE_FILES}
    controller=SOURCE/'controller';draft=json.loads((controller/'source-profile.json').read_bytes())
    for name in draft['sources']:
        require(type(name) is str and not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts)
        file=controller/name;paths[str(file.relative_to(REPO))]=file
    for file in (controller/'source-profile.json',SOURCE/'source-profile.json',SOURCE/'cold/cold_host.py',
            ROOT.parent/'support.py',ROOT.parent/'cold/test_cold_host.py',ROOT.parent/'cold/cold_worker.py'):
        paths[str(file.relative_to(REPO))]=file
    for name in catalogue['sources']:
        if name.startswith('frozen/') and name!='frozen/ops/backup/erasure_journal_s3.py': file=REPO/name.removeprefix('frozen/')
        else: file=SOURCE/name
        paths[str(file.relative_to(REPO))]=file
    tree=ast.parse((SOURCE/'cold/cold_host.py').read_bytes())
    wheels=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
        and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='WHEELS')
    require(type(wheels) is dict and len(wheels)==7)
    for name,wanted in wheels.items():
        file=ROOT.parent/'wheels'/name;require(digest(file)==wanted);paths['wheel:'+name]=file
    return paths

def verify(file):
    require(sys.platform=='linux' and os.geteuid()==0)
    file=Path(file);require(file.parent.resolve()==ROOT and re.fullmatch('RUN_[0-9]+\\.json',file.name))
    info=file.lstat();require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and 0<info.st_size<1048576)
    raw=file.read_bytes();report=json.loads(raw);require(valid_summary(report))
    paths=expected_paths();require(set(paths)==set(report['source_hashes_before']))
    for name,target in paths.items(): require(digest(target)==report['source_hashes_before'][name])
    result=subprocess.run(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend','pult-call-gate-*'],capture_output=True,timeout=10)
    require(result.returncode==0 and not result.stdout.strip() and not result.stderr)
    result=subprocess.run(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend','pult-controller-ci-run-*'],capture_output=True,timeout=10)
    require(result.returncode==0 and not result.stdout.strip() and not result.stderr)
    require(not list(Path('/run/systemd/system').glob('pult-call-gate-*')))
    require(not list(Path('/tmp').glob('pult-controller-ci-*')))
    require(not list(Path('/run').glob('pult-cold-joint-*')))
    require(not list(Path('/tmp').glob('pult-cc-*')))
    require(not list(Path('/run').glob('pult-controller-scope-*')))
    matches=[]
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit() or int(directory.name)==os.getpid(): continue
        try: args=(directory/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError,ProcessLookupError,PermissionError): continue
        if any(arg.startswith((b'/tmp/pult-controller-ci-',b'/tmp/pult-cc-')) for arg in args): matches.append(int(directory.name))
    require(not matches)
    return hashlib.sha256(raw).hexdigest()

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_verifier_refused"}');return 2
    if len(sys.argv)!=2: return 2
    try: result=verify(Path(sys.argv[1]))
    except Exception:
        print('{"outcome":"REFUSED","reason":"receipt_verification_failed"}');return 1
    print('FINAL_52_PASS_INPUTS_REHASHED_OWN_RESOURCES_EMPTY')
    print('RECEIPT_SHA256 '+result);return 0

if __name__=='__main__': raise SystemExit(main())
