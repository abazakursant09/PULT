"""Owned bounded CI supervision, synthetic data only; never installation."""
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
from types import ModuleType

HERE=Path(__file__).resolve().parent
PINS={'ci_gate.py':'d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b',
      'scope_control.py':'d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'}
def capture(file,wanted,name):
    raw=file.read_bytes()
    if wanted is not None and hashlib.sha256(raw).hexdigest()!=wanted:raise RuntimeError('FIXED_ACTION_CI_INPUT_REFUSED')
    m=ModuleType(name);m.__file__=str(file);sys.modules[name]=m
    exec(compile(raw,str(file),'exec',dont_inherit=True,optimize=0),m.__dict__);return m

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_action_ci_refused"}');return 2
    if (sys.argv[1:]!=['--reviewed-by-main'] or sys.platform!='linux' or os.geteuid()!=0
            or sys.version_info[:2]!=(3,12) or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode):return 2
    verifier=capture(HERE/'verify_receipt.py',None,'independent_action_receipt')
    before={k:verifier.digest(p) for k,p in verifier.expected_paths().items()}
    lib=HERE.parent/'controller'
    control=capture(lib/'scope_control.py',PINS['scope_control.py'],'fixed_action_ci_scope')
    supervisor=capture(lib/'ci_gate.py',PINS['ci_gate.py'],'fixed_action_ci_supervisor')
    def cancel(signum,frame):raise KeyboardInterrupt
    previous=signal.signal(signal.SIGTERM,cancel)
    try:
        with supervisor.create_scope(control) as scope:
            result,clean,reason=supervisor.supervised_run(scope,control,HERE/'native_chain.py',timeout=350)
    except (Exception,KeyboardInterrupt,SystemExit):
        print('{"outcome":"FAIL","reason":"action_supervision_incomplete"}');return 1
    finally:signal.signal(signal.SIGTERM,previous)
    if reason or not clean or result is None:
        print(json.dumps(dict(outcome='FAIL',reason=reason or 'action_run_incomplete',owned_resources_empty=clean)));return 1
    print(result.stdout,end='',flush=True)
    if result.returncode or result.stderr:
        print(result.stderr,file=sys.stderr,end='');return 1
    receipts=[r.removeprefix('RECEIPT ') for r in result.stdout.splitlines() if r.startswith('RECEIPT ')]
    if len(receipts)!=1:return 1
    digest=verifier.verify(receipts[0],before,scope)
    print(json.dumps(dict(format='pult-action-ci-completion-v3',outcome='PASS',native_cases=5,
        independent_postcheck=True,owned_resources_empty=True,receipt_sha256=digest,
        purge=False,restore=False,live_provider_iam_tls=False,deployment_admitted=False,new_paid_resources=0),sort_keys=True),flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
