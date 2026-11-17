"""Owned synthetic CI resources only. No application/runtime dependency."""
import json
import os
from pathlib import Path
import re
import stat

ENV='PULT_CONTROLLER_CI_SCOPE'
UNIT_PATTERN=r'pult-call-gate-[0-9a-f]{32}\.service'

def canonical(value): return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode('ascii')
def identity(file):
    info=file.lstat();return [info.st_dev,info.st_ino]

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
    os.link(pending,record,follow_symlinks=False)  # Atomic publication, never overwrite.
    pending.unlink()
    directory=os.open(record.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try: os.fsync(directory)
    finally: os.close(directory)
    return [argv[0],'--property=ConditionPathExists='+data['active'],
        '--property=PartOf='+data['parent_unit'],'--property=After='+data['parent_unit'],*argv[1:]]
