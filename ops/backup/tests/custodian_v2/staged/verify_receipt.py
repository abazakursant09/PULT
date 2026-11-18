"""Independent read-only receipt/source/resource check, never a runtime authority."""
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[4]
FIELDS={'format','outcome','full_gate','scope','tests','discovered','errors','failures','skips','expected_failures',
 'unexpected_successes','errors_detail','failures_detail','issues','source_hashes_before','source_hashes_after',
 'sources_unchanged','private_pid_namespace','remaining_owned_processes','native_test_tree_removed',
 'elapsed_seconds','new_paid_resources','additional_spend_rub'}

def require(value):
    if not value: raise ValueError('STAGED_RECEIPT_REFUSED')

def valid_summary(value):
    try:
        require(type(value) is dict and set(value)==FIELDS and value['format']=='pult-staged-ipc-native-proof-v1')
        require(value['outcome']=='PASS' and value['scope']=='LOCAL_SYNTHETIC_AUTHENTICATED_IPC_NOT_LIVE_S3_HOST_ENTRY_OR_OUTER_WRITE_STOP')
        for name in ('full_gate','sources_unchanged','private_pid_namespace','native_test_tree_removed'): require(value[name] is True)
        for name in ('tests','discovered'): require(type(value[name]) is int and value[name]==30)
        for name in ('errors','failures','skips','expected_failures','unexpected_successes','new_paid_resources','additional_spend_rub'):
            require(type(value[name]) is int and value[name]==0)
        for name in ('errors_detail','failures_detail','issues','remaining_owned_processes'): require(value[name]==[])
        elapsed=value['elapsed_seconds'];require(type(elapsed) in (int,float) and math.isfinite(elapsed) and 0<=elapsed<=900)
        before=value['source_hashes_before'];require(type(before) is dict and bool(before) and before==value['source_hashes_after'])
        require(all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for k,v in before.items()))
        return True
    except (KeyError,TypeError,ValueError,OverflowError): return False

def expected_paths():
    source=REPO/'ops/backup/custodian_v2';tests=ROOT.parent
    require(hashlib.sha256((source/'source-profile.json').read_bytes()).hexdigest()=='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3')
    catalogue=json.loads((source/'source-profile.json').read_bytes());paths={}
    require(hashlib.sha256((source/'cold/cold_host.py').read_bytes()).hexdigest()=='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c')
    for name,wanted in catalogue['sources'].items():
        file=REPO/name.removeprefix('frozen/') if name.startswith('frozen/') and name!='frozen/ops/backup/erasure_journal_s3.py' else source/name
        require(hashlib.sha256(file.read_bytes()).hexdigest()==wanted);paths['source:'+name]=file
    paths.update({name:ROOT/name for name in ('run_gate.py','worker.py','ci_gate.py','verify_receipt.py')})
    paths.update({'staged_ipc.py':source/'staged/staged_ipc.py','draft':source/'staged/source-profile.json',
        'catalogue':source/'source-profile.json','cold_metadata':source/'cold/cold_host.py',
        'fixture':tests/'durable/crash_worker.py','helper':tests/'support.py',
        'supervisor':tests/'controller/ci_gate.py','scope':tests/'controller/scope_control.py'})
    tree=ast.parse((source/'cold/cold_host.py').read_bytes())
    wheels=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='WHEELS' for t in n.targets))
    require(len(wheels)==7)
    for name,wanted in wheels.items():
        file=tests/'wheels'/name;require(hashlib.sha256(file.read_bytes()).hexdigest()==wanted);paths['wheel:'+name]=file
    return paths

def verify(file):
    require(sys.platform=='linux' and os.geteuid()==0)
    file=Path(file);require(file.parent.resolve()==ROOT and re.fullmatch('RUN_[0-9]+\\.json',file.name))
    info=file.lstat();require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and 0<info.st_size<1048576)
    raw=file.read_bytes();value=json.loads(raw);require(valid_summary(value));paths=expected_paths()
    require(len(paths)==32 and set(paths)==set(value['source_hashes_before']))
    for name,path in paths.items(): require(hashlib.sha256(path.read_bytes()).hexdigest()==value['source_hashes_before'][name])
    for directory,pattern in (('/run','pult-controller-scope-*'),('/tmp','pult-cc-*')):
        require(not list(Path(directory).glob(pattern)))
    inventory=subprocess.run(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend',
        'pult-controller-ci-run-*','pult-call-gate-*'],capture_output=True,timeout=10)
    require(inventory.returncode==0 and not inventory.stdout.strip() and not inventory.stderr)
    return hashlib.sha256(raw).hexdigest()

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_staged_verifier_refused"}');return 2
    if len(sys.argv)!=2: return 2
    try: result=verify(Path(sys.argv[1]))
    except Exception:
        print('{"outcome":"REFUSED","reason":"staged_receipt_verification_failed"}');return 1
    print('STAGED_FULL30_INPUTS_REHASHED_OWN_RESOURCES_EMPTY '+result);return 0

if __name__=='__main__': raise SystemExit(main())
