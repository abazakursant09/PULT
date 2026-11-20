"""Real systemd/UID/Lease/SQLite/IPC gate with explicit signed Unix mock S3.

Tests one approved synthetic intent. No OpenSSH/TLS/provider/IAM/two-host proof;
those are separate gates. Original sources remain unchanged. Test adapters are
explicitly pinned and never selected by a deployed production profile.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from uuid import uuid4

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2/synthetic_ssh_s3'
BASE=REPO/'ops/backup/custodian_v2'
PROOFS=REPO/'ops/backup/tests/custodian_v2'
ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
CONTROL_SHA='d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'
SOURCE_PINS_SHA='45ee3546f139226e11f0e004c5f926873c26fe76c807c7040ab07d2bf9ebb1a1'

def need(ok):
    if not ok:raise RuntimeError('NATIVE_STORAGE_CHAIN_REFUSED')
def sha(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def canonical(value):return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def identity(file):
    info=file.lstat();return dict(device=info.st_dev,inode=info.st_ino)
def ticks(pid):return int(Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split()[19])
def put(file,raw,mode=0o644):file.write_bytes(raw);file.chmod(mode)
def load(file,name,wanted=None):
    raw=file.read_bytes()
    if wanted is not None:need(hashlib.sha256(raw).hexdigest()==wanted)
    spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;exec(compile(raw,str(file),'exec',dont_inherit=True,optimize=0),module.__dict__);return module
def check_frozen_fixture():
    file=HERE/'native-source-pins.json';need(sha(file)==SOURCE_PINS_SHA)
    for path,wanted in json.loads(file.read_bytes()).items():need(sha(REPO/path)==wanted)
def fault_trace(mode,methods,activation):
    expected={'happy':(['PUT','GET'],None),'write_unknown':(['PUT'],'write_unknown_after_put'),
              'read_fail':(['PUT','GET'],'read_fail_get_error')}
    need(mode in expected and (methods,activation)==expected[mode])
def state(unit):
    run=subprocess.run(['/usr/bin/systemctl','show',unit,'--property=MainPID,InvocationID,ActiveState,SubState,LoadState'],
        capture_output=True,text=True,timeout=3,check=True)
    return dict(row.split('=',1) for row in run.stdout.splitlines() if '=' in row)
def line(process,seconds=10):
    if not select.select([process.stdout],[],[],seconds)[0]:raise RuntimeError('bounded native line missing')
    raw=process.stdout.readline(8193);need(0<len(raw)<=8192 and raw.endswith(b'\n'));return raw
def group_empty(unit):
    file=Path('/sys/fs/cgroup/system.slice')/unit
    try:return 'populated 0' in (file/'cgroup.events').read_text().splitlines()
    except FileNotFoundError:return not file.exists()
def stop(unit):
    need(unit.startswith('pult-call-gate-') and unit.endswith('.service') and len(unit)==55)
    subprocess.run(['/usr/bin/systemctl','stop',unit],capture_output=True,timeout=7)
    if not group_empty(unit):
        subprocess.run(['/usr/bin/systemctl','kill','--kill-whom=all','--signal=SIGKILL',unit],capture_output=True,timeout=3)
        subprocess.run(['/usr/bin/systemctl','stop',unit],capture_output=True,timeout=7)
    need(group_empty(unit));subprocess.run(['/usr/bin/systemctl','reset-failed',unit],capture_output=True,timeout=3)

def broker(root):
    config=json.loads((root/'config.json').read_bytes())
    sys.path[:0]=[str(root/'mock_sources')]+[str(root/'release'/d) for d in ('candidate','codec','store','ownership','frozen','deps')]
    worker=load(root/'mock_sources/worker.py','fixed_signed_mock_worker')
    sys.addaudithook(worker.no_network)
    import crash_worker,httpx
    original=crash_worker.FakeObjects
    class RecordedFaultObjects(original):
        def handle(self,role,request):
            try:response=super().handle(role,request)
            except httpx.ReadError:
                activation=None
                if config['mode']=='write_unknown' and role=='writer' and request.method=='PUT':
                    need(len(list((root/'objects').iterdir()))==1)
                    activation='write_unknown_after_put'
                elif config['mode']=='read_fail' and role=='reader' and request.method=='GET':
                    activation='read_fail_get_error'
                if activation is not None:
                    with (root/'fault-activation.json').open('xb') as file:
                        file.write(canonical(dict(activation=activation)))
                raise
            return response
    crash_worker.FakeObjects=RecordedFaultObjects
    # This process alone owns mock object storage and both ARTIFICIAL keys.
    return worker.broker(root,config)

def case(base,original,helper,control,mode):
    root=helper.fixture(base,original,'happy');root_id=identity(root)
    host=json.loads((root/'host.json').read_bytes());host.update(writer_uid=48002,writer_gid=48003)
    put(root/'host.json',canonical(host));entry=root/'new';entry.mkdir(mode=0o755)
    names=('action_profile.py','action_protocol.py','action_host.py','action_child.py','action_root.py',
        'live_event_store.py','live_durable_protocol.py','controller_entry.py','ssh_ingress.py',
        'native_actor_adapter.py','native_controller_adapter.py')
    for name in names:shutil.copyfile((HERE if name.startswith('native_') else SOURCE)/name,entry/name);(entry/name).chmod(0o644)
    shutil.copyfile(PROOFS/'controller/scope_control.py',entry/'scope_control.py');(entry/'scope_control.py').chmod(0o644)
    sources={name:dict(path=str(entry/('action_'+name+'.py')),sha256=sha(entry/('action_'+name+'.py')))
        for name in ('profile','protocol','host','root')}
    sources['child']=dict(path=str(entry/'native_actor_adapter.py'),sha256=sha(entry/'native_actor_adapter.py'))
    for name,file in (('store','live_event_store.py'),('durable_protocol','live_durable_protocol.py')):
        sources[name]=dict(path=str(entry/file),sha256=sha(entry/file))
    for name,file in (('constructor','host/staged_host.py'),('stop','stop/constructor_stop.py'),
                      ('supervisor','controller/supervisor_v2.py'),('core','controller/frozen/admission.py')):
        shutil.copyfile(BASE/file,entry/(name+'.py'));(entry/(name+'.py')).chmod(0o644)
        sources[name]=dict(path=str(entry/(name+'.py')),sha256=sha(entry/(name+'.py')))
    for directory,bits in (('control',0o700),('admission',0o755),('ipc',0o750),('writer-private',0o700),('objects',0o700)):
        (root/directory).mkdir(mode=bits);(root/directory).chmod(bits)
    (root/'mock_sources').mkdir(mode=0o755)
    for name,relative in (('worker.py','staged/worker.py'),('crash_worker.py','durable/crash_worker.py')):
        shutil.copyfile(PROOFS/relative,root/'mock_sources'/name);(root/'mock_sources'/name).chmod(0o644)
    os.chown(root/'ipc',47003,48003);os.chown(root/'writer-private',48002,48003)
    put(root/'control/constructor-stop.lock',b'',0o600)
    put(root/'writer-private/key.json',canonical(dict(access_key='FAKE_ACTION_WRITER',secret_key='synthetic-writer-not-a-working-key')),0o400)
    os.chown(root/'writer-private/key.json',48002,48003)
    config=dict(mode=mode,broker_path=str(root/'broker.sock'),reader_key=host['reader_credential_file'],
        writer_key=str(root/'writer-private/key.json'),endpoint=host['s3']['endpoint'],bucket=host['s3']['bucket'],
        binding=dict(scope=host['scope'],generation=host['generation']),owner_uid=47003,owner_gid=47004,writer_uid=48002,writer_gid=48003)
    put(root/'config.json',canonical(config))
    runtime_parent=Path(control.read_scope()['ipc'])
    runtime=runtime_parent/('i-'+uuid4().hex[:8])
    need(runtime.parent==runtime_parent and runtime_parent.parent.parent==Path('/run') and not runtime.exists())
    runtime.mkdir(mode=0o750);os.chown(runtime,0,50002)
    runtime_id=identity(runtime);units=[];processes=[];result=None
    key_pins={name:sha(Path(file)) for name,file in (('reader',host['reader_credential_file']),('writer',root/'writer-private/key.json'))}
    try:
        mock=subprocess.Popen(['/usr/bin/python3.12','-I','-S','-B',str(HERE/'native_storage_chain.py'),'--mock',str(root)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV,close_fds=True)
        processes.append(mock);need(json.loads(line(mock))['event']=='BROKER_READY')
        put(entry/'native-bridge.json',canonical(dict(path=str(root/'broker.sock'),identity=identity(root/'broker.sock'),
            pid=mock.pid,start_ticks=ticks(mock.pid))))
        root_unit='pult-call-gate-'+uuid4().hex+'.service'
        role_units={r:'pult-call-gate-'+uuid4().hex+'.service' for r in ('custodian','writer')}
        prospective=[root_unit,*role_units.values()]
        need(all(state(unit).get('LoadState')=='not-found' and group_empty(unit) for unit in prospective))
        units=prospective
        db=sqlite3.connect(host['state_file']);seq,digest=db.execute('SELECT seq,hash FROM anchor_v2').fetchone();db.close()
        action=dict(format='pult-synthetic-ssh-s3-profile-v1',mode='ONE_SYNTHETIC_INTENT_S3_STORAGE_ONLY',sources=sources,controller=None,
            control=dict(root=str(root/'control'),identity=identity(root/'control'),lock_identity=identity(root/'control/constructor-stop.lock')),
            admission=dict(root=str(root/'admission'),identity=identity(root/'admission')),
            custodian=dict(uid=47003,gid=47004,unit=role_units['custodian'],host_profile=str(root/'host.json'),
                host_profile_sha256=sha(root/'host.json'),ipc_directory=str(root/'ipc'),ipc_identity=identity(root/'ipc')),
            writer=dict(uid=48002,gid=48003,unit=role_units['writer'],credential_file=str(root/'writer-private/key.json'),
                credential_identity=identity(root/'writer-private/key.json'),access_key='FAKE_ACTION_WRITER'),
            operation=dict(id=str(uuid4()),attempt=str(uuid4()),event=dict(purge_id=str(uuid4()),subject_id=str(uuid4()),phase='intent'),
                base=dict(sequence=seq,digest=digest)),network=dict(peer_ipv4='1.1.1.1'))
        plan=dict(format='pult-synthetic-one-shot-launch-v1',entry_sha256=sha(entry/'native_controller_adapter.py'),
            runtime_profile=str(root/'runtime-profile.json'),controller_unit=root_unit,action=action,
            ingress=dict(uid=50001,gid=50002,directory=str(runtime),socket_file=str(runtime/'control.sock'),
                config_file=str(runtime/'config.json'),source=dict(path=str(entry/'ssh_ingress.py'),sha256=sha(entry/'ssh_ingress.py'))))
        put(root/'launch.json',canonical(plan));approval=sha(root/'launch.json')
        argv=['/usr/bin/systemd-run','--quiet','--pipe','--no-ask-password','--expand-environment=no','--unit='+root_unit,
            '--property=Type=exec','--property=ExitType=main','--property=User=0','--property=Group=0','--property=Restart=no',
            '--property=KillMode=control-group','--property=KillSignal=SIGTERM','--property=FinalKillSignal=SIGKILL',
            '--property=SendSIGKILL=yes','--property=TimeoutStopSec=2','--property=RuntimeMaxSec=90','--property=PrivateNetwork=yes',
            '--property=NoNewPrivileges=yes','--property=ConditionPathExists=!'+str(root/'control/constructor-stop.claim'),
            '--setenv='+control.ENV+'='+os.environ[control.ENV],'/usr/bin/python3.12','-I','-S','-B',
            str(entry/'native_controller_adapter.py'),str(root/'launch.json'),approval]
        process=subprocess.Popen(control.bound_command(argv),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,close_fds=True,env=ENV);processes.append(process)
        published=json.loads(line(process,15))
        if published.get('format')!='pult-synthetic-ingress-published-v1':
            print('NATIVE_BOOTSTRAP_DIAGNOSTIC '+json.dumps(published,sort_keys=True),flush=True)
            raise RuntimeError('native bootstrap refused')
        # Direct ingress proves the SAME Unix boundary; real SSH is separately
        # tested in native_ssh_admission, not falsely claimed by this fixture.
        ingress=subprocess.Popen(['/usr/bin/setpriv','--reuid=50001','--regid=50002','--clear-groups','--bounding-set=-all',
            '--inh-caps=-all','--ambient-caps=-all','--no-new-privs','/usr/bin/python3.12','-I','-S','-B',
            str(entry/'ssh_ingress.py'),str(runtime/'config.json'),published['config_sha256']],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,
            env=dict(ENV,SSH_ORIGINAL_COMMAND='pult-synthetic-one-v1'));processes.append(ingress)
        out,err=ingress.communicate(input=b'RUN_ONE_SYNTHETIC_STORAGE_ACTION_V1\n',timeout=55)
        root_out,root_err=process.communicate(timeout=12)
        success=mode=='happy'
        expected=(b'{"format":"pult-synthetic-storage-only-reply-v1",'
            b'"purge":false,"restore":false,"status":"complete","storage_only":true}\n')
        if success:
            ok=ingress.returncode==process.returncode==0 and out==expected and not err and not root_out and not root_err
            if not ok:
                diagnostic=dict(ingress_returncode=ingress.returncode,root_returncode=process.returncode,
                    ingress_stdout_bytes=len(out),ingress_stderr_bytes=len(err),root_stderr_bytes=len(root_err))
                try:diagnostic['root_safe_diagnostic']=json.loads(root_out)
                except ValueError:diagnostic['root_stdout_bytes']=len(root_out)
                print('NATIVE_EXCHANGE_DIAGNOSTIC '+json.dumps(diagnostic,sort_keys=True),flush=True)
            need(ok)
        else:need(ingress.returncode!=0 and process.returncode!=0 and not out)
        need(all(group_empty(u) for u in role_units.values()))
        need((root/'control/constructor-stop.claim').is_file() and not (root/'admission/constructor-stop.claim').exists())
        db=sqlite3.connect(host['state_file']);meta=db.execute('SELECT seq,revision,pending FROM anchor_v2').fetchone()
        phases=[row[0] for row in db.execute('SELECT phase FROM attempts_v2')];db.close()
        calls=[json.loads(row) for row in (root/'calls.jsonl').read_text().splitlines()] if (root/'calls.jsonl').exists() else []
        activation_file=root/'fault-activation.json'
        activation=json.loads(activation_file.read_bytes())['activation'] if activation_file.exists() else None
        fault_trace(mode,[c['method'] for c in calls],activation)
        if success:need(meta==(1,3,None) and phases==['COMMITTED'] and [c['method'] for c in calls]==['PUT','GET'])
        else:need(meta==(0,2,action['operation']['attempt']) and phases==['DISPATCHED'])
        need(key_pins=={name:sha(Path(file)) for name,file in (('reader',host['reader_credential_file']),('writer',root/'writer-private/key.json'))})
        result=dict(case=mode,outcome='PASS',actual_sqlite_state=list(meta),actual_phases=phases,
            methods=[c['method'] for c in calls],whole_groups_empty=True,permanent_fence_retained=True,keys_unchanged=True,
            fault_activation=activation,
            real_systemd_roles=True,explicit_mock_s3=True,real_openssh=False,real_selectel_iam_tls=False,
            actor_private_netns_verified=True,
            purge=False,restore=False,deployment_admitted=False,root=str(root),root_identity=root_id,units=units)
        return result
    finally:
        for unit in units:stop(unit)
        for process in reversed(processes):
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)
        need(runtime.parent==runtime_parent and runtime.name.startswith('i-') and len(runtime.name)==10 and identity(runtime)==runtime_id)
        need(set(p.name for p in runtime.iterdir())<={'control.sock','config.json'})
        for file in runtime.iterdir():file.unlink()
        runtime.rmdir()

def main():
    need(not sys.flags.optimize and sys.platform=='linux' and os.geteuid()==0 and sys.version_info[:2]==(3,12)
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
    check_frozen_fixture()
    if len(sys.argv)==3 and sys.argv[1]=='--mock':return broker(Path(sys.argv[2]))
    need(sys.argv[1:]==['--reviewed-by-main'])
    control=load(PROOFS/'controller/scope_control.py','native_storage_scope',CONTROL_SHA)
    scope=control.read_scope();ENV[control.ENV]=os.environ[control.ENV]
    helper=load(PROOFS/'host/run_gate.py','accepted_native_fixture')
    import tempfile
    base=Path(tempfile.mkdtemp(prefix='synthetic-live-',dir=scope['work']));base.chmod(0o755)
    original,_,_=helper.template(base,REPO,PROOFS/'wheels')
    results=[case(base,original,helper,control,mode) for mode in ('happy','write_unknown','read_fail')]
    print('NATIVE_STORAGE_RESULT '+json.dumps(dict(outcome='PASS',cases=results,full_provider_proof=False,new_paid_resources=0),sort_keys=True),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
