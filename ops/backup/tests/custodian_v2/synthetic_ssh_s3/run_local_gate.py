"""Component gate only: NOT OpenSSH/systemd/live-provider/full-chain proof."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import unittest

def need(ok):
    if not ok:raise SystemExit('SYNTHETIC_COMPONENT_GATE_REFUSED')
need(not sys.flags.optimize and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE))
SOURCE=HERE.parents[4]/'ops/backup/custodian_v2/synthetic_ssh_s3'
def source_files():return tuple(sorted(SOURCE.glob('*.py')))+tuple(sorted(HERE.glob('*.py')))
files=source_files()
def snapshot():return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
before=snapshot()
names=['test_ssh_ingress','test_live_event_store']
if sys.platform=='linux':names+=['test_action_lifecycle','test_ssh_helper','test_controller_entry']
modules=[importlib.import_module(n) for n in names]
suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in modules)
result=unittest.TextTestRunner(verbosity=2).run(suite)
need(result.wasSuccessful() and not result.skipped and result.testsRun==({'win32':49,'linux':92}[sys.platform]))
import test_support
test_support.predecessor_sources();need(snapshot()==before and source_files()==files)
receipt=dict(format='pult-synthetic-ssh-s3-component-proof-v1',outcome='PASS',platform=sys.platform,
    tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skips=len(result.skipped),
    source_hashes=before,sources_unchanged=True,frozen_catalogue_verified=True,
    full_integrated_chain_proven=False,real_openssh_proven=False,actual_systemd_isolation_proven=False,
    actual_selectel_iam_tls_proven=False,deployment_admitted=False,new_paid_resources=0)
file=HERE/('COMPONENT_'+sys.platform+'_'+str(time.time_ns())+'.json')
with file.open('xb') as output:output.write((json.dumps(receipt,sort_keys=True,separators=(',',':'))+'\n').encode())
print(json.dumps(dict(receipt=file.name,**receipt),sort_keys=True))
