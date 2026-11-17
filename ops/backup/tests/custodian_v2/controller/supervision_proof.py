"""Real timeout/cancellation proof for synthetic CI ownership, not runtime logic."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parent

def worker():
    if sys.flags.optimize or sys.argv[1:]!=['--reviewed-by-main'] or os.geteuid()!=0: return 2
    spec=importlib.util.spec_from_file_location('fault_scope',ROOT/'scope_control.py')
    control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control);data=control.read_scope()
    assert [line.split(':',1)[0].strip() for line in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']
    unit='pult-call-gate-'+uuid.uuid4().hex+'.service'
    command=control.bound_command(['/usr/bin/systemd-run','--quiet','--no-ask-password','--expand-environment=no',
        '--unit='+unit,'--property=Type=exec','--property=PrivateNetwork=yes','--property=RuntimeMaxSec=30',
        '--property=TimeoutStopSec=1','--property=KillMode=control-group','--property=SendSIGKILL=yes',
        '--property=Restart=no','/bin/sleep','600'])
    subprocess.run(command,check=True,capture_output=True,timeout=5)
    subprocess.Popen(['/bin/sleep','600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (Path(data['ipc'])/'synthetic-ipc').write_text('synthetic only\n')
    (Path(data['work'])/'synthetic-file').write_text('synthetic only\n')
    pending=Path(data['units'])/('.pending-pult-call-gate-'+uuid.uuid4().hex+'.service')
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try: os.write(fd,b'{"partial":');os.fsync(fd)
    finally: os.close(fd)
    print('FAULT_FIXTURE_READY '+data['id'],flush=True)
    while True: time.sleep(60)

def prove(supervisor,control):
    # Cancellation while allocating, and after allocation but before Popen,
    # must retain an ownership guard just as a running command does.
    for point in ('allocation','handoff'):
        before=(set(Path('/run').glob('pult-controller-scope-*')),set(Path('/tmp').glob('pult-cc-*')))
        original=control.canonical;interrupted=False
        def cancel_allocation(value):
            if value.get('format')=='pult-controller-ci-scope-v1': os.kill(os.getpid(),signal.SIGTERM)
            return original(value)
        if point=='allocation': control.canonical=cancel_allocation
        try:
            with supervisor.create_scope(control):
                os.kill(os.getpid(),signal.SIGTERM)
        except KeyboardInterrupt: interrupted=True
        finally: control.canonical=original
        after=(set(Path('/run').glob('pult-controller-scope-*')),set(Path('/tmp').glob('pult-cc-*')))
        if not interrupted or before!=after: raise RuntimeError('CI_SCOPE_CANCELLATION_CLEANUP_FAILED')
        print(json.dumps(dict(format='pult-controller-ci-supervision-fault-v1',fault=point,outcome='PASS',
            owned_resources_empty=True,new_paid_resources=0)),flush=True)
    for fault in ('timeout','cancel'):
        with supervisor.create_scope(control) as data:
            previous=None
            if fault=='cancel':
                def cancel(signum,frame): os.kill(os.getpid(),signal.SIGTERM)
                previous=signal.signal(signal.SIGALRM,cancel);signal.setitimer(signal.ITIMER_REAL,8)
            try: result,clean,reason=supervisor.supervised_run(data,control,Path(__file__),timeout=8 if fault=='timeout' else 20)
            finally:
                if fault=='cancel': signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
        wanted='native_supervisor_timeout' if fault=='timeout' else 'native_supervisor_cancelled'
        if not clean or reason!=wanted or result is None or result.stderr or result.stdout.strip()!='FAULT_FIXTURE_READY '+data['id']:
            raise RuntimeError('CI_SUPERVISION_FAULT_PROOF_FAILED')
        if Path(data['work']).exists() or Path(data['active']).parent.exists(): raise RuntimeError('CI_FAULT_TREE_REMAINS')
        listing=subprocess.run(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend',
            'pult-call-gate-*','pult-controller-ci-run-*'],capture_output=True,timeout=5,check=False)
        if listing.returncode or listing.stdout.strip() or listing.stderr: raise RuntimeError('CI_FAULT_UNIT_REMAINS')
        print(json.dumps(dict(format='pult-controller-ci-supervision-fault-v1',fault=fault,outcome='PASS',
            owned_resources_empty=True,real_descendant_and_unit=True,new_paid_resources=0)),flush=True)

if __name__=='__main__': raise SystemExit(worker())
