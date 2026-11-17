"""Offline controller packaging contracts; never execute product candidates."""

import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import textwrap

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "ops/backup/custodian_v2/controller"
TESTS = ROOT / "ops/backup/tests/custodian_v2/controller"
WORKFLOW = ROOT / ".github/workflows/backup_restore_synthetic.yml"
GUARD_PATH = "backend/tests/test_custodian_controller_source_ci.py"
DOC_PATH = "docs/erasure-custodian-controller-source-ci.md"
TEST_PREFIX = "ops/backup/tests/custodian_v2/controller/"
GUARD_STEP = "Guard dormant controller source and CI contracts"
NATIVE_STEP = "Native systemd controller proof and independent receipt check"
OPTIMIZED_STEP = "Reject optimized controller gate and verifier runtimes"
OLD_OPTIMIZED_STEP = "Reject optimized custodian v2 gate runtimes with exact reason"
BUILD_STEP = "Build backup runner image (pinned base + pinned rclone/age, hash-verified)"
NEW_STEPS = [GUARD_STEP, NATIVE_STEP, OPTIMIZED_STEP]
PREFIX = ["sudo", "-n", "--", "unshare", "--net", "/usr/bin/python3.12", "-I", "-S", "-B"]
PINS = {
    "observer_v2.py": "65462111526213daa1e62b3cbe81e146e8277f7eb5d521b70266f28046972739",
    "supervisor_v2.py": "3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e",
    "repeatable_observer.py": "de0c1caeaa00cabc2e4aa324e595bf6708668d719841f92bf85b3715be107aad",
    "frozen/admission.py": "961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7",
}
WORKER_PINS = {
    "joint_worker.py": "2ee178a01ec2631d4891e7a509cb275f3419b78e8ae9f0f69f9b8cb64c7057a8",
    "owner_worker.py": "e9c337673e5c8e08f7f104154d1ebc2b3dbb379d4544a553916db77e546b0ed5",
    "test_repeat.py": "d8e0e07083d1254a73d407d9e8bc30ff0b6c33b88f558d16d5acff874a98cfe7",
}
NATIVE_FILES = ("fixture_joint.py", "fixture_v2.py", "joint_worker.py", "owner_worker.py",
                "proof_repeat.py", "test_repeat.py", "run_gate.py", "verify_receipt.py", "ci_gate.py",
                "scope_control.py", "supervision_proof.py")
REFUSALS = {
    "ci_gate.py": "optimized_ci_runtime_refused",
    "run_gate.py": "optimized_test_runtime_refused",
    "verify_receipt.py": "optimized_verifier_refused",
}
OPTIMIZED_DRIVER = textwrap.dedent('''\
    import json
    import subprocess

    root = "ops/backup/tests/custodian_v2/controller/"
    cases = (
        ("ci_gate.py", "optimized_ci_runtime_refused"),
        ("run_gate.py", "optimized_test_runtime_refused"),
        ("verify_receipt.py", "optimized_verifier_refused"),
    )
    for name, reason in cases:
        result = subprocess.run(
            ["sudo", "-n", "--", "unshare", "--net", "/usr/bin/python3.12", "-I", "-S", "-B", "-O",
             root + name, "--reviewed-by-main"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        try:
            report = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise SystemExit("optimized controller entry did not return refusal JSON") from None
        if (result.returncode != 2 or result.stderr
                or report != {"outcome": "NOT_RUN", "reason": reason}):
            raise SystemExit("optimized controller entry did not return the exact refusal")
''')

# Exact function ASTs deliberately protect control flow, not just the presence
# of cleanup/hardening tokens in an unreachable or conditional branch.
SUPERVISOR_CONTRACT = r'''
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
    control.read_scope();active=Path(data['active']);active.unlink();sync_directory(active.parent)
    stop_owned(data['parent_unit'])
    control.read_scope(active=False)
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
'''

SCOPE_CONTRACT = r'''
def read_scope(active=True):
    file=Path(os.environ[ENV])
    if not file.is_absolute() or file.name!='scope.json' or file.is_symlink(): raise RuntimeError('CI_SCOPE_REFUSED')
    info=file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size>8192:
        raise RuntimeError('CI_SCOPE_REFUSED')
    raw=file.read_bytes();data=json.loads(raw)
    if canonical(data)!=raw or set(data)!={'format','id','parent_unit','work','ipc','units','active','identities'}:
        raise RuntimeError('CI_SCOPE_REFUSED')
    token=data['id']
    if data['format']!='pult-controller-ci-scope-v1' or not re.fullmatch('[0-9a-f]{32}',token): raise RuntimeError('CI_SCOPE_REFUSED')
    meta=Path('/run')/('pult-controller-scope-'+token)
    expected={'work':Path('/tmp')/('pult-cc-'+token[:16]),'ipc':meta/'ipc','units':meta/'units','active':meta/'active'}
    if file.parent!=meta or data['parent_unit']!='pult-controller-ci-run-'+token+'.service': raise RuntimeError('CI_SCOPE_REFUSED')
    if set(data['identities'])!={'meta','work','ipc','units','active'}: raise RuntimeError('CI_SCOPE_REFUSED')
    for name,path in expected.items():
        if data[name]!=str(path): raise RuntimeError('CI_SCOPE_REFUSED')
        if name=='active' and not active: continue
        info=path.lstat()
        wanted_mode=0o600 if name=='active' else (0o700 if name=='units' else 0o755)
        if (info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=wanted_mode or stat.S_ISLNK(info.st_mode)
            or identity(path)!=data['identities'][name]): raise RuntimeError('CI_SCOPE_REFUSED')
        if name=='active' and (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1): raise RuntimeError('CI_SCOPE_REFUSED')
        if name!='active' and not stat.S_ISDIR(info.st_mode): raise RuntimeError('CI_SCOPE_REFUSED')
    for path in (meta,Path('/tmp'),Path('/run')):
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0: raise RuntimeError('CI_SCOPE_REFUSED')
    if stat.S_IMODE(meta.lstat().st_mode)!=0o755 or identity(meta)!=data['identities']['meta']: raise RuntimeError('CI_SCOPE_REFUSED')
    return data

def bound_command(argv):
    """Persist unit identity BEFORE sending its start request to PID1."""
    data=read_scope();unit=next(item.removeprefix('--unit=') for item in argv if item.startswith('--unit='))
    if not re.fullmatch(UNIT_PATTERN,unit): raise RuntimeError('CI_UNIT_IDENTITY_REFUSED')
    record=Path(data['units'])/(unit+'.json');raw=canonical(dict(format='pult-controller-ci-unit-v1',scope_id=data['id'],unit=unit))
    pending=record.parent/('.pending-'+unit)
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        os.fchmod(fd,0o600);offset=0
        while offset<len(raw):
            count=os.write(fd,raw[offset:])
            if count<=0: raise RuntimeError('CI_UNIT_RECORD_INCOMPLETE')
            offset+=count
        os.fsync(fd)
    finally: os.close(fd)
    os.link(pending,record,follow_symlinks=False)
    pending.unlink()
    directory=os.open(record.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try: os.fsync(directory)
    finally: os.close(directory)
    return [argv[0],'--property=ConditionPathExists='+data['active'],
        '--property=PartOf='+data['parent_unit'],'--property=After='+data['parent_unit'],*argv[1:]]
'''

FAULT_CONTRACT = r'''
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
'''


def _shape(source):
    return ast.dump(ast.parse(source))


def _literal(tree, name):
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    assert len(values) == 1, name
    return ast.literal_eval(values[0])


def _has(tree, source):
    expected = ast.dump(ast.parse(source).body[0])
    assert expected in {ast.dump(node) for node in ast.walk(tree)}, source


def _assert_functions(source, contract):
    tree = ast.parse(source)
    for expected in ast.parse(contract).body:
        assert isinstance(expected, ast.FunctionDef)
        matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == expected.name]
        assert len(matches) == 1, expected.name
        assert ast.dump(matches[0]) == ast.dump(expected), expected.name
    return tree


def _assert_supervisor(source):
    _assert_entry("ci_gate.py", source)
    return _assert_functions(source, SUPERVISOR_CONTRACT)


def _assert_scope(source):
    tree = _assert_functions(source, SCOPE_CONTRACT)
    assert _literal(tree, "ENV") == "PULT_CONTROLLER_CI_SCOPE"
    assert _literal(tree, "UNIT_PATTERN") == r"pult-call-gate-[0-9a-f]{32}\.service"
    return tree


def _assert_elapsed_contract(source):
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "valid_summary"]
    assert len(functions) == 1
    assert len(functions[0].body) == 1 and isinstance(functions[0].body[0], ast.Try)
    body = functions[0].body[0].body
    assignment = ast.parse("elapsed=report['elapsed_seconds']").body[0]
    positions = [i for i, node in enumerate(body) if ast.dump(node) == ast.dump(assignment)]
    assert len(positions) == 1 and positions[0] + 1 < len(body)
    expected = ast.parse("require(type(elapsed) in (int,float) and math.isfinite(elapsed) and 0<=elapsed<=900)").body[0]
    assert ast.dump(body[positions[0] + 1]) == ast.dump(expected)


BOUND_HELPERS = {
    "fixture_joint.py": "scope_control",
    "fixture_v2.py": "old.scope_control",
    "proof_repeat.py": "v2.old.scope_control",
}


def _assert_bound_helper(name, source):
    tree = ast.parse(source)
    launch = ast.parse("process=original(argv,*args,**kwargs)").body[0]
    expected = ast.parse("if unit: argv=" + BOUND_HELPERS[name] + ".bound_command(argv)").body[0]
    wrappers = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                and any(ast.dump(statement) == ast.dump(launch) for statement in node.body)]
    assert len(wrappers) == 1, name
    body = wrappers[0].body
    index = next(i for i, statement in enumerate(body) if ast.dump(statement) == ast.dump(launch))
    assert index > 0 and ast.dump(body[index - 1]) == ast.dump(expected), name
    calls = [node for node in ast.walk(wrappers[0]) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "bound_command"]
    assert len(calls) == 1, name


def _workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _assert_ci(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["master"]
    for event in ("pull_request", "push"):
        paths = workflow["on"][event]["paths"]
        assert {GUARD_PATH, DOC_PATH, "ops/backup/**"} <= set(paths)
        assert not {"*", "**", "backend/**", "ops/**", "docs/**"}.intersection(paths)
    assert set(workflow["jobs"]) == {"synthetic"}
    job = workflow["jobs"]["synthetic"]
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == "25"
    assert not {"if", "continue-on-error", "defaults", "container", "services", "strategy"}.intersection(job)
    steps = job["steps"]
    names = [step["name"] for step in steps]
    assert len(names) == len(set(names))
    start = names.index(OLD_OPTIMIZED_STEP)
    assert names[start:start + 5] == [OLD_OPTIMIZED_STEP] + NEW_STEPS + [BUILD_STEP]
    by_name = {step["name"]: step for step in steps}
    for name in NEW_STEPS:
        assert set(by_name[name]) == {"name", "run"}
    assert shlex.split(by_name[GUARD_STEP]["run"]) == ["python", "-m", "pytest", GUARD_PATH, "-q"]
    assert shlex.split(by_name[NATIVE_STEP]["run"]) == PREFIX + [TEST_PREFIX + "ci_gate.py", "--reviewed-by-main"]
    negative = by_name[OPTIMIZED_STEP]["run"].splitlines()
    assert negative[:2] == ["set -euo pipefail", "python - <<'PY'"] and negative[-1] == "PY"
    assert _shape("\n".join(negative[2:-1])) == _shape(OPTIMIZED_DRIVER)
    for step in steps:
        assert "continue-on-error" not in step
        if "uses" in step:
            assert re.search(r"@[0-9a-f]{40}$", step["uses"])
            assert "upload-artifact" not in step["uses"].lower()


def _assert_pins(blobs):
    assert set(blobs) == set(PINS)
    for name, wanted in PINS.items():
        assert hashlib.sha256(blobs[name]).hexdigest() == wanted, name
        ast.parse(blobs[name], filename=name)


def _assert_draft(profile):
    assert profile == {
        "format": "pult-controller-source-preparation-v1",
        "status": "DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED",
        "operator_approval": None,
        "admitted_operations": [],
        "working_credentials_included": False,
        "database_or_pending_included": False,
        "installation_service_or_network_actions": False,
        "lifecycle_limit": "ONE_LIVE_CONTROLLER_NO_RESTART_ADOPTION",
        "max_calls": 64,
        "runtime": {"implementation": "cpython", "major_minor": [3, 12], "platform": "linux"},
        "sources": PINS,
    }
    for name in ("working_credentials_included", "database_or_pending_included", "installation_service_or_network_actions"):
        assert profile[name] is False
    assert type(profile["max_calls"]) is int


def _assert_entry(name, source):
    tree = ast.parse(source)
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    refusal = main.body[0]
    expected = ast.parse("if sys.flags.optimize:\n    print(" + repr(json.dumps(
        {"outcome": "NOT_RUN", "reason": REFUSALS[name]}, separators=(",", ":"))) + ")\n    return 2").body[0]
    assert ast.dump(refusal) == ast.dump(expected), name
    assert not any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and "--focus" in node.value for node in ast.walk(tree))
    return tree


def _assert_native_gate(source):
    tree = _assert_entry("run_gate.py", source)
    assert _literal(tree, "SOURCES") == PINS
    assert _literal(tree, "WORKER_PINS") == WORKER_PINS
    assert _literal(tree, "NATIVE_FILES") == NATIVE_FILES
    for statement in (
        "if (sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12) "
        "or os.geteuid()!=0 or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode "
        "or Path('/proc/1/comm').read_text().strip()!='systemd' "
        "or not Path('/sys/fs/cgroup/cgroup.controllers').exists()):\n"
        "    print('{\"outcome\":\"NOT_RUN\",\"reason\":\"native_root_systemd_cgroup_required\"}');return 2",
        "assert [line.split(':',1)[0].strip() for line in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']",
        "assert not loader.errors and count==52",
        "success=(completed and count==result.testsRun==52 and result.wasSuccessful() and not result.skipped "
        "and not result.expectedFailures and not result.unexpectedSuccesses and not issues and not units_after "
        "and before==after and not base.exists())",
        "assert all(sha(CONTROLLER/name)==wanted for name,wanted in SOURCES.items())",
        "assert all(sha(ROOT/name)==wanted for name,wanted in WORKER_PINS.items())",
        "before={name:sha(file) for name,file in files.items()}",
        "after={name:sha(file) for name,file in files.items()}",
        "before.update(helper.input_hashes())", "after.update(helper.input_hashes())",
        "scope_control=load(ROOT/'scope_control.py','native_gate_owned_scope')",
        "scope=scope_control.read_scope()",
        "assert base.parent==Path(scope['work'])",
    ):
        _has(tree, statement)
    scoped = [node for node in ast.walk(tree) if isinstance(node, ast.With)
              and any(isinstance(item.context_expr, ast.Call)
                      and isinstance(item.context_expr.func, ast.Attribute)
                      and item.context_expr.func.attr == "TemporaryDirectory" for item in node.items)]
    assert len(scoped) == 1
    assert ast.dump(scoped[0].items[0].context_expr) == ast.dump(ast.parse(
        "tempfile.TemporaryDirectory(prefix='native-',dir=scope['work'])", mode="eval").body)
    reports = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == 'report' for target in node.targets)]
    assert len(reports) == 1 and isinstance(reports[0], ast.Call)
    assert isinstance(reports[0].func, ast.Name) and reports[0].func.id == "dict"
    values = {item.arg: item.value for item in reports[0].keywords}
    assert set(values) == set(_valid_report()) and len(values) == len(reports[0].keywords)
    for key, expression in {
        "scope": repr(_valid_report()["scope"]), "full_gate": "True",
        "outcome": "'PASS' if success else 'FAIL'", "tests": "result.testsRun", "discovered": "count",
        "source_hashes_before": "before", "source_hashes_after": "after", "sources_unchanged": "before==after",
        "new_paid_resources": "0", "additional_spend_rub": "0",
    }.items():
        assert ast.dump(values[key]) == ast.dump(ast.parse(expression, mode="eval").body), key


def _assert_fixture_source(name, body):
    tree = ast.parse(body, filename=name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not re.search(r"custodian-[a-z0-9-]+-20[0-9]{6}|^[A-Za-z]:[\\/]|/home/|/Users/", node.value), (name, node.lineno)
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
    return tree


def test_workflow_exact_controller_commands_order_and_refusals():
    _assert_ci(_workflow())


def test_four_sources_are_raw_pinned_and_profile_is_dormant():
    _assert_pins({name: (SOURCE / name).read_bytes() for name in PINS})
    assert {str(path.relative_to(SOURCE)).replace('\\', '/') for path in SOURCE.rglob('*') if path.is_file()} == set(PINS) | {"source-profile.json"}
    _assert_draft(json.loads((SOURCE / "source-profile.json").read_bytes()))


def test_fixture_dependencies_are_closed_and_repository_native():
    assert {path.name for path in TESTS.glob('*.py')} == set(NATIVE_FILES)
    for name in NATIVE_FILES:
        body = (TESTS / name).read_bytes()
        _assert_fixture_source(name, body)
        if name in WORKER_PINS:
            assert hashlib.sha256(body).hexdigest() == WORKER_PINS[name]
    dependencies = {
        "fixture_joint.py": ["REPO=ROOT.parents[4]", "TESTS=REPO/'ops/backup/tests/custodian_v2'",
                             "ADMISSION=SOURCE/'controller/frozen/admission.py'",
                             "cold_tests=load(TESTS/'cold/test_cold_host.py','joint_pinned_cold_fixture')",
                             "scope_control=load(ROOT/'scope_control.py','owned_controller_ci_scope')",
                             "IPC_PARENT=Path(scope_control.read_scope()['ipc'])",
                             "self.ipc=IPC_PARENT/('j-'+uuid4().hex[:16])",
                             "if self.ipc.parent!=IPC_PARENT or not re.fullmatch('j-[0-9a-f]{16}',self.ipc.name):\n"
                             "    errors.append('IPC_SCOPE')\nelse: shutil.rmtree(self.ipc)"],
        "fixture_v2.py": ["old=load(HERE/'fixture_joint.py','v2_immutable_fixture')"],
        "proof_repeat.py": ["v2=load(ROOT/'fixture_v2.py','immutable_v2_proof_fixture')"],
        "run_gate.py": ["REPO=ROOT.parents[4]", "SOURCE=ROOT.parents[2]/'custodian_v2'",
                        "helper=load(TESTS/'support.py','controller_verified_wheel_fixture')"],
    }
    for name, statements in dependencies.items():
        tree = ast.parse((TESTS / name).read_bytes())
        for statement in statements:
            _has(tree, statement)


@pytest.mark.parametrize("path", ["C:/business-pult/tmp/fixture.py", "/home/developer/fixture.py",
                                 "/Users/developer/fixture.py", "custodian-observer-v2-20261117/proof_v2.py"])
def test_fixture_contract_rejects_developer_path_dependencies(path):
    with pytest.raises(AssertionError):
        _assert_fixture_source("fixture.py", "dependency=" + repr(path))


def test_preserved_52_cases_include_30_unique_real_crash_boundaries():
    tree = ast.parse((TESTS / "test_repeat.py").read_bytes())
    cases = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RepeatableTests")
    assert len([node for node in cases.body if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]) == 22
    phases = _literal(tree, "CRASH_PHASES")
    assert len(phases) == len(set(phases)) == 30
    _has(tree, "setattr(RepeatableTests,'test_crash_'+phase.lower(),crash_case(phase))")
    proof = ast.parse((TESTS / "proof_repeat.py").read_bytes())
    _has(proof, "signal.pidfd_send_signal(self.pins[controller],signal.SIGKILL)")


@pytest.mark.parametrize("name", REFUSALS)
def test_optimized_refusal_is_first_entry_action(name):
    _assert_entry(name, (TESTS / name).read_text(encoding="utf-8"))


def test_native_gate_requires_systemd_and_exact_full_immutable_proof():
    _assert_native_gate((TESTS / "run_gate.py").read_text(encoding="utf-8"))
    _assert_supervisor((TESTS / "ci_gate.py").read_text(encoding="utf-8"))
    verifier_source = (TESTS / "verify_receipt.py").read_text(encoding="utf-8")
    _assert_elapsed_contract(verifier_source)
    verifier = ast.parse(verifier_source)
    assert _literal(verifier, "NATIVE_FILES") == NATIVE_FILES
    assert len(NATIVE_FILES) == 11
    assert _literal(verifier, "SUMMARY_FIELDS") == set(_valid_report())
    _has(verifier, "require(set(paths)==set(report['source_hashes_before']))")
    _has(verifier, "for name,target in paths.items(): require(digest(target)==report['source_hashes_before'][name])")
    for directory, pattern in (("/tmp", "pult-cc-*"), ("/run", "pult-controller-scope-*")):
        _has(verifier, "require(not list(Path(" + repr(directory) + ").glob(" + repr(pattern) + ")))")
    _has(verifier, "result=subprocess.run(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend',"
         "'pult-controller-ci-run-*'],capture_output=True,timeout=10)")


def test_scoped_launch_gate_and_durable_unit_ledger_contract():
    _assert_scope((TESTS / "scope_control.py").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", BOUND_HELPERS)
def test_each_helper_binds_durable_unit_before_popen(name):
    _assert_bound_helper(name, (TESTS / name).read_text(encoding="utf-8"))


def test_real_fault_proof_requires_timeout_cancel_descendant_unit_and_empty_resources():
    _assert_functions((TESTS / "supervision_proof.py").read_text(encoding="utf-8"), FAULT_CONTRACT)


@pytest.mark.parametrize("old,new", [
    ("try: cleanup(data,control);clean=True", "try: clean=True"),
    ("finally:\n        # A repeated CI cancellation", "else:\n        # A repeated CI cancellation"),
    ("except subprocess.TimeoutExpired: reason='native_supervisor_timeout'",
     "except subprocess.TimeoutExpired: return None,True,None"),
    ("except (KeyboardInterrupt,SystemExit): reason='native_supervisor_cancelled'",
     "except (KeyboardInterrupt,SystemExit): pass"),
    ("except Exception: reason='native_supervisor_failed'", "except Exception: pass"),
    ("except Exception: reason='owned_resource_cleanup_unproven'", "except Exception: clean=True"),
    ("except Exception: clean=False;reason='supervisor_cli_exit_unproven'", "except Exception: pass"),
    ("if pidfd is None: raise RuntimeError('CI_CHILD_IDENTITY_UNAVAILABLE')", "if pidfd is None: pass"),
    ("signal.pidfd_send_signal(pidfd,signal.SIGKILL)", "pass"),
    ("stdout,stderr=child.communicate(timeout=5)", "stdout,stderr='',''"),
    ("if result is None: result=subprocess.CompletedProcess(child.args,child.returncode,stdout,stderr)", "pass"),
    ("return result,clean,reason", "return result,True,None"),
    ("active.unlink();sync_directory(active.parent)", "sync_directory(active.parent)"),
    ("active.unlink();sync_directory(active.parent)\n    stop_owned(data['parent_unit'])",
     "stop_owned(data['parent_unit']);active.unlink();sync_directory(active.parent)"),
    ("stop_owned(record['unit'])", "pass"),
    ("record['scope_id']!=data['id']", "False"),
    ("control.identity(path)!=data['identities'][name]", "False"),
    ("group!='/system.slice/'+unit", "False"),
    ("any(file.read_text().strip() for file in root.rglob('cgroup.procs'))", "False"),
    ("shutil.rmtree(path)", "pass"),
    ("path.mkdir(mode=0o755)", "path.mkdir(mode=0o755,exist_ok=True)"),
    ("os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW", "os.O_WRONLY|os.O_CREAT"),
    ("with (meta/'scope.json').open('xb')", "with (meta/'scope.json').open('wb')"),
    ("file.flush();os.fsync(file.fileno())", "file.flush()"),
    ("supervision.prove(sys.modules[__name__],control)", "pass"),
    ("supervision.prove(sys.modules[__name__],control)", "if False: supervision.prove(sys.modules[__name__],control)"),
    ("result,clean,reason=supervised_run(data,control,ROOT/'run_gate.py')",
     "result,clean,reason=supervised_run(data,control,ROOT/'run_gate.py')\n        supervision.prove(sys.modules[__name__],control)"),
    ("if reason or not clean or result is None:", "if False:"),
    ("owned_resources_empty=clean", "owned_resources_empty=True"),
    ("independent_postcheck=True", "independent_postcheck=False"),
    ("digest=verifier.verify(receipt)", "digest='0'*64"),
    ("if files!={file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in ROOT.glob('*.py')}: return 1",
     "if False: return 1"),
    ("deployment_admitted=False", "deployment_admitted=True"),
    ("'--property=ConditionPathExists='+data['active'],", ""),
    ("'--property=ConditionPathExists='+data['active']", "'--property=ConditionPathExists=/tmp/always'"),
    ("'--property=ConditionPathExists='+data['active']", "'--property=ConditionPathExists='+data['work']"),
    ("previous=signal.signal(signal.SIGTERM,cancelled)", "previous=signal.getsignal(signal.SIGTERM)"),
    ("previous=signal.signal(signal.SIGTERM,cancelled)\n    try:\n        supervision.prove",
     "previous=signal.getsignal(signal.SIGTERM)\n    try:\n        previous=signal.signal(signal.SIGTERM,cancelled)\n        supervision.prove"),
    ("supervision.prove(sys.modules[__name__],control)\n        with create_scope(control) as data:",
     "supervision.prove(sys.modules[__name__],control)\n        previous=signal.signal(signal.SIGTERM,cancelled)\n        with create_scope(control) as data:"),
    ("signal.SIG_IGN", "signal.SIG_DFL"),
    ("(signal.SIGINT,signal.SIGTERM,signal.SIGALRM)", "(signal.SIGINT,signal.SIGTERM)"),
    ("(signal.SIGINT,signal.SIGTERM,signal.SIGALRM)", "(signal.SIGINT,signal.SIGALRM)"),
    ("(signal.SIGINT,signal.SIGTERM,signal.SIGALRM)", "(signal.SIGTERM,signal.SIGALRM)"),
    ("for number,handler in saved.items(): signal.signal(number,handler)", "for number,handler in saved.items(): pass"),
    ("for number,handler in saved.items(): signal.signal(number,handler)",
     "for number,handler in saved.items(): signal.signal(number,signal.SIG_DFL)"),
])
def test_supervisor_contract_rejects_cleanup_cancel_timeout_and_false_success_mutations(old, new):
    source = (TESTS / "ci_gate.py").read_text(encoding="utf-8")
    assert old in source
    with pytest.raises(AssertionError):
        _assert_supervisor(source.replace(old, new))


@pytest.mark.parametrize("mutation", ["no_signal_guard", "ignore_after_cleanup", "restore_before_cleanup",
                                    "restore_before_cli_reap", "restore_not_finally",
                                    "handler_after_proof", "handler_after_scope_creation"])
def test_supervisor_rejects_signal_protection_order_mutations(mutation):
    source = (TESTS / "ci_gate.py").read_text(encoding="utf-8")
    _assert_supervisor(source)
    tree = ast.parse(source)
    if mutation.startswith("handler_after_"):
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        install = next(node for node in main.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "previous" for target in node.targets))
        protected = next(node for node in main.body if isinstance(node, ast.Try)
                         and any(isinstance(item, ast.Expr) and isinstance(item.value, ast.Call)
                                 and isinstance(item.value.func, ast.Attribute) and item.value.func.attr == "prove"
                                 for item in node.body))
        main.body.remove(install)
        if mutation == "handler_after_proof":
            protected.body.insert(1, install)
        else:
            context = next(node for node in protected.body if isinstance(node, ast.With))
            context.body.insert(0, install)
    else:
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "supervised_run")
        outer = next(node for node in function.body if isinstance(node, ast.Try))
        saved, protected = outer.finalbody
        assert isinstance(saved, ast.Assign) and isinstance(protected, ast.Try)
        restore = protected.finalbody[0]
        if mutation == "no_signal_guard":
            outer.finalbody = protected.body
        elif mutation == "ignore_after_cleanup":
            outer.finalbody.remove(saved)
            protected.body.insert(1, saved)
        elif mutation == "restore_not_finally":
            outer.finalbody = [saved, *protected.body, restore]
        else:
            protected.finalbody = [ast.Pass()]
            protected.body.insert(0 if mutation == "restore_before_cleanup" else 1, restore)
    with pytest.raises(AssertionError):
        _assert_supervisor(ast.unparse(ast.fix_missing_locations(tree)))


@pytest.mark.parametrize("option", [
    "--pipe", "--wait", "--collect", "--no-ask-password", "--expand-environment=no",
    "--property=Type=exec", "--property=ExitType=cgroup", "--property=RuntimeMaxSec=900",
    "--property=TimeoutStartSec=2", "--property=TimeoutStopSec=2", "--property=KillMode=control-group",
    "--property=SendSIGKILL=yes", "--property=FinalKillSignal=SIGKILL", "--property=Restart=no",
    "--property=PrivateNetwork=yes", "--property=NoNewPrivileges=yes", "--property=User=0", "--property=Group=0",
    "--setenv=PATH=/usr/bin:/bin", "--setenv=LANG=C.UTF-8", "/usr/bin/python3.12", "-I", "-S", "-B",
])
def test_parent_command_requires_every_exact_hardening_argument(option):
    source = (TESTS / "ci_gate.py").read_text(encoding="utf-8")
    token = repr(option) + ","
    assert token in source
    with pytest.raises(AssertionError):
        _assert_supervisor(source.replace(token, "", 1))


@pytest.mark.parametrize("old,new", [
    ("@contextmanager\ndef create_scope", "def create_scope"),
    ("yield data", "return data"),
    ("data=_allocate_scope(control,owned)\n        yield data", "yield data\n        data=_allocate_scope(control,owned)"),
    ("if data is None: remove_allocated()", "if data is None: pass"),
    ("elif Path(data['active']).exists(): cleanup(data,control)", "elif Path(data['active']).exists(): pass"),
    ("elif Path(data['work']).exists() or Path(data['active']).parent.exists():", "elif False:"),
    ("control.identity(path)!=wanted", "False"),
    ("owned[path]=control.identity(path);", ""),
    ("finally: signal.pthread_sigmask(signal.SIG_SETMASK,previous)", "finally: pass"),
    ("path.mkdir(mode=0o755);owned[path]=control.identity(path);path.chmod(0o755)",
     "path.mkdir(mode=0o755);signal.pthread_sigmask(signal.SIG_SETMASK,previous);owned[path]=control.identity(path);path.chmod(0o755)"),
    ("with create_scope(control) as data:", "if True:"),
    ("directory.glob('.pending-*')", "directory.glob('*')"),
    ("not re.fullmatch(r'\\.pending-'+control.UNIT_PATTERN,file.name)", "False"),
    ("info.st_nlink not in (1,2)", "False"),
    ("info=file.lstat()\n        if (not re.fullmatch", "info=file.lstat();json.loads(file.read_bytes())\n        if (not re.fullmatch"),
    ("file.unlink()\n    sync_directory(directory)", "pass\n    sync_directory(directory)"),
    ("sync_directory(directory)\n    for file in sorted(Path(data['units']).iterdir()):",
     "pass\n    for file in sorted(Path(data['units']).iterdir()):"),
])
def test_scope_context_rejects_unowned_allocation_handoff_and_partial_record_cleanup_mutations(old, new):
    source = (TESTS / "ci_gate.py").read_text(encoding="utf-8")
    _assert_supervisor(source)
    assert old in source
    with pytest.raises(AssertionError):
        _assert_supervisor(source.replace(old, new))


@pytest.mark.parametrize("mutation", ["parent_stop_after_pending", "parse_records_before_pending"])
def test_pending_cleanup_cannot_precede_parent_stop_or_follow_record_parsing(mutation):
    source = (TESTS / "ci_gate.py").read_text(encoding="utf-8")
    _assert_supervisor(source)
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "cleanup")
    pending = next(node for node in function.body if isinstance(node, ast.For)
                   and any(isinstance(item, ast.Constant) and item.value == '.pending-*' for item in ast.walk(node.iter)))
    if mutation == "parent_stop_after_pending":
        stop = next(node for node in function.body if isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "stop_owned")
        function.body.remove(stop)
        function.body.insert(function.body.index(pending) + 1, stop)
    else:
        records = next(node for node in function.body if isinstance(node, ast.For)
                       and any(isinstance(item, ast.Attribute) and item.attr == "iterdir" for item in ast.walk(node.iter)))
        function.body.remove(records)
        function.body.insert(function.body.index(pending), records)
    with pytest.raises(AssertionError):
        _assert_supervisor(ast.unparse(ast.fix_missing_locations(tree)))


@pytest.mark.parametrize("old,new", [
    ("data=read_scope();unit=", "data=read_scope(active=False);unit="),
    ("name=='active' and not active", "name=='active'"),
    ("os.O_EXCL|", ""), ("os.O_NOFOLLOW|", ""),
    ("os.fsync(fd)", "pass"), ("try: os.fsync(directory)", "try: pass"),
    ("'--property=ConditionPathExists='+data['active']", "'--property=ConditionPathExists=/tmp/always'"),
    ("'--property=PartOf='+data['parent_unit']", "'--property=PartOf=unrelated.service'"),
    ("'--property=After='+data['parent_unit']", "'--property=After=unrelated.service'"),
    ("canonical(data)!=raw", "False"),
    ("'units','active','identities'}", "'units','active','identities','extra'}"),
    ("identity(path)!=data['identities'][name]", "False"),
    ("record=Path(data['units'])", "record=Path('/tmp')"),
    ("fd=os.open(pending,", "fd=os.open(record,"),
    ("os.link(pending,record,follow_symlinks=False)", "os.replace(pending,record)"),
    ("os.link(pending,record,follow_symlinks=False)", "os.rename(pending,record)"),
    ("os.link(pending,record,follow_symlinks=False)", "os.link(pending,record,follow_symlinks=True)"),
    ("pending.unlink()", "pass"),
])
def test_scope_contract_rejects_launch_gate_ledger_and_ownership_mutations(old, new):
    source = (TESTS / "scope_control.py").read_text(encoding="utf-8")
    assert old in source
    with pytest.raises(AssertionError):
        _assert_scope(source.replace(old, new))


@pytest.mark.parametrize("mutation", ["publish_before_file_fsync", "unlink_before_publish", "return_before_directory_fsync"])
def test_unit_ledger_requires_fsync_atomic_noreplace_publication_and_directory_fsync_before_launch(mutation):
    source = (TESTS / "scope_control.py").read_text(encoding="utf-8")
    _assert_scope(source)
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bound_command")
    link = next(node for node in function.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "link")
    if mutation == "publish_before_file_fsync":
        write = next(node for node in function.body if isinstance(node, ast.Try))
        function.body.remove(link)
        function.body.insert(function.body.index(write), link)
    elif mutation == "unlink_before_publish":
        unlink = next(node for node in function.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                      and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "unlink")
        function.body.remove(unlink)
        function.body.insert(function.body.index(link), unlink)
    else:
        directory_sync = next(node for node in function.body if isinstance(node, ast.Try)
                              and ast.dump(node.body[0]) == ast.dump(ast.parse("os.fsync(directory)").body[0]))
        result = next(node for node in function.body if isinstance(node, ast.Return))
        function.body.remove(result)
        function.body.insert(function.body.index(directory_sync), result)
    with pytest.raises(AssertionError):
        _assert_scope(ast.unparse(ast.fix_missing_locations(tree)))


@pytest.mark.parametrize("name", BOUND_HELPERS)
@pytest.mark.parametrize("mutation", ["removed", "after_popen", "conditional"])
def test_helper_contract_rejects_missing_or_late_durable_binding(name, mutation):
    source = (TESTS / name).read_text(encoding="utf-8")
    binding = "if unit: argv=" + BOUND_HELPERS[name] + ".bound_command(argv)"
    assert source.count(binding) == 1
    if mutation == "after_popen":
        pattern = re.escape(binding) + r"\n( +)(process=original\(argv,\*args,\*\*kwargs\))"
        source, changed = re.subn(pattern, lambda match: match[2] + "\n" + match[1] + binding, source)
        assert changed == 1
    else:
        source = source.replace(binding, "pass" if mutation == "removed" else binding.replace("if unit:", "if False:"))
    with pytest.raises(AssertionError):
        _assert_bound_helper(name, source)


@pytest.mark.parametrize("old,new", [
    ("for fault in ('timeout','cancel'):", "for fault in ('timeout',):"),
    ("signal.setitimer(signal.ITIMER_REAL,8)", "signal.setitimer(signal.ITIMER_REAL,0)"),
    ("timeout=8 if fault=='timeout' else 20", "timeout=80 if fault=='timeout' else 20"),
    ("def cancel(signum,frame): os.kill(os.getpid(),signal.SIGTERM)", "def cancel(signum,frame): pass"),
    ("def cancel(signum,frame): os.kill(os.getpid(),signal.SIGTERM)", "def cancel(signum,frame): raise KeyboardInterrupt"),
    ("os.kill(os.getpid(),signal.SIGTERM)", "os.kill(os.getpid(),signal.SIGINT)"),
    ("command=control.bound_command([", "command=list(["),
    ("subprocess.run(command,check=True,capture_output=True,timeout=5)", "pass"),
    ("subprocess.Popen(['/bin/sleep','600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)", "pass"),
    ("(Path(data['ipc'])/'synthetic-ipc').write_text('synthetic only\\n')", "pass"),
    ("(Path(data['work'])/'synthetic-file').write_text('synthetic only\\n')", "pass"),
    ("result.stdout.strip()!='FAULT_FIXTURE_READY '+data['id']", "False"),
    ("not clean or reason!=wanted", "False"),
    ("if listing.returncode or listing.stdout.strip() or listing.stderr:", "if False:"),
    ("if Path(data['work']).exists() or Path(data['active']).parent.exists():", "if False:"),
    ("for point in ('allocation','handoff'):", "for point in ('allocation',):"),
    ("for point in ('allocation','handoff'):", "for point in ('handoff',):"),
    ("if point=='allocation': control.canonical=cancel_allocation", "if point=='allocation': pass"),
    ("if value.get('format')=='pult-controller-ci-scope-v1': os.kill(os.getpid(),signal.SIGTERM)", "if False: pass"),
    ("except KeyboardInterrupt: interrupted=True", "except KeyboardInterrupt: pass"),
    ("finally: control.canonical=original", "finally: pass"),
    ("if not interrupted or before!=after:", "if before!=after:"),
    ("if not interrupted or before!=after:", "if not interrupted:"),
    ("with supervisor.create_scope(control):\n                os.kill(os.getpid(),signal.SIGTERM)",
     "with supervisor.create_scope(control):\n                pass"),
    ("with supervisor.create_scope(control) as data:", "if True:"),
    ("try: os.write(fd,b'{\"partial\":');os.fsync(fd)", "try: pass"),
])
def test_fault_proof_contract_rejects_missing_native_evidence_or_cleanup_checks(old, new):
    source = (TESTS / "supervision_proof.py").read_text(encoding="utf-8")
    assert old in source
    with pytest.raises(AssertionError):
        _assert_functions(source.replace(old, new), FAULT_CONTRACT)


@pytest.mark.parametrize("step,old,new", [
    (GUARD_STEP, GUARD_PATH, "backend/tests/test_custodian_v2_source_ci.py"),
    (NATIVE_STEP, "unshare --net", "unshare --pid --fork --mount-proc --net"),
    (NATIVE_STEP, "--net ", ""),
    (NATIVE_STEP, "-I ", ""), (NATIVE_STEP, "-S ", ""), (NATIVE_STEP, "-B ", ""),
    (NATIVE_STEP, "sudo -n -- ", ""),
    (NATIVE_STEP, "/usr/bin/python3.12", "python"),
    (NATIVE_STEP, "ci_gate.py", "run_gate.py"),
    (NATIVE_STEP, "--reviewed-by-main", "--focus-invalid-caller"),
    (NATIVE_STEP, "--reviewed-by-main", "--reviewed-by-main || true"),
    (OPTIMIZED_STEP, '"--net", ', '"--pid", "--net", '),
    (OPTIMIZED_STEP, '"--net", ', ""),
    (OPTIMIZED_STEP, '"-I", ', ""), (OPTIMIZED_STEP, '"-S", ', ""),
    (OPTIMIZED_STEP, '"-B", ', ""), (OPTIMIZED_STEP, '"-O",', ""),
    (OPTIMIZED_STEP, "result.returncode != 2", "False"),
    (OPTIMIZED_STEP, "result.stderr", "False"),
    (OPTIMIZED_STEP, 'report != {"outcome": "NOT_RUN", "reason": reason}', 'report.get("reason") != reason'),
    (OPTIMIZED_STEP, "optimized_ci_runtime_refused", "arbitrary_failure"),
    (OPTIMIZED_STEP, "optimized_test_runtime_refused", "arbitrary_failure"),
    (OPTIMIZED_STEP, "optimized_verifier_refused", "arbitrary_failure"),
    (OPTIMIZED_STEP, '("verify_receipt.py", "optimized_verifier_refused"),', ""),
])
def test_controller_ci_rejects_command_and_optimized_result_mutations(step, old, new):
    workflow = _workflow()
    target = next(item for item in workflow["jobs"]["synthetic"]["steps"] if item["name"] == step)
    assert old in target["run"]
    target["run"] = target["run"].replace(old, new)
    with pytest.raises(AssertionError):
        _assert_ci(workflow)


@pytest.mark.parametrize("step", NEW_STEPS)
@pytest.mark.parametrize("key,value", [("if", "false"), ("continue-on-error", "true"),
                                      ("uses", "actions/upload-artifact@" + "0" * 40)])
def test_controller_steps_cannot_skip_continue_or_upload(step, key, value):
    workflow = _workflow()
    next(item for item in workflow["jobs"]["synthetic"]["steps"] if item["name"] == step)[key] = value
    with pytest.raises(AssertionError):
        _assert_ci(workflow)


def test_controller_step_order_cannot_bypass_independent_postcheck():
    workflow = _workflow()
    steps = workflow["jobs"]["synthetic"]["steps"]
    index = next(i for i, step in enumerate(steps) if step["name"] == NATIVE_STEP)
    steps[index], steps[index + 1] = steps[index + 1], steps[index]
    with pytest.raises(AssertionError):
        _assert_ci(workflow)


@pytest.mark.parametrize("event", ["pull_request", "push"])
@pytest.mark.parametrize("path", [GUARD_PATH, DOC_PATH])
def test_controller_security_inputs_require_both_exact_trigger_paths(event, path):
    workflow = _workflow()
    workflow["on"][event]["paths"].remove(path)
    with pytest.raises(AssertionError):
        _assert_ci(workflow)


@pytest.mark.parametrize("name", PINS)
def test_each_controller_raw_pin_rejects_source_mutation(name):
    blobs = {key: (SOURCE / key).read_bytes() for key in PINS}
    blobs[name] += b"\n"
    with pytest.raises(AssertionError):
        _assert_pins(blobs)


@pytest.mark.parametrize("key,value", [("status", "APPROVED"), ("operator_approval", "yes"),
    ("admitted_operations", ["serve"]), ("working_credentials_included", True),
    ("database_or_pending_included", True), ("installation_service_or_network_actions", True),
    ("lifecycle_limit", "RESTART_ADOPTION"), ("max_calls", 65), ("extra", "activation")])
def test_controller_draft_rejects_activation_and_bound_mutations(key, value):
    profile = deepcopy(json.loads((SOURCE / "source-profile.json").read_bytes()))
    profile[key] = value
    with pytest.raises(AssertionError):
        _assert_draft(profile)


@pytest.mark.parametrize("old,new", [("count==52", "count==1"),
    ("count==result.testsRun==52", "count==result.testsRun==51"),
    ("not result.skipped", "True"), ("not result.expectedFailures", "True"),
    ("not result.unexpectedSuccesses", "True"), ("before==after", "True"),
    ("os.geteuid()!=0", "False"), ("strip()!='systemd'", "strip()!='python'"),
    ("==['lo']", "==['lo','eth0']"), ("dir=scope['work']", "dir='/tmp'"),
    ("assert base.parent==Path(scope['work'])", "assert True"),
    ("source_hashes_before=before", "source_hashes_before=after"),
    ("LOCAL_MOCK_ONLY_ONE_LIVE_CONTROLLER_NOT_RESTART_DEPLOYMENT_OR_REMOTE_FRESHNESS", "DEPLOYMENT_ADMITTED")])
def test_native_gate_contract_rejects_weakened_proof(old, new):
    source = (TESTS / "run_gate.py").read_text(encoding="utf-8")
    assert old in source
    with pytest.raises(AssertionError):
        _assert_native_gate(source.replace(old, new))


@pytest.mark.parametrize("name", REFUSALS)
def test_optimized_guard_cannot_follow_side_effects(name):
    source = (TESTS / name).read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_entry(name, source.replace("def main():", "def main():\n    print('native work started')", 1))


@pytest.fixture(scope="module")
def receipt_verifier():
    # This test helper has stdlib-only imports and no native work at import time.
    # Do not import fixtures: their module-level loaders execute pinned candidates.
    spec = importlib.util.spec_from_file_location("controller_static_receipt_verifier", TESTS / "verify_receipt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_report():
    hashes = {"ops/backup/custodian_v2/controller/observer_v2.py": PINS["observer_v2.py"]}
    return dict(format="pult-controller-ci-native-proof-v1", outcome="PASS", full_gate=True,
                scope="LOCAL_MOCK_ONLY_ONE_LIVE_CONTROLLER_NOT_RESTART_DEPLOYMENT_OR_REMOTE_FRESHNESS",
                tests=52, discovered=52, errors=0, failures=0, skips=0, expected_failures=0,
                unexpected_successes=0, errors_detail=[], failures_detail=[], issues=[],
                source_hashes_before=hashes, source_hashes_after=dict(hashes), sources_unchanged=True,
                units_after_empty=True, native_test_tree_removed=True, elapsed_seconds=198.566,
                new_paid_resources=0, additional_spend_rub=0)


def test_pure_receipt_summary_accepts_exact_complete_pass(receipt_verifier):
    assert receipt_verifier.valid_summary(_valid_report()) is True


@pytest.mark.parametrize("elapsed", [0, 0.0, 198.566, 900, 900.0])
def test_pure_receipt_elapsed_accepts_inclusive_finite_bounds(receipt_verifier, elapsed):
    report = _valid_report()
    report["elapsed_seconds"] = elapsed
    assert receipt_verifier.valid_summary(report) is True


@pytest.mark.parametrize("elapsed", [True, False, None, "198.566", [], {}, -1, -0.001,
                                    900.001, 901, 2**64, float("nan"), float("inf"), -float("inf")])
def test_pure_receipt_elapsed_rejects_nonfinite_wrong_type_or_outside_bounds(receipt_verifier, elapsed):
    report = _valid_report()
    report["elapsed_seconds"] = elapsed
    assert receipt_verifier.valid_summary(report) is False


@pytest.mark.parametrize("old,new", [
    ("type(elapsed) in (int,float)", "isinstance(elapsed,(int,float))"),
    ("type(elapsed) in (int,float)", "True"),
    ("math.isfinite(elapsed)", "True"),
    ("0<=elapsed<=900", "True"),
    ("0<=elapsed<=900", "-1<=elapsed<=900"),
    ("0<=elapsed<=900", "0<=elapsed<=901"),
    ("0<=elapsed<=900", "0<elapsed<900"),
    ("require(type(elapsed) in (int,float) and math.isfinite(elapsed) and 0<=elapsed<=900)", "pass"),
])
def test_receipt_elapsed_ast_rejects_finite_type_and_bound_gate_mutations(old, new):
    source = (TESTS / "verify_receipt.py").read_text(encoding="utf-8")
    _assert_elapsed_contract(source)
    assert old in source
    with pytest.raises(AssertionError):
        _assert_elapsed_contract(source.replace(old, new))


@pytest.mark.parametrize("key,value", [("tests", 51), ("discovered", 53), ("tests", True),
    ("full_gate", False), ("full_gate", 1), ("outcome", "FAIL"),
    ("errors", 1), ("failures", 1), ("skips", 1), ("expected_failures", 1),
    ("unexpected_successes", 1), ("errors", False), ("failures", False),
    ("skips", False), ("expected_failures", False), ("unexpected_successes", False),
    ("sources_unchanged", False), ("units_after_empty", False), ("native_test_tree_removed", False),
    ("issues", ["failure"]), ("errors_detail", ["failure"]), ("failures_detail", ["failure"]),
    ("source_hashes_before", {}), ("source_hashes_after", {}),
    ("source_hashes_before", {"source": "not-a-digest"}),
    ("scope", "DEPLOYMENT_ADMITTED"), ("new_paid_resources", 1), ("new_paid_resources", False),
    ("additional_spend_rub", 1), ("additional_spend_rub", False)])
def test_pure_receipt_summary_rejects_incomplete_or_false_proof(receipt_verifier, key, value):
    report = _valid_report()
    report[key] = value
    assert receipt_verifier.valid_summary(report) is False


@pytest.mark.parametrize("key", ["reader_credential", "state_file", "raw_exception", "payload"])
def test_pure_receipt_summary_rejects_private_extra_fields(receipt_verifier, key):
    report = _valid_report()
    report[key] = "PRIVATE_MUST_NOT_BE_EXPORTED"
    assert receipt_verifier.valid_summary(report) is False


@pytest.mark.parametrize("key", sorted(_valid_report()))
def test_pure_receipt_summary_requires_every_exact_field(receipt_verifier, key):
    report = _valid_report()
    del report[key]
    assert receipt_verifier.valid_summary(report) is False
