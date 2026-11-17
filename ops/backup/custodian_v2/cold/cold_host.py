"""Cold OFFLINE_CURRENT_ONLY entry candidate. No CLI, listener or live transport.

Root/interpreter/stdlib/this bootstrap are trust roots, not anti-root attestation.
The supplied profile digest belongs to a trusted service entry, never a request.
"""
import hashlib
from importlib.machinery import BuiltinImporter,FrozenImporter,PathFinder,SourceFileLoader,ModuleSpec
from importlib.util import spec_from_loader
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import stat
import sys
import zipfile
from uuid import UUID

CATALOG_SHA='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'
WHEELS={
 'anyio-4.14.2-py3-none-any.whl':'9f505dda5ac9f0c8309b5e8bd445a8c2bf7246f3ce950121e45ea15bc41d1494',
 'certifi-2026.10.19-py3-none-any.whl':'42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b',
 'h11-0.16.0-py3-none-any.whl':'63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86',
 'httpcore-1.0.9-py3-none-any.whl':'2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55',
 'httpx-0.28.1-py3-none-any.whl':'d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad',
 'idna-3.18-py3-none-any.whl':'7f952cbe720b688055e3f87de14f5c3e5fdaa8bc3928985c4077ca689de849a2',
 'typing_extensions-4.16.0-py3-none-any.whl':'481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8',
}
DEP_NAMES={'anyio','certifi','h11','httpcore','httpx','idna','typing_extensions'}
APP_NAMES={'anchor_v2','protocol_v2','event_codec','event_store','owned_host','host_core','ops'}
BOUNDS={'max_receipts':1024,'max_state_bytes':2097152,'record_bytes':2048}
_ATTEMPTED=False
_FAILED=set()

class ColdRefused(Exception): pass

def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')

def _path(value):
    if type(value) is not str or '\0' in value: raise ColdRefused('explicit absolute path required')
    result=Path(value)
    if not result.is_absolute() or '..' in result.parts or str(result)!=value:
        raise ColdRefused('normalized absolute path required')
    return result

def _ancestors(path):
    for parent in path.parents:
        info=parent.lstat();sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or (info.st_mode&0o022 and not sticky):
            raise ColdRefused('root-controlled ancestor required')

def _root_bytes(path,maximum,*,allow_empty=False):
    _ancestors(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1
                or stat.S_IMODE(before.st_mode) not in (0o444,0o644)
                or not (0 if allow_empty else 1)<=before.st_size<=maximum):
            raise ColdRefused('bounded root file required')
        body=bytearray()
        while len(body)<=maximum:
            chunk=os.read(fd,min(8192,maximum+1-len(body)))
            if not chunk: break
            body.extend(chunk)
        after=os.fstat(fd);fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        if len(body)!=before.st_size or any(getattr(before,k)!=getattr(after,k) for k in fields):
            raise ColdRefused('intake file changed')
        return bytes(body)
    finally: os.close(fd)

def _pairs(rows):
    result={}
    for key,value in rows:
        if key in result: raise ColdRefused('duplicate profile field')
        result[key]=value
    return result

def _json(body):
    try:
        value=json.loads(body,object_pairs_hook=_pairs)
        if type(value) is not dict or canonical(value)!=body: raise ColdRefused('canonical object required')
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError): raise ColdRefused('invalid profile JSON') from None

def _identity(value):
    if (type(value) is not dict or set(value)!={'device','inode'}
            or any(type(item) is not int or item<0 for item in value.values()) or value['inode']==0):
        raise ColdRefused('explicit inode binding required')

def _runtime():
    if (sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12)
            or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.flags.optimize
            or Path(sys.executable).resolve()!=Path('/usr/bin/python3.12') or sys.prefix!='/usr' or sys.base_prefix!='/usr'
            or os.getuid()<1000 or os.getuid()!=os.geteuid()):
        raise ColdRefused('cold isolated dedicated runtime required')
    fields=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if fields['Threads'].strip()!='1' or any(int(fields[k],16) for k in ('CapEff','CapPrm','CapInh','CapAmb')):
        raise ColdRefused('single unprivileged process required')
    if tuple(sys.meta_path)!=(BuiltinImporter,FrozenImporter,PathFinder): raise ColdRefused('foreign import finder')
    paths={'/usr/lib/python312.zip','/usr/lib/python3.12','/usr/lib/python3.12/lib-dynload'}
    if len(sys.path)!=3 or set(sys.path)!=paths: raise ColdRefused('closed system stdlib required')
    for name in paths:
        path=Path(name);_ancestors(path)
        if os.path.lexists(path):
            info=path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                raise ColdRefused('untrusted stdlib path')
    if any(name.split('.',1)[0] in APP_NAMES|DEP_NAMES for name in sys.modules):
        raise ColdRefused('preloaded payload or dependency')

def _profile(body,root,profile_file):
    value=_json(body)
    keys={'format','mode','release_root','scope','generation','custodian_uid','socket_gid','state_file','state_identity',
        'lease_file','lease_identity','reader_credential_file','reader_credential_identity','reader_access_key','s3','bounds'}
    if (set(value)!=keys or value['format']!='pult-schema2-cold-host-profile-v1'
            or value['mode']!='OFFLINE_CURRENT_ONLY' or value['release_root']!=str(root)):
        raise ColdRefused('fixed offline host profile required')
    if type(value['scope']) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,64}',value['scope']):
        raise ColdRefused('invalid scope')
    try: parsed=UUID(value['generation']) if type(value['generation']) is str else None
    except ValueError: parsed=None
    if parsed is None or parsed.version!=4 or str(parsed)!=value['generation']: raise ColdRefused('canonical generation required')
    for name in ('custodian_uid','socket_gid'):
        if type(value[name]) is not int or not 1000<=value[name]<2**31: raise ColdRefused('dedicated principal required')
    fields=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if ([int(x) for x in fields['Uid'].split()]!=[value['custodian_uid']]*4
            or [int(x) for x in fields['Gid'].split()]!=[value['socket_gid']]*4
            or not set(os.getgroups()).issubset({value['socket_gid']})):
        raise ColdRefused('profile principal mismatch')
    if (type(value['bounds']) is not dict or value['bounds']!=BOUNDS
            or any(type(x) is not int for x in value['bounds'].values())):
        raise ColdRefused('fixed capacity required')
    state=_path(value['state_file']);lease=_path(value['lease_file']);credential=_path(value['reader_credential_file'])
    if (lease!=state.parent/'owner.lock' or any(path.is_relative_to(root) for path in (state,lease,credential,profile_file))
            or credential.parent==state.parent or credential.parent in state.parents or state.parent in credential.parents):
        raise ColdRefused('separate existing state and credential required')
    for name in ('state_identity','lease_identity','reader_credential_identity'): _identity(value[name])
    if (type(value['reader_access_key']) is not str or not re.fullmatch('[\x21-\x7e]{1,256}',value['reader_access_key'])):
        raise ColdRefused('explicit reader identity required')
    s3=value['s3']
    if (type(s3) is not dict or set(s3)!={'endpoint','region','bucket'} or s3['region']!='ru-3'
            or s3['endpoint']!='https://s3.ru-3.storage.selcloud.ru' or type(s3['bucket']) is not str
            or not re.fullmatch('[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',s3['bucket'])):
        raise ColdRefused('fixed ru3 location required')
    return value

def _relative(name):
    if (type(name) is not str or not name or '\\' in name or ':' in name or '\0' in name
            or any(part in ('','.','..') for part in name.split('/')) or PurePosixPath(name).is_absolute()):
        raise ColdRefused('closed relative member required')
    return name

def _closed(root,files):
    directories={''}
    for name in files:
        _relative(name);directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent)!='.')
    for name in directories:
        path=root/name;_ancestors(path/'entry');info=path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022):
            raise ColdRefused('root-controlled release directory required')
        expected={PurePosixPath(member).relative_to(name or '.').parts[0] for member in files
            if not name or member.startswith(name+'/')}
        if {item.name for item in path.iterdir()}!=expected: raise ColdRefused('unexpected release entry')

class _Captured(SourceFileLoader):
    def __init__(self,finder,name,path,body,package):
        super().__init__(name,str(path));self.finder=finder;self.body=body;self.package=package
    def get_code(self,fullname): return compile(self.body,self.path,'exec')
    def is_package(self,fullname): return self.package
    def exec_module(self,module):
        self.finder.owned[module.__name__]=module
        try: super().exec_module(module)
        except BaseException:
            # Importlib removes a failed import. Do not retain a stale "loaded"
            # identity when a parent deliberately handles an optional ImportError.
            if self.finder.owned.get(module.__name__) is module:
                del self.finder.owned[module.__name__]
            if sys.modules.get(module.__name__) is module:
                del sys.modules[module.__name__]
            raise

class _Namespace:
    def __init__(self,finder,path): self.finder=finder;self.path=path
    def create_module(self,spec): return None
    def exec_module(self,module):
        self.finder.owned[module.__name__]=module;module.__path__=[str(self.path)]

class _Finder:
    def __init__(self,modules,namespaces):
        self.owned={};self.loaders={name:_Captured(self,name,*row) for name,row in modules.items()}
        self.namespaces={name:_Namespace(self,path) for name,path in namespaces.items() if name not in modules}
        self.path=tuple(sys.path)
    def find_spec(self,fullname,path=None,target=None):
        if fullname in self.loaders: return spec_from_loader(fullname,self.loaders[fullname])
        if fullname in self.namespaces:
            spec=ModuleSpec(fullname,self.namespaces[fullname],is_package=True)
            spec.submodule_search_locations=[str(self.namespaces[fullname].path)];return spec
        if fullname.split('.',1)[0] in APP_NAMES|DEP_NAMES:
            raise ColdRefused('uncaptured payload import refused')
        return None
    def check(self):
        if (tuple(sys.meta_path)!=(self,BuiltinImporter,FrozenImporter,PathFinder) or tuple(sys.path)!=self.path
                or any(sys.modules.get(name) is not module for name,module in self.owned.items())):
            raise ColdRefused('loaded module identity changed')
        for name,module in self.owned.items():
            loader=self.loaders.get(name,self.namespaces.get(name));spec=module.__spec__
            if (module.__loader__ is not loader or spec is None or spec.loader is not loader or spec.name!=name):
                raise ColdRefused('loaded source identity changed')
            if name in self.loaders and (module.__file__!=loader.path or spec.origin!=loader.path):
                raise ColdRefused('loaded source origin changed')
            if name in self.namespaces and module.__path__!=[str(loader.path)]:
                raise ColdRefused('loaded namespace path changed')
    def rollback(self):
        if self in sys.meta_path: sys.meta_path.remove(self)
        for name,module in reversed(tuple(self.owned.items())):
            if sys.modules.get(name) is module: del sys.modules[name]

def _intake(root):
    catalogue=_root_bytes(root/'source-profile.json',16384)
    if hashlib.sha256(catalogue).hexdigest()!=CATALOG_SHA: raise ColdRefused('unapproved source catalogue')
    manifest=json.loads(catalogue);sources=manifest['sources']
    if len(sources)!=13 or manifest['admitted_operations'] or manifest['operator_approval'] is not None:
        raise ColdRefused('source catalogue is not an operation approval')
    files={'source-profile.json':catalogue};dependencies={};total=0
    for wheel,digest in WHEELS.items():
        body=_root_bytes(root/'wheels'/wheel,2*1024*1024)
        if hashlib.sha256(body).hexdigest()!=digest: raise ColdRefused('dependency wheel mismatch')
        files['wheels/'+wheel]=body
        with zipfile.ZipFile(io.BytesIO(body)) as package:
            for row in package.infolist():
                if row.is_dir(): continue
                name=_relative(row.filename);total+=row.file_size
                if (name in dependencies or stat.S_ISLNK(row.external_attr>>16) or row.flag_bits&1
                        or row.file_size>8*1024*1024 or total>64*1024*1024 or name.endswith(('.pth','.pyc','.so'))):
                    raise ColdRefused('unsupported dependency member')
                dependencies[name]=package.read(row)
    if len(dependencies)>5000: raise ColdRefused('dependency tree exceeds bound')
    modules={};namespaces={}
    def add_module(name,path,body,package):
        if name in modules: raise ColdRefused('overlapping source module')
        modules[name]=(path,body,package)
        parts=name.split('.')
        for index in range(1,len(parts)):
            namespaces.setdefault('.'.join(parts[:index]),path.parents[len(parts)-index if package else len(parts)-index-1])
    for name,digest in sources.items():
        _relative(name);body=_root_bytes(root/name,600000)
        if hashlib.sha256(body).hexdigest()!=digest: raise ColdRefused('source digest mismatch')
        files[name]=body
        if name.endswith('.py'):
            relative=name.split('/',1)[1][:-3];module=relative.replace('/','.')
            if module.split('.',1)[0] not in APP_NAMES: raise ColdRefused('unexpected source module')
            add_module(module,root/name,body,False)
    for name,body in dependencies.items():
        actual=_root_bytes(root/'deps'/name,8*1024*1024,allow_empty=True)
        if actual!=body: raise ColdRefused('extracted dependency mismatch')
        files['deps/'+name]=actual
        if name.endswith('.py'):
            package=name.endswith('/__init__.py')
            module=(name[:-12] if package else name[:-3]).replace('/','.')
            if module.split('.',1)[0] not in DEP_NAMES: raise ColdRefused('unexpected dependency module')
            add_module(module,root/'deps'/name,body,package)
    _closed(root,files)
    return _Finder(modules,namespaces)

def _activate(finder):
    sys.meta_path.insert(0,finder)
    import anchor_v2,protocol_v2,event_codec,event_store,owned_host,host_core,httpx
    finder.check()
    if (protocol_v2.V2Anchor is not anchor_v2.V2Anchor or anchor_v2.Lease is not owned_host.Lease
            or event_store.Binding is not event_codec.Binding): raise ColdRefused('cross-module identity mismatch')
    return anchor_v2,protocol_v2,event_codec,event_store,owned_host,host_core,httpx

def _credential(host,value):
    path=Path(value['reader_credential_file']);uid=value['custodian_uid']
    host._ancestors(path,{0,uid})
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=uid or before.st_nlink!=1
                or stat.S_IMODE(before.st_mode) not in (0o400,0o600) or host._acl(fd) is not None
                or not 0<before.st_size<=8192
                or {'device':before.st_dev,'inode':before.st_ino}!=value['reader_credential_identity']):
            raise ColdRefused('pinned private reader descriptor required')
        body=bytearray()
        while len(body)<=8192:
            part=os.read(fd,min(4096,8193-len(body)))
            if not part: break
            body.extend(part)
        after=os.fstat(fd);fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        if len(body)!=before.st_size or any(getattr(before,k)!=getattr(after,k) for k in fields):
            raise ColdRefused('reader credential changed')
        result=host._json(bytes(body))
        if result.get('access_key')!=value['reader_access_key']: raise ColdRefused('reader identity mismatch')
        return result
    finally: os.close(fd)

class Ready:
    def __init__(self,profile_file,raw,finder):
        self._profile_file=profile_file;self._raw=raw;self._finder=finder
        self._owner=None;self._reader=None;self._anchor=None;self._closed=False;self._failed=False
    def __repr__(self): return '<ColdSchema2Ready offline local observation only>'
    def _check(self):
        if self._closed or self._failed: raise ColdRefused('cold handle unavailable')
        if _root_bytes(self._profile_file,8192)!=self._raw: raise ColdRefused('approved profile changed')
        self._finder.check()
        if self._owner is not None: self._owner.check()
    def observe(self):
        try:
            self._check();state=self._anchor.load();self._check()
            return {'status':'PENDING_REQUIRES_REVIEW' if state.pending is not None else 'OBSERVED_LOCAL_ONLY',
                'head':{'sequence':state.head.sequence,'digest':state.head.digest},'revision':state.revision,
                'write_admitted':False,'purge_or_restore_admitted':False}
        except Exception: raise ColdRefused('local observation refused') from None
    def close(self):
        if self._closed: return
        if self._failed: raise ColdRefused('cleanup uncertain; process exit required')
        try:
            if self._reader is not None: self._reader.close()
            if self._owner is not None: self._owner.check();self._owner.close()
        except BaseException:
            self._failed=True;_FAILED.add(self)
            raise ColdRefused('cleanup uncertain; process exit required') from None
        self._finder.rollback();self._closed=True

def prepare(release_root,profile_file,approved_profile_sha256):
    global _ATTEMPTED
    ready=None;finder=None;reader_started=False
    try:
        if _ATTEMPTED: raise ColdRefused('one cold attempt per process required')
        _ATTEMPTED=True;_runtime()
        root=_path(release_root);profile_file=_path(profile_file)
        if type(approved_profile_sha256) is not str or not re.fullmatch('[0-9a-f]{64}',approved_profile_sha256):
            raise ColdRefused('independent profile approval required')
        raw=_root_bytes(profile_file,8192)
        if hashlib.sha256(raw).hexdigest()!=approved_profile_sha256: raise ColdRefused('profile approval mismatch')
        value=_profile(raw,root,profile_file);finder=_intake(root)
        ready=Ready(profile_file,raw,finder)
        anchor,protocol,codec,store,ownership,host,httpx=_activate(finder)
        if anchor.MAX_RECEIPTS!=BOUNDS['max_receipts'] or codec.MAX_RECORD_BYTES!=BOUNDS['record_bytes']:
            raise ColdRefused('loaded bounds mismatch')
        ready._check()
        spec={name:value[name] for name in ('state_file','state_identity','lease_file','lease_identity','custodian_uid','socket_gid')}
        ready._owner=ownership.Lease(host,spec)  # before first SQL or credential load
        binding=codec.Binding(value['scope'],value['generation']);s3=value['s3']
        location=s3['endpoint']+'/'+s3['bucket']+'/pult/erasure-v2/'+binding.scope+'/'+binding.generation+'/events/'
        ready._anchor=anchor.V2Anchor(value['state_file'],binding=binding,location=location,owner=ready._owner)
        ready._anchor.load();ready._check()  # schema/read validation before secrets
        credential=_credential(host,value)
        ready._check();reader_started=True
        def no_io(request): raise ColdRefused('remote I/O is not admitted')
        ready._reader=store.EventReader(**s3,binding=binding,credential=credential,transport=httpx.MockTransport(no_io))
        protocol.V2Custodian(ready._anchor,ready._reader)  # exact binding and ownership, not an append export
        ready._check();return ready
    except BaseException as error:
        if ready is not None:
            if reader_started and ready._reader is None:
                ready._failed=True;_FAILED.add(ready)  # opaque constructor cleanup: hold until exit
            else:
                try: ready.close()
                except BaseException: pass  # retained by Ready on uncertain cleanup
        elif finder is not None: finder.rollback()
        if isinstance(error,(KeyboardInterrupt,SystemExit)): raise
        raise ColdRefused('cold host preparation refused') from None
