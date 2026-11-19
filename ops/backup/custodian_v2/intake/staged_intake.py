"""Captured staged sources ONLY; no host, state, credential or write authority.

Root/OS/interpreter/stdlib/this bootstrap and independent profile SHA are trust
roots, not anti-root attestation. Profile arguments belong to trusted entry
wiring, NEVER to a connected caller. No old ColdReady handle is promoted.
"""
import hashlib
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
from types import ModuleType

COLD_SHA = '091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'
STAGED_SHA = 'f8010c39d6162dcac44b5e20cef1e79323c0e88a2e8a01eac34e8fe9163dbf02'
FORMAT = 'pult-staged-source-intake-profile-v1'
MODE = 'SOURCES_ONLY_NO_HOST_CAPABILITY'
FIELDS = {'format', 'mode', 'release_root', 'cold_entry', 'staged_source'}
PAYLOAD = {'anchor_v2','protocol_v2','event_codec','event_store','owned_host','host_core',
           'ops','anyio','certifi','h11','httpcore','httpx','idna','typing_extensions','staged_ipc'}
_ATTEMPTED = False

class Refused(Exception): pass

def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')

def runtime():
    if (sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12)
        or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.flags.optimize
        or Path(sys.executable).resolve()!=Path('/usr/bin/python3.12') or sys.prefix!='/usr' or sys.base_prefix!='/usr'
        or os.getuid()<1000 or os.getuid()!=os.geteuid()):
        raise Refused('isolated unprivileged fixed runtime required')
    fields=dict(row.split(':',1) for row in Path('/proc/self/status').read_text().splitlines() if ':' in row)
    if fields['Threads'].strip()!='1' or any(int(fields[k],16) for k in ('CapEff','CapPrm','CapInh','CapAmb')):
        raise Refused('one capability-free process required')

def path(value):
    if type(value) is not str or '\0' in value:
        raise Refused('explicit path required')
    result=Path(value)
    if not result.is_absolute() or '..' in result.parts or str(result)!=value:
        raise Refused('normalized absolute path required')
    return result

def root_bytes(file,maximum):
    # Minimal bootstrap primitive: the reusable cold reader is not executed
    # until its own exact descriptor bytes have passed this independent check.
    for parent in file.parents:
        info=parent.lstat();sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or (info.st_mode&0o022 and not sticky)
            or any(n.startswith('system.posix_acl_') for n in os.listxattr(parent,follow_symlinks=False))):
            raise Refused('root-controlled ancestor required')
    fd=os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1
            or stat.S_IMODE(before.st_mode) not in (0o444,0o644) or not 0<before.st_size<=maximum
            or any(n.startswith('system.posix_acl_') for n in os.listxattr(fd))):
            raise Refused('bounded root descriptor required')
        raw=bytearray()
        while len(raw)<=maximum:
            part=os.read(fd,min(8192,maximum+1-len(raw)))
            if not part: break
            raw.extend(part)
        after=os.fstat(fd);current=file.lstat()
        if (len(raw)!=before.st_size or (current.st_dev,current.st_ino)!=(before.st_dev,before.st_ino)
            or any(getattr(before,k)!=getattr(after,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))):
            raise Refused('intake changed')
        return bytes(raw)
    finally: os.close(fd)

def document(raw):
    def pairs(rows):
        result={}
        for name,value in rows:
            if name in result: raise Refused('duplicate field')
            result[name]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs)
    if type(value) is not dict or canonical(value)!=raw:
        raise Refused('canonical profile required')
    return value

def closed_interpreter():
    runtime()
    if (tuple(sys.meta_path)!=(BuiltinImporter,FrozenImporter,PathFinder)
        or len(sys.path)!=3 or set(sys.path)!={'/usr/lib/python312.zip','/usr/lib/python3.12','/usr/lib/python3.12/lib-dynload'}
        or any(n.split('.',1)[0] in PAYLOAD for n in sys.modules)):
        raise Refused('fresh closed interpreter required')

class Captured:
    def __init__(self,file,raw,cold,finder):
        self._file=file;self._raw=raw;self._cold=cold;self._finder=finder
        self._pid=os.getpid();self._thread=threading.current_thread()
        self._active=False;self._closed=False;self._attempted=False
    def __repr__(self): return '<StagedSourcesOnly no host or write authority>'
    def check(self):
        try:
            runtime()
            if (os.getpid()!=self._pid or threading.current_thread() is not self._thread or self._closed
                or root_bytes(self._file,8192)!=self._raw):
                raise Refused('source capsule unavailable')
            if self._active: self._finder.check()
            return True
        except Exception:
            raise Refused('source capsule unavailable') from None
    def activate(self):
        try:
            self.check()
            if self._attempted: raise Refused('one source activation required')
            self._attempted=True
            closed_interpreter();self._cold._runtime()
            modules=self._cold._activate(self._finder)
            stage=importlib.import_module('staged_ipc')
            if (stage.V2Anchor is not modules[0].V2Anchor or stage.V2Custodian is not modules[1].V2Custodian
                or stage.EventReader is not modules[3].EventReader or stage.EventWriter is not modules[3].EventWriter):
                raise Refused('fixed public type identities required')
            self._active=True;self.check()
            return dict(status='STAGED_SOURCES_ONLY',staged_sha256=STAGED_SHA,
                write_admitted=False,host_capability_created=False,purge_or_restore_admitted=False)
        except BaseException as error:
            self.close()
            if isinstance(error,(KeyboardInterrupt,SystemExit)): raise
            raise Refused('source activation refused') from None
    def close(self):
        if self._closed: return
        if os.getpid()!=self._pid or threading.current_thread() is not self._thread:
            raise Refused('same source capsule owner required')
        self._finder.rollback();self._closed=True

def capture(profile_file,approved_sha256):
    global _ATTEMPTED
    finder=None
    try:
        if _ATTEMPTED: raise Refused('one intake attempt per process')
        _ATTEMPTED=True;closed_interpreter()
        if type(approved_sha256) is not str or not re.fullmatch('[0-9a-f]{64}',approved_sha256):
            raise Refused('independent profile digest required')
        profile_file=path(profile_file);raw=root_bytes(profile_file,8192)
        if hashlib.sha256(raw).hexdigest()!=approved_sha256: raise Refused('independent approval mismatch')
        value=document(raw)
        if set(value)!=FIELDS or value['format']!=FORMAT or value['mode']!=MODE:
            raise Refused('distinct source-only profile required')
        root=path(value['release_root']);cold_file=path(value['cold_entry']);stage_file=path(value['staged_source'])
        if (len({root,profile_file,cold_file,stage_file})!=4
            or any(p.is_relative_to(root) for p in (profile_file,cold_file,stage_file))):
            raise Refused('separate closed base and approved entry files required')
        cold_raw=root_bytes(cold_file,100000);stage_raw=root_bytes(stage_file,100000)
        if hashlib.sha256(cold_raw).hexdigest()!=COLD_SHA or hashlib.sha256(stage_raw).hexdigest()!=STAGED_SHA:
            raise Refused('fixed accepted sources required')
        cold=ModuleType('_staged_fixed_cold_intake');cold.__file__=str(cold_file)
        exec(compile(cold_raw,str(cold_file),'exec',dont_inherit=True,optimize=0),cold.__dict__)
        cold._runtime();finder=cold._intake(root)
        if 'staged_ipc' in finder.loaders or 'staged_ipc' in finder.namespaces:
            raise Refused('overlapping staged module')
        finder.loaders['staged_ipc']=cold._Captured(finder,'staged_ipc',stage_file,stage_raw,False)
        return Captured(profile_file,raw,cold,finder)
    except BaseException as error:
        if finder is not None: finder.rollback()
        if isinstance(error,(KeyboardInterrupt,SystemExit)): raise
        raise Refused('staged source intake refused') from None
