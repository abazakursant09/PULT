"""Bounded construction-stop proof, using the unchanged owned CI supervisor."""
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import os
from pathlib import Path
import re
import signal
from types import ModuleType

ROOT = Path(__file__).resolve().parent
PINS = {'ci_gate.py': 'd30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b',
    'scope_control.py': 'd46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'}

def captured(file, wanted, name):
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != wanted: raise RuntimeError('FIXED_CI_LIBRARY_REFUSED')
    module = ModuleType(name); module.__file__ = str(file); sys.modules[name] = module
    exec(compile(raw, str(file), 'exec', dont_inherit=True), module.__dict__)
    return module

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_stop_binding_ci_refused"}'); return 2
    if (sys.argv[1:] != ['--reviewed-by-main'] or sys.platform != 'linux' or os.geteuid() != 0
        or sys.version_info[:2] != (3, 12) or not sys.flags.isolated or not sys.flags.no_site): return 2
    raw = (ROOT / 'verify_receipt.py').read_bytes()
    verifier = ModuleType('_independent_stop_receipt'); verifier.__file__ = str(ROOT / 'verify_receipt.py')
    exec(compile(raw, verifier.__file__, 'exec', dont_inherit=True), verifier.__dict__)
    before = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in verifier.expected_paths().items()}
    library = ROOT.parent / 'controller'
    control = captured(library / 'scope_control.py', PINS['scope_control.py'], '_stop_fixed_scope')
    supervisor = captured(library / 'ci_gate.py', PINS['ci_gate.py'], '_stop_fixed_supervisor')
    def cancelled(signum, frame): raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, cancelled)
    try:
        with supervisor.create_scope(control) as scope:
            result, clean, reason = supervisor.supervised_run(scope, control, ROOT / 'run_gate.py', timeout=400)
    except (Exception, KeyboardInterrupt, SystemExit):
        print('{"outcome":"FAIL","reason":"stop_binding_supervision_incomplete"}'); return 1
    finally: signal.signal(signal.SIGTERM, previous)
    if reason or not clean or result is None:
        print(json.dumps(dict(outcome='FAIL', reason=reason or 'stop_binding_run_incomplete', owned_resources_empty=clean))); return 1
    print(result.stdout, end='', flush=True)
    if result.returncode or result.stderr:
        print(result.stderr, file=sys.stderr, end=''); return 1
    rows = [row.removeprefix('RECEIPT ') for row in result.stdout.splitlines() if row.startswith('RECEIPT ')]
    if len(rows) != 1: return 1
    receipt = Path(rows[0])
    if receipt.parent.resolve() != ROOT or not re.fullmatch('RUN_[0-9]+\\.json', receipt.name): return 1
    if before != {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in verifier.expected_paths().items()}: return 1
    try: digest = verifier.verify(receipt)
    except Exception:
        print('{"outcome":"FAIL","reason":"stop_binding_independent_postcheck_failed"}'); return 1
    print(json.dumps(dict(format='pult-stop-bound-construction-supervised-v1', outcome='PASS', tests=21,
        full_gate=True, independent_postcheck=True, owned_resources_empty=True,
        receipt=receipt.name, receipt_sha256=digest, journal_write_admitted=False,
        listener_admitted=False, deployment_admitted=False, remote_io_admitted=False,
        new_paid_resources=0), sort_keys=True), flush=True)
    return 0

if __name__ == '__main__': raise SystemExit(main())
