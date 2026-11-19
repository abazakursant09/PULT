"""Bounded root fixture/proof for source intake only, not host deployment."""
import sys
sys.dont_write_bytecode=True
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import grp
import select
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import time

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SCOPE_SHA='d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'
SUPERVISOR_SHA='d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b'
ENTRY_SHA='48c238948c9f2c94ed485093b0a583ebcaf7dca760612622e5a01eebd063617c'
WORKER_SHA='c8853243aad6e27a04193af99d611cf1ece92ab56a0e925f9965e38bb5b344b2'
CATALOG_SHA='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'
COLD_SHA='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'
STAGED_SHA='f8010c39d6162dcac44b5e20cef1e79323c0e88a2e8a01eac34e8fe9163dbf02'
HELPER_SHA='d343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d'
UID,GID=47003,47004
CASES=('happy','capture_only','wrong_approval','old_profile','wrong_mode','extra_field',
 'duplicate_profile','noncanonical','profile_symlink','profile_hardlink','profile_userowned',
 'profile_writable','profile_acl','cold_tamper','stage_tamper','base_tamper','wheel_tamper',
 'dependency_tamper','release_extra','staged_inside_release','preloaded_app','preloaded_stage',
 'foreign_finder','foreign_path','optimized','profile_checkpoint','captured_source_checkpoint',
 'uncaptured_probe_checkpoint','loader_drift','repeat_capture','repeat_activate')

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(value): return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()

def load(file,name):
    spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

def profile(root):
    value=dict(format='pult-staged-source-intake-profile-v1',mode='SOURCES_ONLY_NO_HOST_CAPABILITY',
        release_root=str(root/'release'),cold_entry=str(root/'entry/cold_host.py'),
        staged_source=str(root/'entry/staged_ipc.py'))
    write_profile(root,canonical(value));return value

def write_profile(root,raw):
    (root/'profile.json').write_bytes(raw);(root/'profile.json').chmod(0o644)
    (root/'approval.txt').write_text(hashlib.sha256(raw).hexdigest()+'\n')

def mutate(root,mode):
    value=profile(root)
    if mode=='old_profile': value['format']='pult-schema2-cold-host-profile-v1'
    elif mode=='wrong_mode': value['mode']='STAGED_WRITE'
    elif mode=='extra_field': value['writer_uid']=48002
    elif mode=='staged_inside_release':
        shutil.copyfile(root/'entry/staged_ipc.py',root/'release/staged_ipc.py')
        value['staged_source']=str(root/'release/staged_ipc.py')
    write_profile(root,canonical(value))
    target=root/'profile.json'
    if mode=='duplicate_profile':
        raw=canonical(value);write_profile(root,b'{"format":'+json.dumps(value['format']).encode()+b','+raw[1:])
    elif mode=='noncanonical': write_profile(root,json.dumps(value,indent=2).encode())
    elif mode=='profile_symlink':
        target.rename(root/'original-profile');target.symlink_to(root/'original-profile')
    elif mode=='profile_hardlink': os.link(target,root/'profile-second-link')
    elif mode=='profile_userowned': os.chown(target,UID,GID)
    elif mode=='profile_writable': target.chmod(0o666)
    elif mode=='profile_acl':
        unknown=0xffffffff
        acl=struct.pack('<I',2)+b''.join(struct.pack('<HHI',*row) for row in (
            (1,6,unknown),(2,4,UID),(4,4,unknown),(16,4,unknown),(32,4,unknown)))
        os.setxattr(target,'system.posix_acl_access',acl)
    elif mode=='cold_tamper': (root/'entry/cold_host.py').write_bytes(b'raise RuntimeError("UNTRUSTED")\n')
    elif mode=='stage_tamper': (root/'entry/staged_ipc.py').write_bytes(b'raise RuntimeError("UNTRUSTED")\n')
    elif mode=='base_tamper': (root/'release/candidate/anchor_v2.py').write_bytes(b'raise RuntimeError("UNTRUSTED")\n')
    elif mode=='wheel_tamper':
        wheel=next((root/'release/wheels').iterdir());wheel.write_bytes(wheel.read_bytes()+b'x')
    elif mode=='dependency_tamper': (root/'release/deps/httpx/__init__.py').write_bytes(b'raise RuntimeError("UNTRUSTED")\n')
    elif mode=='release_extra': (root/'release/foreign.py').write_bytes(b'pass\n')

def read_line(child):
    raw=b'';deadline=time.monotonic()+10
    while b'\n' not in raw and len(raw)<4096:
        remaining=deadline-time.monotonic()
        if remaining<=0 or not select.select([child.stdout],[],[],remaining)[0]: raise RuntimeError('CHECKPOINT_TIMEOUT')
        part=os.read(child.stdout.fileno(),1)
        if not part: raise RuntimeError('CHECKPOINT_EOF')
        raw+=part
    return raw.strip()

def exercise(root,worker,mode):
    args=['/usr/bin/python3.12','-I','-S','-B']
    if mode=='optimized': args.append('-O')
    worker_mode=mode if mode in {'happy','capture_only','wrong_approval','preloaded_app','preloaded_stage',
        'foreign_finder','foreign_path','optimized','profile_checkpoint','captured_source_checkpoint',
        'uncaptured_probe_checkpoint','loader_drift','repeat_capture','repeat_activate'} else 'normal'
    child=subprocess.Popen(args+[str(worker),str(root),worker_mode],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,user=UID,group=GID,extra_groups=[],
        close_fds=True,start_new_session=True,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
    try: fd=os.pidfd_open(child.pid)
    except BaseException:
        child.kill();child.communicate(timeout=5);raise
    try:
        if mode in ('profile_checkpoint','captured_source_checkpoint','uncaptured_probe_checkpoint'):
            if read_line(child)!=b'CAPTURED': raise RuntimeError('MISSING_CAPTURE_CHECKPOINT')
            if mode=='profile_checkpoint':
                value=json.loads((root/'profile.json').read_bytes());value['mode']='STAGED_WRITE'
                (root/'profile.json').write_bytes(canonical(value))
            elif mode=='captured_source_checkpoint':
                (root/'entry/staged_ipc.py').write_bytes(
                    b'from pathlib import Path\nPath('+repr(str(root/'marker')).encode()+b').write_text("bad")\n')
            else:
                (root/'release/deps/httpx/uncaptured_probe.py').write_bytes(
                    b'from pathlib import Path\nPath('+repr(str(root/'marker')).encode()+b').write_text("bad")\n')
            child.stdin.write(b'CONTINUE\n');child.stdin.flush()
        out,err=child.communicate(timeout=30)
        if child.returncode!=0 or err: raise RuntimeError('WORKER_FAILED')
        rows=out.splitlines()
        if len(rows)!=1: raise RuntimeError('ONE_WORKER_RECEIPT_REQUIRED')
        value=json.loads(rows[0])
        if (root/'marker').exists(): raise RuntimeError('UNAPPROVED_SOURCE_EXECUTED')
        return value
    finally:
        if child.poll() is None: signal.pidfd_send_signal(fd,signal.SIGKILL)
        child.communicate(timeout=5);os.close(fd)

def validate(mode,value):
    assert value['sql']==value['credential_opens']==value['credential_body_reads']==value['network_attempts']==value['listener_attempts']==0
    assert value['capture_payload_exec']==0
    assert value['candidate_pin'] and value['modules_clean'] and value['finder_clean']
    positive={'happy','capture_only','captured_source_checkpoint','uncaptured_probe_checkpoint',
              'loader_drift','repeat_capture','repeat_activate'}
    assert value['ok'] is (mode in positive) and value['refused'] is (mode not in positive)
    assert value['outcome']==('ok' if mode in positive else 'refused') and value['interrupted'] is False
    assert value['captured'] is (mode in positive or mode=='profile_checkpoint')
    if value['captured']: assert value['no_host_methods'] and value['capture_clean']
    if mode in ('happy','captured_source_checkpoint','uncaptured_probe_checkpoint','loader_drift','repeat_capture','repeat_activate'):
        assert value['activated'] and value['payload_exec']>0 and value['all_false_flags']
        assert value['summary_exact'] and value['no_module_export'] and value['modules_identity']
        assert value['captured_source_used'] and value['marker_absent']
    else: assert not value['activated'] and value['payload_exec']==0
    if mode in ('wrong_approval','old_profile','wrong_mode','extra_field','duplicate_profile','noncanonical',
                'profile_symlink','profile_hardlink','profile_userowned','profile_writable','profile_acl',
                'cold_tamper','stage_tamper','base_tamper','wheel_tamper','dependency_tamper','release_extra',
                'staged_inside_release','preloaded_app','preloaded_stage','foreign_finder','foreign_path',
                'optimized','profile_checkpoint'):
        assert value['refused']
    for case,key in (('repeat_capture','repeat_capture_refused'),('repeat_activate','repeat_activate_refused'),
                     ('loader_drift','drift_blocked'),('uncaptured_probe_checkpoint','probe_blocked')):
        if mode==case: assert value[key]
    if mode in ('repeat_capture','repeat_activate'): assert value['repeat_no_exec']
    if mode=='uncaptured_probe_checkpoint': assert value['probe_no_exec']
    if mode=='optimized': assert value['optimized'] and value['profile_reads']==0

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_intake_gate_refused"}');return 2
    if sys.argv[1:]!=['--reviewed-by-main']: return 2
    if sys.platform=='linux' and os.geteuid()==0 and os.getpid()!=1:
        os.execv('/usr/bin/unshare',['unshare','--pid','--fork','--mount-proc','--net',
            '/usr/bin/python3.12','-I','-S','-B',str(HERE/'run_gate.py'),'--reviewed-by-main'])
    assert sys.platform=='linux' and sys.version_info[:2]==(3,12) and os.geteuid()==0 and os.getpid()==1
    assert [row.split(':',1)[0].strip() for row in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']
    for fn,value in ((pwd.getpwuid,UID),(grp.getgrgid,GID)):
        try: fn(value)
        except KeyError: pass
        else: raise RuntimeError('IDENTITY_CONFLICT')
    source=REPO/'ops/backup/custodian_v2';tests=REPO/'ops/backup/tests/custodian_v2'
    assert sha(source/'source-profile.json')==CATALOG_SHA and sha(source/'cold/cold_host.py')==COLD_SHA
    assert sha(source/'staged/staged_ipc.py')==STAGED_SHA and sha(tests/'support.py')==HELPER_SHA
    helper=load(tests/'support.py','fixed_intake_fixture')
    catalogue=json.loads((source/'source-profile.json').read_bytes())
    inputs={name:helper.source_path(name) for name in catalogue['sources']}
    for name,digest in catalogue['sources'].items(): assert sha(inputs[name])==digest
    inputs.update(catalogue=source/'source-profile.json',cold=source/'cold/cold_host.py',
        staged=source/'staged/staged_ipc.py',helper=tests/'support.py',
        entry=source/'intake/staged_intake.py',worker=HERE/'worker.py',gate=Path(__file__),
        scope=tests/'controller/scope_control.py',supervisor=tests/'controller/ci_gate.py',
        wrapper=HERE/'ci_gate.py',verifier=HERE/'verify_receipt.py',draft=source/'intake/source-profile.json')
    assert sha(inputs['entry'])==ENTRY_SHA and sha(inputs['worker'])==WORKER_SHA
    assert sha(inputs['scope'])==SCOPE_SHA and sha(inputs['supervisor'])==SUPERVISOR_SHA
    draft=json.loads(inputs['draft'].read_bytes())
    assert draft['status']=='DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED' and draft['operator_approval'] is None
    assert draft['admitted_operations']==[] and draft['sources']=={'staged_intake.py':ENTRY_SHA}
    control=load(inputs['scope'],'fixed_intake_scope');scope=control.read_scope()
    before={name:sha(file) for name,file in inputs.items()};before.update(helper.input_hashes())
    base=Path(tempfile.mkdtemp(prefix='i-',dir=scope['work'])).resolve()
    assert base.parent==Path(scope['work']) and base.name.startswith('i-')
    identity=(base.stat().st_dev,base.stat().st_ino);base.chmod(0o755)
    started=time.monotonic();results=[];failures=[]
    try:
        template=base/'template';release=template/'release';release.mkdir(parents=True);(template/'entry').mkdir()
        for name in catalogue['sources']:
            target=release/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(inputs[name],target)
        shutil.copyfile(inputs['catalogue'],release/'source-profile.json')
        helper.unpack_verified_wheels(release/'deps');(release/'wheels').mkdir()
        for name in helper.wheel_pins(): shutil.copyfile(helper.WHEEL_DIR/name,release/'wheels'/name)
        for key,name in (('entry','staged_intake.py'),('cold','cold_host.py'),('staged','staged_ipc.py')):
            shutil.copyfile(inputs[key],template/'entry'/name)
        (template/'entry.sha256').write_text(before['entry']+'\n')
        (template/'secret').write_bytes(b'SYNTHETIC_ONLY_NEVER_A_WORKING_CREDENTIAL')
        worker=base/'worker.py';shutil.copyfile(inputs['worker'],worker)
        for item in base.rglob('*'): item.chmod(0o755 if item.is_dir() else 0o644)
        (base/'data').mkdir()
        for index,mode in enumerate(CASES):
            root=base/'data'/('case'+str(index));shutil.copytree(template,root);root.chmod(0o755)
            (root/'secret').chmod(0o400);os.chown(root/'secret',UID,GID)
            try:
                mutate(root,mode);value=exercise(root,worker,mode);validate(mode,value)
                results.append(dict(case=mode,outcome='PASS',metrics=value))
            except BaseException as error:
                failures.append(dict(case=mode,type=type(error).__name__))
                results.append(dict(case=mode,outcome='FAIL'))
            print('CASE_FINISHED '+mode+' '+results[-1]['outcome'],flush=True)
        remaining=[p.name for p in Path('/proc').iterdir() if p.name.isdigit() and int(p.name)!=os.getpid()]
        assert not remaining
    finally:
        assert base.parent==Path(scope['work']) and base.name.startswith('i-')
        assert (base.stat().st_dev,base.stat().st_ino)==identity
        shutil.rmtree(base)
    after={name:sha(file) for name,file in inputs.items()};after.update(helper.input_hashes())
    passed=len(results)==len(CASES)==31 and not failures and before==after and not base.exists()
    report=dict(format='pult-staged-source-intake-native-proof-v1',outcome='PASS' if passed else 'FAIL',
        scope='LOCAL_SOURCE_INTAKE_ONLY_NOT_HOST_WRITE_OR_OUTER_STOP',tests=len(results),failures=failures,skips=0,
        results=results,source_hashes_before=before,source_hashes_after=after,sources_unchanged=before==after,
        remaining_owned_processes=[],native_tree_removed=not base.exists(),private_pid_network_namespace=True,
        elapsed_seconds=round(time.monotonic()-started,3),new_paid_resources=0,additional_spend_rub=0)
    target=HERE/('RUN_'+str(time.time_ns())+'.json')
    with target.open('x') as stream: json.dump(report,stream,sort_keys=True,indent=2);stream.write('\n')
    print(json.dumps({k:report[k] for k in ('outcome','tests','failures','skips','sources_unchanged','elapsed_seconds')}),flush=True)
    print('RECEIPT '+str(target),flush=True);return 0 if passed else 1

if __name__=='__main__': raise SystemExit(main())
