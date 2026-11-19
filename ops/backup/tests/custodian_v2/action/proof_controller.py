"""Private fixture controller: root OS entry, independently supplied profile SHA."""
import importlib.util
import json
import os
from pathlib import Path
import select
import sys

file = Path(sys.argv[1])
print('CONTROLLER_READY ' + str(os.getpid()), flush=True)
approval = sys.stdin.readline().strip()
spec = importlib.util.spec_from_file_location('fixture_fixed_bootstrap', Path(__file__).parent / 'action_child.py')
entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
profile = entry.bootstrap(str(file), approval)
scope_file=Path(__file__).parent/'scope_control.py'
assert __import__('hashlib').sha256(scope_file.read_bytes()).hexdigest()=='d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'
scope_spec=importlib.util.spec_from_file_location('fixed_action_proof_scope',scope_file)
control=importlib.util.module_from_spec(scope_spec);scope_spec.loader.exec_module(control)
class ScopedAction(profile.capture('root').Action):
    def command(self,role):
        # Test supervision only: register the already fixed prospective unit
        # BEFORE manager submission, add the outer CI gate/lifecycle dependency.
        return control.bound_command(super().command(role))
action = ScopedAction(profile)
try:
    answer = action.run()
except BaseException:
    original = action.failure
    print(json.dumps(dict(outcome='UNKNOWN', diagnostic=type(original).__name__ + ':' + str(original),
        fixture_output={r:{k:bytes(v).decode('utf-8','replace') for k,v in streams.items()} for r,streams in action.buffers.items()}),sort_keys=True),flush=True)
else:
    print(json.dumps(dict(outcome='PASS',result=answer),sort_keys=True),flush=True)
# Keep the ORIGINAL parent active while the harness tries a queued late start.
# This is a fixture control only, not a selectable runtime operation or permit.
if select.select([sys.stdin],[],[],10)[0]:
    assert sys.stdin.readline() == 'EXIT\n'
