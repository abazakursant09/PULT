"""Private, root-owned, real-systemd one-action proof. Synthetic local mock only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2/action'
WHEELS=HERE.parent/'wheels'
ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
MODES=('happy','write_unknown','read_fail','kill_controller_after_put','freeze_roles_at_put')

def load(file,name):
    spec=importlib.util.spec_from_file_location(name,file); m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m

def sha(file): return hashlib.sha256(file.read_bytes()).hexdigest()
def canonical(value): return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def put(file,raw,mode=0o644): file.write_bytes(raw);file.chmod(mode)
def ident(file):
    s=file.lstat();return dict(device=s.st_dev,inode=s.st_ino)
def ticks(pid): return int(Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split()[19])
def state(unit):
    r=subprocess.run(['/usr/bin/systemctl','show',unit,'--property=MainPID,InvocationID,ActiveState,SubState,LoadState,ConditionResult'],capture_output=True,text=True,timeout=5,check=True)
    return dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
def empty(unit):
    file=Path('/sys/fs/cgroup/system.slice')/unit
    return not file.exists() or not any(f.read_text().strip() for f in file.rglob('cgroup.procs'))
def stop(unit):
    subprocess.run(['/usr/bin/systemctl','stop',unit],capture_output=True,timeout=7)
    if not empty(unit):
        subprocess.run(['/usr/bin/systemctl','kill','--kill-whom=all','--signal=SIGKILL',unit],capture_output=True,timeout=4)
        subprocess.run(['/usr/bin/systemctl','stop',unit],capture_output=True,timeout=7)
    assert empty(unit),'OWNED_GROUP_NOT_EMPTY'
    subprocess.run(['/usr/bin/systemctl','reset-failed',unit],capture_output=True,timeout=4)
def line(process,seconds=20):
    raw=bytearray();deadline=time.monotonic()+seconds
    while len(raw)<16384:
        left=deadline-time.monotonic()
        if left<=0 or not select.select([process.stdout],[],[],left)[0]: raise RuntimeError('FIXTURE_LINE_TIMEOUT')
        part=os.read(process.stdout.fileno(),1)
        if not part: raise RuntimeError('FIXTURE_LINE_EOF')
        if part==b'\n':return bytes(raw)
        raw.extend(part)
    raise RuntimeError('FIXTURE_LINE_BOUND')

def broker(case):
    config=json.loads((case/'config.json').read_bytes())
    sys.path[:0]=[str(case/'mock_sources')]+[str(case/'release'/d) for d in ('candidate','codec','store','ownership','frozen','deps')]
    worker=load(case/'mock_sources/worker.py','fixture_mock_worker')
    sys.addaudithook(worker.no_network)
    if config['mode'] in ('kill_controller_after_put','freeze_roles_at_put'):
        import crash_worker
        original=crash_worker.FakeObjects
        class PausedObjects(original):
            def __init__(self,directory):
                def checkpoint(stage):
                    chosen='after-put-before-ack' if config['mode']=='kill_controller_after_put' else 'before-put'
                    if stage==chosen:
                        worker.emit('BROKER_CHECKPOINT',stage=stage)
                        if not select.select([sys.stdin],[],[],25)[0] or sys.stdin.readline()!='CONTINUE\n':
                            raise RuntimeError('SYNTHETIC_CHECKPOINT_CONTROL_REFUSED')
                super().__init__(directory,checkpoint)
        crash_worker.FakeObjects=PausedObjects  # Mock storage fixture ONLY.
    return worker.broker(case,config)

def case(base,original,helper,mode):
    root=helper.fixture(base,original,'happy')
    root_id=ident(root); processes=[]; units=[]; result=None
    host=json.loads((root/'host.json').read_bytes())
    host.update(writer_uid=48002,writer_gid=48003)
    put(root/'host.json',canonical(host))
    common=root/'new';common.mkdir(mode=0o755)
    for file in ('action_profile.py','action_protocol.py','action_host.py','action_child.py','action_root.py','proof_controller.py','native_chain.py'):
        shutil.copyfile((SOURCE if file.startswith('action_') else HERE)/file,common/file);(common/file).chmod(0o644)
    shutil.copyfile(REPO/'ops/backup/tests/custodian_v2/controller/scope_control.py',common/'scope_control.py')
    (common/'scope_control.py').chmod(0o644)
    sources={name:dict(path=str(common/('action_'+name+'.py')),sha256=sha(common/('action_'+name+'.py')))
        for name in ('profile','protocol','host','child','root')}
    for name,relative in (
        ('constructor','host/staged_host.py'),('stop','stop/constructor_stop.py'),
        ('supervisor','controller/supervisor_v2.py'),('core','controller/frozen/admission.py')):
        dst=common/(name+'.py');shutil.copyfile(REPO/'ops/backup/custodian_v2'/relative,dst);dst.chmod(0o644)
        sources[name]=dict(path=str(dst),sha256=sha(dst))
    for directory,modebits in (('control',0o700),('admission',0o755),('ipc',0o750),('writer-private',0o700),('mock_sources',0o755),('objects',0o700)):
        (root/directory).mkdir(mode=modebits);(root/directory).chmod(modebits)
    os.chown(root/'ipc',47003,48003);os.chown(root/'writer-private',48002,48003)
    put(root/'control/constructor-stop.lock',b'',0o600)
    put(root/'writer-private/key.json',canonical(dict(access_key='FAKE_ACTION_WRITER',secret_key='synthetic-writer-not-a-working-key')),0o400)
    os.chown(root/'writer-private/key.json',48002,48003)
    for file,relative in (('worker.py','staged/worker.py'),('crash_worker.py','durable/crash_worker.py')):
        shutil.copyfile(REPO/'ops/backup/tests/custodian_v2'/relative,root/'mock_sources'/file)
        (root/'mock_sources'/file).chmod(0o644)
    config=dict(mode=mode,broker_path=str(root/'broker.sock'),reader_key=host['reader_credential_file'],
        writer_key=str(root/'writer-private/key.json'),endpoint=host['s3']['endpoint'],bucket=host['s3']['bucket'],
        binding=dict(scope=host['scope'],generation=host['generation']),owner_uid=47003,owner_gid=47004,writer_uid=48002,writer_gid=48003)
    put(root/'config.json',canonical(config))
    db=sqlite3.connect(host['state_file']);seq,genesis=db.execute('SELECT seq,hash FROM anchor_v2').fetchone();db.close()
    original_key=sha(Path(host['reader_credential_file']));writer_key=sha(root/'writer-private/key.json')
    try:
        mock=subprocess.Popen(['/usr/bin/python3.12','-I','-S','-B',str(common/'native_chain.py'),'--broker',str(root)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV,close_fds=True)
        processes.append(mock)
        assert json.loads(line(mock))['event']=='BROKER_READY'
        root_unit='pult-call-gate-'+uuid4().hex+'.service';units.append(root_unit)
        role_units={r:'pult-call-gate-'+uuid4().hex+'.service' for r in ('custodian','writer')};units.extend(role_units.values())
        profile=root/'action.json'
        cmd=['/usr/bin/systemd-run','--quiet','--pipe','--no-ask-password','--expand-environment=no','--unit='+root_unit,
            '--property=Type=exec','--property=ExitType=main','--property=RemainAfterExit=no','--property=User=0','--property=Group=0',
            '--property=KillMode=control-group','--property=KillSignal=SIGTERM','--property=FinalKillSignal=SIGKILL','--property=SendSIGKILL=yes',
            '--property=TimeoutStopSec=2','--property=Restart=no','--property=RuntimeMaxSec=65','--property=PrivateNetwork=yes',
            '--property=NoNewPrivileges=yes','--property=ConditionPathExists=!'+str(root/'control/constructor-stop.claim'),
            '--setenv=PULT_CONTROLLER_CI_SCOPE='+os.environ['PULT_CONTROLLER_CI_SCOPE'],
            '/usr/bin/python3.12','-I','-S','-B',str(common/'proof_controller.py'),str(profile)]
        control=load(REPO/'ops/backup/tests/custodian_v2/controller/scope_control.py','fixed_action_native_scope')
        cmd=control.bound_command(cmd)
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV,close_fds=True)
        processes.append(proc);first=line(proc).decode();assert first.startswith('CONTROLLER_READY '),first
        parent=state(root_unit);pid=int(first.split()[-1]);assert parent['MainPID']==str(pid)
        spec=dict(format='pult-synthetic-storage-chain-profile-v3',mode='ONE_INTENT_MOCK_ONLY_NO_PERMIT',sources=sources,
            controller=dict(unit=root_unit,invocation_id=parent['InvocationID'],boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),pid=pid,start_ticks=ticks(pid)),
            control=dict(root=str(root/'control'),identity=ident(root/'control'),lock_identity=ident(root/'control/constructor-stop.lock')),
            admission=dict(root=str(root/'admission'),identity=ident(root/'admission')),
            custodian=dict(uid=47003,gid=47004,unit=role_units['custodian'],host_profile=str(root/'host.json'),host_profile_sha256=sha(root/'host.json'),ipc_directory=str(root/'ipc'),ipc_identity=ident(root/'ipc')),
            writer=dict(uid=48002,gid=48003,unit=role_units['writer'],credential_file=str(root/'writer-private/key.json'),credential_identity=ident(root/'writer-private/key.json'),access_key='FAKE_ACTION_WRITER'),
            operation=dict(id=str(uuid4()),attempt=str(uuid4()),event=dict(purge_id=str(uuid4()),subject_id=str(uuid4()),phase='intent'),base=dict(sequence=seq,digest=genesis)),
            mock=dict(path=str(root/'broker.sock'),identity=ident(root/'broker.sock'),pid=mock.pid,start_ticks=ticks(mock.pid)))
        put(profile,canonical(spec));approval=sha(profile)
        proc.stdin.write((approval+'\n').encode());proc.stdin.flush()
        killed=mode=='kill_controller_after_put'
        frozen=mode=='freeze_roles_at_put'
        if killed or frozen:
            milestone=json.loads(line(mock,25));assert milestone['event']=='BROKER_CHECKPOINT',milestone
            if killed:
                subprocess.run(['/usr/bin/systemctl','kill','--kill-whom=main','--signal=SIGKILL',root_unit],capture_output=True,timeout=5,check=True)
                out,err=proc.communicate(timeout=8)
                assert proc.returncode!=0 and not out,(out,err)
                result=dict(outcome='CONTROLLER_KILLED_EXPECT_UNKNOWN')
                for unit in role_units.values():stop(unit)
                # The provider mock already durably created the object. Killing
                # the app controller cannot roll that independent storage back.
                mock.stdin.write(b'CONTINUE\n');mock.stdin.flush()
                assert len(list((root/'objects').iterdir()))==1
                gate=root/'admission/constructor-stop.claim'
                assert gate.is_file() and gate.read_bytes()==canonical(dict(format='pult-one-action-admission-v3',approval=approval,
                    operation=spec['operation']['id'],controller=spec['controller'],units=role_units))
                gate.unlink();fd=os.open(gate.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
            else:
                for unit in role_units.values():
                    actor=int(state(unit)['MainPID']);assert actor>0
                    os.kill(actor,signal.SIGSTOP)
                raw=line(proc,35);result=json.loads(raw)
                # Finish the independent mock request only AFTER the app groups
                # are confirmed stopped: prove cancellation is not atomic PUT.
                assert all(empty(u) for u in role_units.values())
                mock.stdin.write(b'CONTINUE\n');mock.stdin.flush()
                until=time.monotonic()+3
                while not list((root/'objects').iterdir()) and time.monotonic()<until:time.sleep(.02)
                assert len(list((root/'objects').iterdir()))==1
        else:
            raw=line(proc,50);result=json.loads(raw)
        if not killed:assert state(root_unit)['ActiveState']=='active','ORIGINAL_PARENT_MUST_STILL_BE_ACTIVE'
        if mode=='happy':
            assert result['outcome']=='PASS',result
            assert result['result']['purge'] is False and result['result']['restore'] is False
        elif not killed:assert result['outcome']=='UNKNOWN',result
        assert not (root/'admission/constructor-stop.claim').exists(),'ADMISSION_NOT_CLOSED'
        assert (root/'control/constructor-stop.claim').is_file(),'PERMANENT_FENCE_MISSING'
        # The original parent is still active. A queued child start must fail
        # its positive kernel condition without executing even the drop helper.
        late=[a for a in cmd if not a.startswith('--property=ConditionPathExists=')]
        late=[('--unit='+role_units['writer']) if a.startswith('--unit=') else a for a in late]
        cut=late.index('/usr/bin/python3.12');late=late[:cut]+['/usr/bin/printf','LATE_EXECUTED']
        late.insert(cut,'--property=ConditionPathExists='+str(root/'admission/constructor-stop.claim'))
        if killed:
            # Original Root's permanent negative condition rejects reactivation.
            stop(root_unit)  # Remove only the owned failed unit, not its fence.
            replay=subprocess.run(cmd,input=b'',capture_output=True,timeout=8,env=ENV)
            assert not replay.stdout and state(root_unit)['ConditionResult']=='no',(replay.stdout,replay.stderr)
        else:
            rejected=subprocess.run(late,capture_output=True,timeout=8,env=ENV)
            assert b'LATE_EXECUTED' not in rejected.stdout and state(role_units['writer'])['ConditionResult']=='no',(rejected.stdout,rejected.stderr)
        assert empty(role_units['writer']) and empty(role_units['custodian'])
        if not killed:
            proc.stdin.write(b'EXIT\n');proc.stdin.flush();out,err=proc.communicate(timeout=5)
            assert proc.returncode==0 and not out and not err,(out,err)
        db=sqlite3.connect(host['state_file'])
        meta=db.execute('SELECT seq,revision,pending FROM anchor_v2').fetchone()
        phases=[r[0] for r in db.execute('SELECT phase FROM attempts_v2')];db.close()
        calls=[json.loads(l) for l in (root/'calls.jsonl').read_text().splitlines()] if (root/'calls.jsonl').exists() else []
        if mode=='happy':assert meta==(1,3,None) and phases==['COMMITTED'] and [c['method'] for c in calls]==['PUT','GET'],(meta,phases,calls)
        else:assert meta[0]==0 and meta[1]==2 and meta[2]==spec['operation']['attempt'] and phases==['DISPATCHED'],(meta,phases,calls)
        assert sha(Path(host['reader_credential_file']))==original_key and sha(root/'writer-private/key.json')==writer_key
        return dict(case=mode,outcome='PASS',action=result['outcome'],meta=list(meta),phases=phases,calls=calls,
            separate_uids=True,late_start_rejected_while_parent_active=not killed,root_reactivation_rejected=killed,
            actual_role_freeze=frozen,independent_put_can_outlive_controller=killed or frozen,
            groups_empty=True,keys_unchanged=True,permanent_fence=True,
            evidence=dict(root=str(root),identity=root_id,units=units,reader_sha=original_key,writer_sha=writer_key))
    except BaseException:
        print('CASE_DEBUG '+str(root)+' '+repr(result),flush=True)
        for unit in units:
            r=subprocess.run(['/usr/bin/journalctl','--no-pager','-o','cat','-n','12','-u',unit],capture_output=True,text=True,timeout=3)
            print(unit+' '+r.stdout[-3000:],flush=True)
        raise
    finally:
        if units:
            stop(units[0])
            for unit in units[1:]: stop(unit)
        for p in reversed(processes):
            if p.poll() is None:p.kill()
            p.communicate(timeout=4)
        # On failure retain the complete synthetic tree for bounded diagnostics.
        if result is not None and result.get('outcome') in ('PASS','UNKNOWN'):
            assert root.parent==base and ident(root)==root_id and root.name.startswith('case-')

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_action_gate_refused"}');return 2
    if len(sys.argv)==3 and sys.argv[1]=='--broker':return broker(Path(sys.argv[2]))
    assert os.getuid()==0 and sys.platform=='linux' and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
    if sys.argv[1:]!=['--reviewed-by-main']:return 2
    assert sys.version_info[:2]==(3,12)
    control=load(REPO/'ops/backup/tests/custodian_v2/controller/scope_control.py','fixed_action_scope')
    assert sha(REPO/'ops/backup/tests/custodian_v2/controller/scope_control.py')=='d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'
    scope=control.read_scope();ENV[control.ENV]=os.environ[control.ENV]
    helper=load(REPO/'ops/backup/tests/custodian_v2/host/run_gate.py','exact_native_fixture')
    base=Path(tempfile.mkdtemp(prefix='a-',dir=scope['work']));base.chmod(0o755)
    original,_,_=helper.template(base,REPO,WHEELS)
    verifier=load(HERE/'verify_receipt.py','independent_action_postcheck')
    results=[]
    for mode in MODES:
        row=case(base,original,helper,mode);results.append(row);print(json.dumps(row,sort_keys=True),flush=True)
        row['independent_evidence']=verifier.observe(row)
    report=dict(format='pult-action-native-receipt-v3',outcome='PASS',scope='LOCAL_SYSTEMD_AUTHENTICATED_MOCK_STORAGE_ONLY',
        scope_id=scope['id'],scope_work=scope['work'],cases=results,
        retained_private_synthetic_tree=str(base),live_provider_iam_tls=False,new_paid_resources=0,new_persistent_accounts=0)
    target=HERE/('RUN_'+str(time.time_ns())+'.json')
    put(target,canonical(report));print('RECEIPT '+str(target),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
