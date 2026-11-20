"""Owned systemd supervision and bounded postcheck for synthetic storage gate."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2/synthetic_ssh_s3'
PROOFS=REPO/'ops/backup/tests/custodian_v2/controller'

def need(ok):
    if not ok:raise RuntimeError('NATIVE_STORAGE_GATE_REFUSED')
def digest(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def capture(file,wanted,name):
    raw=file.read_bytes();need(hashlib.sha256(raw).hexdigest()==wanted)
    spec=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;exec(compile(raw,str(file),'exec',dont_inherit=True,optimize=0),m.__dict__);return m

def main():
    need(sys.platform=='linux' and sys.version_info[:2]==(3,12) and os.geteuid()==0 and sys.flags.isolated
        and sys.flags.no_site and sys.dont_write_bytecode and not sys.flags.optimize and sys.argv[1:]==['--reviewed-by-main'])
    control=capture(PROOFS/'scope_control.py','d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234','fixed_storage_scope')
    supervisor=capture(PROOFS/'ci_gate.py','d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b','fixed_storage_supervisor')
    files=tuple(sorted(SOURCE.glob('*.py')))+tuple(sorted(HERE.glob('*.py')))+(HERE/'native-source-pins.json',)
    before={file.name:digest(file) for file in files}
    with supervisor.create_scope(control) as scope:
        result,clean,reason=supervisor.supervised_run(scope,control,HERE/'native_storage_chain.py',timeout=240)
    need(clean and not reason and result is not None)
    # Output contains only artificial fixture metadata, never real key material.
    print(result.stdout,end='')
    if result.stderr:print(result.stderr,file=sys.stderr,end='')
    need(result.returncode==0 and not result.stderr)
    reports=[json.loads(row.removeprefix('NATIVE_STORAGE_RESULT ')) for row in result.stdout.splitlines() if row.startswith('NATIVE_STORAGE_RESULT ')]
    need(len(reports)==1 and reports[0]['outcome']=='PASS')
    rows=reports[0]['cases'];need([r['case'] for r in rows]==['happy','write_unknown','read_fail'])
    expected={'happy':(['PUT','GET'],None),'write_unknown':(['PUT'],'write_unknown_after_put'),
        'read_fail':(['PUT','GET'],'read_fail_get_error')}
    need(all((r['methods'],r['fault_activation'])==expected[r['case']] for r in rows))
    need(all(r['whole_groups_empty'] and r['permanent_fence_retained'] and r['keys_unchanged'] for r in rows))
    need(all(not r['purge'] and not r['restore'] and not r['deployment_admitted'] and not r['real_selectel_iam_tls'] for r in rows))
    need(before=={file.name:digest(file) for file in files})
    need(not Path(scope['work']).exists() and not Path(scope['active']).parent.exists())
    for row in rows:
        need(not Path(row['root']).exists())
        for unit in row['units']:need(not (Path('/sys/fs/cgroup/system.slice')/unit).exists())
    report=dict(format='pult-native-synthetic-storage-proof-v1',outcome='PASS',cases=rows,owned_scope_removed=True,
        source_hashes=before,sources_unchanged=True,real_systemd=True,real_sqlite=True,
        explicit_mock_s3=True,real_openssh=False,provider_tls_iam=False,deployment_admitted=False,new_paid_resources=0)
    need(all(row['actor_private_netns_verified'] for row in rows))
    file=HERE/('NATIVE_STORAGE_'+str(time.time_ns())+'.json')
    with file.open('xb') as output:output.write((json.dumps(report,sort_keys=True,separators=(',',':'))+'\n').encode())
    print('NATIVE_STORAGE_COMPLETION '+json.dumps(dict(receipt=file.name,**report),sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
