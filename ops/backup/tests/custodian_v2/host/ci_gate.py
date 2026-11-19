"""Bounded source-host_construction proof; unchanged reviewed controller CI lifecycle."""
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
    file=ROOT/'verify_receipt.py';raw=file.read_bytes();module=ModuleType('independent_host_construction_receipt')
    module.__file__=str(file);exec(compile(raw,str(file),'exec'),module.__dict__);return module

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_host_construction_ci_refused"}');return 2
    if (sys.argv[1:]!=['--reviewed-by-main'] or sys.platform!='linux' or os.geteuid()!=0
        or sys.version_info[:2]!=(3,12) or not sys.flags.isolated or not sys.flags.no_site): return 2
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')}
    library=ROOT.parent/'controller'
    control=captured(library/'scope_control.py',PINS['scope_control.py'],'host_construction_fixed_scope')
    supervisor=captured(library/'ci_gate.py',PINS['ci_gate.py'],'host_construction_fixed_supervisor')
    def cancelled(signum,frame): raise KeyboardInterrupt
    previous=signal.signal(signal.SIGTERM,cancelled)
    try:
        with supervisor.create_scope(control) as scope:
            result,clean,reason=supervisor.supervised_run(scope,control,ROOT/'run_gate.py')
    except (Exception,KeyboardInterrupt,SystemExit):
        print('{"outcome":"FAIL","reason":"host_construction_supervision_incomplete"}');return 1
    finally: signal.signal(signal.SIGTERM,previous)
    if reason or not clean or result is None:
        print(json.dumps(dict(outcome='FAIL',reason=reason or 'host_construction_run_incomplete',owned_resources_empty=clean)));return 1
    print(result.stdout,end='',flush=True)
    if result.returncode or result.stderr: return 1
    rows=[line.removeprefix('RECEIPT ') for line in result.stdout.splitlines() if line.startswith('RECEIPT ')]
    if len(rows)!=1: return 1
    file=Path(rows[0])
    if file.parent.resolve()!=ROOT or not re.fullmatch('RUN_[0-9]+\\.json',file.name): return 1
    if before!={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')}: return 1
    try: digest=verifier().verify(file)
    except Exception:
        print('{"outcome":"FAIL","reason":"host_construction_independent_postcheck_failed"}');return 1
    print(json.dumps(dict(format='pult-staged-host-construction-supervised-proof-v1',outcome='PASS',tests=60,full_gate=True,
        independent_postcheck=True,owned_resources_empty=True,receipt=file.name,receipt_sha256=digest,
        host_write_admitted=False,listener_admitted=False,outer_write_stop_bound=False,deployment_admitted=False,remote_io_admitted=False,new_paid_resources=0)),flush=True);return 0

if __name__=='__main__': raise SystemExit(main())
