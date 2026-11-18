"""Bounded staged-IPC proof using the unchanged reviewed CI lifecycle library."""
import sys
sys.dont_write_bytecode=True
import hashlib
import json
import os
from pathlib import Path
import re
import signal
from types import ModuleType

ROOT=Path(__file__).resolve().parent
PINS={'ci_gate.py':'d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b',
 'scope_control.py':'d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'}

def captured(file,wanted,name):
    raw=file.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=wanted: raise RuntimeError('FIXED_CI_LIBRARY_REFUSED')
    module=ModuleType(name);module.__file__=str(file);sys.modules[name]=module
    exec(compile(raw,str(file),'exec'),module.__dict__);return module

def verifier():
    raw=(ROOT/'verify_receipt.py').read_bytes();module=ModuleType('staged_independent_receipt')
    module.__file__=str(ROOT/'verify_receipt.py');exec(compile(raw,module.__file__,'exec'),module.__dict__);return module

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_staged_ci_refused"}');return 2
    if (sys.argv[1:]!=['--reviewed-by-main'] or sys.platform!='linux' or os.geteuid()!=0
        or sys.version_info[:2]!=(3,12) or not sys.flags.isolated or not sys.flags.no_site): return 2
    before={file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in ROOT.glob('*.py')}
    library=ROOT.parent/'controller'
    control=captured(library/'scope_control.py',PINS['scope_control.py'],'staged_fixed_scope')
    supervisor=captured(library/'ci_gate.py',PINS['ci_gate.py'],'staged_fixed_supervisor')
    def cancelled(signum,frame): raise KeyboardInterrupt
    previous=signal.signal(signal.SIGTERM,cancelled)
    try:
        with supervisor.create_scope(control) as scope:
            result,clean,reason=supervisor.supervised_run(scope,control,ROOT/'run_gate.py')
    except (Exception,KeyboardInterrupt,SystemExit):
        print('{"outcome":"FAIL","reason":"staged_supervision_incomplete"}');return 1
    finally: signal.signal(signal.SIGTERM,previous)
    if reason or not clean or result is None:
        print(json.dumps(dict(outcome='FAIL',reason=reason or 'staged_run_incomplete',owned_resources_empty=clean)));return 1
    print(result.stdout,end='',flush=True)
    if result.returncode or result.stderr: return 1
    receipts=[line.removeprefix('RECEIPT ') for line in result.stdout.splitlines() if line.startswith('RECEIPT ')]
    if len(receipts)!=1: return 1
    file=Path(receipts[0])
    if file.parent.resolve()!=ROOT or not re.fullmatch('RUN_[0-9]+\\.json',file.name): return 1
    if before!={file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in ROOT.glob('*.py')}: return 1
    try: receipt_sha=verifier().verify(file)
    except Exception:
        print('{"outcome":"FAIL","reason":"staged_independent_postcheck_failed"}');return 1
    print(json.dumps(dict(format='pult-staged-ipc-supervised-proof-v1',outcome='PASS',tests=30,full_gate=True,
        independent_postcheck=True,owned_resources_empty=True,receipt=file.name,receipt_sha256=receipt_sha,
        deployment_admitted=False,remote_io_admitted=False,new_paid_resources=0)),flush=True);return 0

if __name__=='__main__': raise SystemExit(main())
