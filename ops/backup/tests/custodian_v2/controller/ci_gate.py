"""CI supervisor: exact full native run plus independent receipt postcheck."""
import sys
sys.dont_write_bytecode=True
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import uuid
from contextlib import contextmanager

ROOT=Path(__file__).resolve().parent

def load(name):
    spec=importlib.util.spec_from_file_location('ci_owned_'+name,ROOT/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)

@contextmanager
def create_scope(control):
    """Keep ownership through partial allocation and the handoff to supervision."""
    owned={};data=None
    def remove_allocated():
        for path,wanted in reversed(list(owned.items())):
            if path.exists():
                if path.is_symlink() or path.resolve()!=path or control.identity(path)!=wanted:
                    raise RuntimeError('CI_ALLOCATION_IDENTITY_REFUSED')
                shutil.rmtree(path)
    try:
        data=_allocate_scope(control,owned)
        yield data
    finally:
        saved={number:signal.signal(number,signal.SIG_IGN) for number in (signal.SIGINT,signal.SIGTERM,signal.SIGALRM)}
        try:
            if data is None: remove_allocated()
            elif Path(data['active']).exists(): cleanup(data,control)
            elif Path(data['work']).exists() or Path(data['active']).parent.exists():
                raise RuntimeError('CI_SCOPE_CLEANUP_UNPROVEN')
        finally:
            for number,handler in saved.items(): signal.signal(number,handler)

def _allocate_scope(control,owned):
    token=uuid.uuid4().hex;meta=Path('/run')/('pult-controller-scope-'+token)
    work=Path('/tmp')/('pult-cc-'+token[:16])
    # Exclusive, root-owned paths; never reuse or adopt a prior failed run.
    for path in (meta,work):
        previous=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGINT,signal.SIGTERM,signal.SIGALRM})
        try:
            path.mkdir(mode=0o755);owned[path]=control.identity(path);path.chmod(0o755)
        finally: signal.pthread_sigmask(signal.SIG_SETMASK,previous)
    for name,mode in (('ipc',0o755),('units',0o700)):
        (meta/name).mkdir(mode=mode);(meta/name).chmod(mode)
    fd=os.open(meta/'active',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try: os.fchmod(fd,0o600);os.fsync(fd)
    finally: os.close(fd)
    data=dict(format='pult-controller-ci-scope-v1',id=token,parent_unit='pult-controller-ci-run-'+token+'.service',
        work=str(work),ipc=str(meta/'ipc'),units=str(meta/'units'),active=str(meta/'active'))
    data['identities']={name:control.identity(Path(data[name])) for name in ('work','ipc','units','active')}
    data['identities']['meta']=control.identity(meta)
    with (meta/'scope.json').open('xb') as file:
        os.fchmod(file.fileno(),0o600);file.write(control.canonical(data));file.flush();os.fsync(file.fileno())
    sync_directory(meta);sync_directory(meta.parent);sync_directory(work.parent)
    os.environ[control.ENV]=str(meta/'scope.json');control.read_scope()
    return data

def parent_command(data,entry):
    return ['/usr/bin/systemd-run','--quiet','--pipe','--wait','--collect','--no-ask-password','--expand-environment=no',
        '--unit='+data['parent_unit'],'--property=ConditionPathExists='+data['active'],
        '--property=Type=exec','--property=ExitType=cgroup',
        '--property=RuntimeMaxSec=900','--property=TimeoutStartSec=2','--property=TimeoutStopSec=2',
        '--property=KillMode=control-group','--property=SendSIGKILL=yes','--property=FinalKillSignal=SIGKILL',
        '--property=Restart=no','--property=PrivateNetwork=yes','--property=NoNewPrivileges=yes',
        '--property=User=0','--property=Group=0','--setenv=PATH=/usr/bin:/bin','--setenv=LANG=C.UTF-8',
        '--setenv=PULT_CONTROLLER_CI_SCOPE='+os.environ['PULT_CONTROLLER_CI_SCOPE'],
        '/usr/bin/python3.12','-I','-S','-B',str(entry),'--reviewed-by-main']

def stop_owned(unit):
    if not re.fullmatch(r'pult-(?:call-gate-|controller-ci-run-)[0-9a-f]{32}\.service',unit):
        raise RuntimeError('CI_UNIT_IDENTITY_REFUSED')
    for args in (['stop',unit],['kill','--kill-whom=all','--signal=SIGKILL',unit],['stop',unit]):
        try: subprocess.run(['/usr/bin/systemctl',*args],capture_output=True,timeout=5,check=False)
        except subprocess.TimeoutExpired: pass
    result=subprocess.run(['/usr/bin/systemctl','show',unit,'--property=LoadState,ActiveState,ControlGroup'],
        capture_output=True,text=True,timeout=5,check=False)
    fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if fields.get('LoadState')=='not-found': return
    if result.returncode or fields.get('ActiveState') not in ('inactive','failed'):
        raise RuntimeError('CI_UNIT_STILL_ACTIVE')
    group=fields.get('ControlGroup','')
    if group:
        if group!='/system.slice/'+unit: raise RuntimeError('CI_CGROUP_IDENTITY_REFUSED')
        root=Path('/sys/fs/cgroup')/group.lstrip('/')
        if root.exists() and any(file.read_text().strip() for file in root.rglob('cgroup.procs')):
            raise RuntimeError('CI_CGROUP_NOT_EMPTY')
    subprocess.run(['/usr/bin/systemctl','reset-failed',unit],capture_output=True,timeout=5,check=False)

def cleanup(data,control):
    # First close the launch gate. A queued unit cannot start after this point.
    control.read_scope();active=Path(data['active']);active.unlink();sync_directory(active.parent)
    stop_owned(data['parent_unit'])
    control.read_scope(active=False)
    # A killed producer may leave a partial unpublished record. It never grants
    # launch authority and is removed only after every producer has stopped.
    directory=Path(data['units'])
    for file in sorted(directory.glob('.pending-*')):
        info=file.lstat()
        if (not re.fullmatch(r'\.pending-'+control.UNIT_PATTERN,file.name) or not stat.S_ISREG(info.st_mode)
            or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink not in (1,2)):
            raise RuntimeError('CI_PENDING_UNIT_IDENTITY_REFUSED')
        file.unlink()
    sync_directory(directory)
    for file in sorted(Path(data['units']).iterdir()):
        info=file.lstat();raw=file.read_bytes();record=json.loads(raw)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600
            or info.st_nlink!=1 or control.canonical(record)!=raw or set(record)!={'format','scope_id','unit'}
            or record['format']!='pult-controller-ci-unit-v1' or record['scope_id']!=data['id']
            or not re.fullmatch(control.UNIT_PATTERN,record['unit']) or file.name!=record['unit']+'.json'):
            raise RuntimeError('CI_UNIT_LEDGER_REFUSED')
        stop_owned(record['unit'])
    # No producer remains; remove ONLY this run's inode-checked synthetic trees.
    control.read_scope(active=False)
    for name in ('work','meta'):
        path=Path(data['work']) if name=='work' else Path(data['active']).parent
        expected=Path('/tmp')/('pult-cc-'+data['id'][:16]) if name=='work' else Path('/run')/('pult-controller-scope-'+data['id'])
        if path!=expected or path.resolve()!=expected or control.identity(path)!=data['identities'][name]:
            raise RuntimeError('CI_TREE_IDENTITY_REFUSED')
        shutil.rmtree(path)
        if path.exists(): raise RuntimeError('CI_TREE_REMAINS')

def supervised_run(data,control,entry,timeout=930):
    child=None;result=None;reason=None;clean=False;pidfd=None
    try:
        child=subprocess.Popen(parent_command(data,entry),env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        pidfd=os.pidfd_open(child.pid)
        stdout,stderr=child.communicate(timeout=timeout)
        result=subprocess.CompletedProcess(child.args,child.returncode,stdout,stderr)
    except subprocess.TimeoutExpired: reason='native_supervisor_timeout'
    except (KeyboardInterrupt,SystemExit): reason='native_supervisor_cancelled'
    except Exception: reason='native_supervisor_failed'
    finally:
        # A repeated CI cancellation must not interrupt identity-scoped teardown.
        saved={number:signal.signal(number,signal.SIG_IGN) for number in (signal.SIGINT,signal.SIGTERM,signal.SIGALRM)}
        try:
            try: cleanup(data,control);clean=True
            except Exception: reason='owned_resource_cleanup_unproven'
            if child is not None:
                try:
                    if child.poll() is None:
                        if pidfd is None: raise RuntimeError('CI_CHILD_IDENTITY_UNAVAILABLE')
                        signal.pidfd_send_signal(pidfd,signal.SIGKILL)
                    stdout,stderr=child.communicate(timeout=5)
                    if result is None: result=subprocess.CompletedProcess(child.args,child.returncode,stdout,stderr)
                except Exception: clean=False;reason='supervisor_cli_exit_unproven'
            if pidfd is not None: os.close(pidfd)
        finally:
            for number,handler in saved.items(): signal.signal(number,handler)
    return result,clean,reason

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_ci_runtime_refused"}');return 2
    if sys.argv[1:]!=['--reviewed-by-main'] or sys.platform!='linux' or os.geteuid()!=0: return 2
    files={file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in ROOT.glob('*.py')}
    control=load('scope_control');supervision=load('supervision_proof')
    def cancelled(signum,frame): raise KeyboardInterrupt
    previous=signal.signal(signal.SIGTERM,cancelled)
    try:
        supervision.prove(sys.modules[__name__],control)
        with create_scope(control) as data:
            result,clean,reason=supervised_run(data,control,ROOT/'run_gate.py')
    except (KeyboardInterrupt,SystemExit):
        print('{"outcome":"FAIL","reason":"ci_cancelled_outside_native_call"}');return 1
    finally: signal.signal(signal.SIGTERM,previous)
    if reason or not clean or result is None:
        print(json.dumps(dict(outcome='FAIL',reason=reason or 'supervised_run_incomplete',owned_resources_empty=clean)));return 1
    # Child exports only test IDs, sanitized errors and synthetic receipt hashes.
    print(result.stdout,end='',flush=True)
    if result.returncode or result.stderr: return 1
    lines=result.stdout.splitlines();receipts=[line.removeprefix('RECEIPT ') for line in lines if line.startswith('RECEIPT ')]
    if len(receipts)!=1: return 1
    receipt=Path(receipts[0])
    if receipt.parent.resolve()!=ROOT or not re.fullmatch('RUN_[0-9]+\\.json',receipt.name): return 1
    if files!={file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in ROOT.glob('*.py')}: return 1
    spec=importlib.util.spec_from_file_location('ci_independent_receipt_check',ROOT/'verify_receipt.py')
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    try: digest=verifier.verify(receipt)
    except Exception:
        print('{"outcome":"FAIL","reason":"independent_receipt_postcheck_failed"}');return 1
    print(json.dumps(dict(format='pult-controller-ci-supervised-proof-v1',outcome='PASS',full_gate=True,
        tests=52,native_receipt=receipt.name,native_receipt_sha256=digest,independent_postcheck=True,
        owned_resources_empty=clean,deployment_admitted=False,remote_io_admitted=False,new_paid_resources=0)),flush=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
