"""Record real OpenSSH COMPONENT proof, never storage/provider completion."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
def need(ok):
    if not ok:raise SystemExit('NATIVE_SSH_COMPONENT_GATE_REFUSED')
def sha(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def source_file(name):
    return (HERE.parents[4]/'ops/backup/custodian_v2/synthetic_ssh_s3'/name
        if name in ('ssh_ingress.py','ssh_helper.py','bounded_ssh.py') else HERE/name)
need(sys.platform=='linux' and sys.version_info[:2]==(3,12) and os.geteuid()==0 and sys.flags.isolated
     and sys.flags.no_site and sys.dont_write_bytecode and not sys.flags.optimize)
files=('native_ssh_admission.py','ssh_ingress.py','ssh_helper.py','bounded_ssh.py','openssh-supply/SUPPLY.json','run_native_ssh_gate.py')
before={name:sha(source_file(name)) for name in files}
run=subprocess.run(['/usr/bin/python3.12','-I','-S','-B',str(HERE/'native_ssh_admission.py')],
    capture_output=True,text=True,timeout=105)
need(run.returncode==0 and not run.stderr)
report=json.loads(run.stdout)
need(report['component_status']=='PASS' and len(report['cases'])==9 and report['forwarding_target_connections']==0)
need(report['synthetic_reply_fixture_only'] and report['storage_action_not_executed']
    and report['full_chain_proven'] is False and report['provider_proven'] is False)
need(all(report[name] for name in ('owned_bind_mounts_unmounted','owned_fixture_groups_reaped','owned_namespace_exited','owned_temporary_tree_removed','sources_unchanged')))
need(before=={name:sha(source_file(name)) for name in files})
for name,digest in report['source_sha256'].items():need(digest==before[name])
need(len(report['actual_ingress_proc_observations'])==2)
for observed in report['actual_ingress_proc_observations']:
    row=observed['actual_proc']
    need(row['Uid'].split()==['50001']*4 and row['Gid'].split()==['50002']*4
        and row['Groups'].split() in ([],['50002']) and row['NoNewPrivs']=='1' and row['Threads']=='1')
    need(all(int(row[n],16)==0 for n in ('CapEff','CapPrm','CapInh','CapAmb')))
report.update(gate_source_sha256=before,new_paid_resources=0,deployment_admitted=False)
file=HERE/('OPENSSH_COMPONENT_'+str(time.time_ns())+'.json')
with file.open('xb') as out:out.write((json.dumps(report,sort_keys=True,separators=(',',':'))+'\n').encode())
print(json.dumps(dict(receipt=file.name,**report),sort_keys=True))
