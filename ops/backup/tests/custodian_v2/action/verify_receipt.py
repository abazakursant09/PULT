"""Independent read-only synthetic state/journal/source postcheck, no permits."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[4]
MODES=('happy','write_unknown','read_fail','kill_controller_after_put','freeze_roles_at_put')
FIELDS={'UNIT','MESSAGE','MESSAGE_ID','JOB_TYPE','JOB_RESULT','_PID'}

def require(ok):
    if not ok:raise RuntimeError('ACTION_INDEPENDENT_POSTCHECK_REFUSED')
def canonical(v):return (json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
def digest(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def ident(file):
    s=file.lstat();return dict(device=s.st_dev,inode=s.st_ino)
def require_trace(row):
    require(row['case'] in MODES and type(row['calls']) is list)
    expected=[dict(method='PUT',sequence=1,uid=48002)]
    if row['case'] in ('happy','read_fail'):expected.append(dict(method='GET',sequence=1,uid=47003))
    require(all(type(c) is dict and set(c)=={'method','sequence','uid'} and type(c['sequence']) is int
        and type(c['uid']) is int for c in row['calls']))
    require(row['calls']==expected)

def require_scope(value,scope):
    """Bind a receipt to THIS wrapper allocation, not an old valid journal."""
    require(type(scope['id']) is str and re.fullmatch('[0-9a-f]{32}',scope['id']))
    work='/tmp/pult-cc-'+scope['id'][:16]
    require(scope['work']==work and value['scope_id']==scope['id'] and value['scope_work']==work)
    for row in value['cases']:
        e=row['evidence'];root=PurePosixPath(e['root'])
        require(str(root)==e['root'] and root.parent.parent==PurePosixPath(work)
            and re.fullmatch('a-[a-z0-9_]{8}',root.parent.name) and re.fullmatch('case-[0-9a-f]{32}',root.name))
        require(type(e['units']) is list and len(e['units'])==3 and len(set(e['units']))==3
            and all(type(u) is str and re.fullmatch(r'pult-call-gate-[0-9a-f]{32}\.service',u) for u in e['units']))
        observed=row['independent_evidence'];negative=row['case']=='kill_controller_after_put'
        require(observed['negative_condition'] is negative and observed['kernel_unit']==e['units'][0 if negative else 2])
        gate=root/('control' if negative else 'admission')/'constructor-stop.claim'
        require(observed['kernel_fence']==str(gate))
def empty(unit):
    require(re.fullmatch(r'pult-call-gate-[0-9a-f]{32}\.service',unit))
    group=Path('/sys/fs/cgroup/system.slice')/unit
    return not group.exists() or not any(p.read_text().strip() for p in group.rglob('cgroup.procs'))
def kernel_record(unit,fence,negative):
    output=subprocess.run(['/usr/bin/journalctl','--no-pager','-o','json','-u',unit],capture_output=True,text=True,timeout=5,check=True)
    suffix=' was skipped because of an unmet condition check (ConditionPathExists='+('!' if negative else '')+str(fence)+').'
    for line in output.stdout.splitlines():
        row=json.loads(line);record={k:row.get(k) for k in FIELDS}
        if (record['UNIT']==unit and record['_PID']=='1' and record['JOB_TYPE']=='start' and record['JOB_RESULT']=='done'
                and record['MESSAGE_ID']=='39f53479d3a045ac8e11786248231fbf' and type(record['MESSAGE']) is str
                and record['MESSAGE'].endswith(suffix)):
            return record
    raise RuntimeError('ACTION_KERNEL_REFUSAL_RECEIPT_MISSING')

def observe(row):
    """Called only AFTER fixture producers, roles and broker are stopped/reaped."""
    require(sys.platform=='linux' and os.geteuid()==0 and not sys.flags.optimize)
    e=row['evidence'];root=Path(e['root']);require(root.name.startswith('case-') and ident(root)==e['identity'])
    profile=json.loads((root/'action.json').read_bytes());cfg=json.loads((root/'host.json').read_bytes())
    require(all(empty(u) for u in e['units']))
    require(not (root/'admission/constructor-stop.claim').exists())
    fence=root/'control/constructor-stop.claim';f=json.loads(fence.read_bytes())
    require(fence.read_bytes()==canonical(f) and stat.S_IMODE(fence.stat().st_mode)==0o600 and fence.stat().st_uid==0)
    require(f==dict(format='pult-one-storage-action-permanent-fence-v3',approval=digest(root/'action.json'),
        operation=profile['operation']['id'],controller=profile['controller'],units={r:profile[r]['unit'] for r in ('custodian','writer')}))
    for role in ('custodian','writer'):
        file=root/'admission'/(role+'.attested');s=file.lstat();v=json.loads(file.read_bytes())
        require(s.st_uid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o444 and file.read_bytes()==canonical(v))
        require(v['format']=='pult-role-kernel-attestation-v3' and v['approval']==digest(root/'action.json')
            and v['controller']==profile['controller'] and v['role']==role and v['unit']==profile[role]['unit']
            and type(v['pid']) is int and v['pid']>0 and type(v['start_ticks']) is int and v['start_ticks']>0)
    require(all(digest(Path(v['path']))==v['sha256'] for v in profile['sources'].values()))
    db=Path(cfg['state_file']);require(all(not Path(str(db)+x).exists() for x in ('-journal','-wal','-shm')))
    connection=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
    try:
        meta=connection.execute('SELECT seq,hash,revision,pending FROM anchor_v2').fetchone()
        attempts=connection.execute('SELECT id,target_seq,target_hash,body,phase FROM attempts_v2').fetchall()
    finally:connection.close()
    require(len(attempts)==1)
    attempt,seq,target,blob,phase=attempts[0];record=json.loads(blob)
    require(attempt==profile['operation']['attempt'] and seq==1 and target==record['digest'])
    require(record['event']==profile['operation']['event'] and record['previous']==profile['operation']['base']['digest'])
    unsigned=dict(record);unsigned.pop('digest');require(hashlib.sha256(canonical(unsigned)).hexdigest()==target)
    objects=list((root/'objects').iterdir());require(len(objects)==1 and objects[0].name=='00000000000000000001.json')
    require(objects[0].read_bytes()==canonical(record))
    expected=(1,target,3,None) if row['case']=='happy' else (0,profile['operation']['base']['digest'],2,attempt)
    require(meta==expected and phase==('COMMITTED' if row['case']=='happy' else 'DISPATCHED'))
    require(row['meta']==[meta[0],meta[2],meta[3]] and row['phases']==[phase])
    actual_calls=[json.loads(line) for line in (root/'calls.jsonl').read_text().splitlines()]
    require_trace(dict(case=row['case'],calls=actual_calls));require(row['calls']==actual_calls)
    require(digest(Path(cfg['reader_credential_file']))==e['reader_sha'] and digest(root/'writer-private/key.json')==e['writer_sha'])
    fd=os.open(cfg['lease_file'],os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:os.close(fd)
    negative=row['case']=='kill_controller_after_put';unit=profile['controller']['unit'] if negative else profile['writer']['unit']
    gate=fence if negative else root/'admission/constructor-stop.claim'
    journal=kernel_record(unit,gate,negative)
    return dict(state_observed_read_only=True,source_bytes_unchanged=True,owner_lock_released=True,
        object_sha256=digest(objects[0]),meta=list(meta),phase=phase,kernel_unit=unit,kernel_fence=str(gate),
        negative_condition=negative,kernel_record=journal,actual_calls=actual_calls)

def expected_paths():
    catalogue=REPO/'ops/backup/custodian_v2/action/source-profile.json'
    value=json.loads(catalogue.read_bytes())
    require(set(value)=={'format','status','admitted_operations','working_credentials_included','sources'})
    require(value['format']=='pult-synthetic-action-ci-catalogue-v3' and value['status']=='DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED'
        and value['admitted_operations']==[] and value['working_credentials_included'] is False)
    result={k:REPO/k for k in value['sources']}
    require(all(re.fullmatch('[0-9a-f]{64}',v) and digest(result[k])==v for k,v in value['sources'].items()))
    result['catalogue']=catalogue
    return result

def verify(file,before,scope):
    require(not sys.flags.optimize)
    file=Path(file);require(file.parent.resolve()==ROOT and re.fullmatch(r'RUN_[0-9]+\.json',file.name))
    value=json.loads(file.read_bytes());require(value['format']=='pult-action-native-receipt-v3' and value['outcome']=='PASS')
    require_scope(value,scope)
    require(value['live_provider_iam_tls'] is False and value['new_paid_resources']==value['new_persistent_accounts']==0)
    require([r['case'] for r in value['cases']]==list(MODES))
    for row in value['cases']:
        require_trace(row)
        require(row['outcome']=='PASS' and row['groups_empty'] is True and row['keys_unchanged'] is True and row['permanent_fence'] is True)
        require(row['action']==('PASS' if row['case']=='happy' else 'CONTROLLER_KILLED_EXPECT_UNKNOWN' if row['case']=='kill_controller_after_put' else 'UNKNOWN'))
        observed=row['independent_evidence'];require(observed['state_observed_read_only'] and observed['source_bytes_unchanged'] and observed['owner_lock_released'])
        require(observed['actual_calls']==row['calls'])
        require(all(empty(u) for u in row['evidence']['units']))
        require(kernel_record(observed['kernel_unit'],observed['kernel_fence'],observed['negative_condition'])==observed['kernel_record'])
    require(before=={k:digest(p) for k,p in expected_paths().items()})
    return digest(file)
