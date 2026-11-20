"""TEST ONLY: original entry with an additional owned CI lifecycle dependency.

Does not replace any check, profile source, actor UID, Lease or storage result.
The root-created static launch document pins this adapter's entry digest and
the exact separately captured controller-entry source below.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType

HERE=Path(__file__).resolve().parent
ENTRY_SHA='9d14ce8bc95e916b294f69087cc7441800b18302f175fa115e059b30844cb3e8'
SCOPE_SHA='d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'

def load(file,wanted,name):
    raw=file.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=wanted:raise RuntimeError('NATIVE_CONTROLLER_TEST_INPUT_REFUSED')
    module=ModuleType(name);module.__file__=str(file);sys.modules[name]=module
    exec(compile(raw,str(file),'exec',dont_inherit=True,optimize=0),module.__dict__);return module

def main():
    entry=load(HERE/'controller_entry.py',ENTRY_SHA,'fixed_controller_entry_under_native_test')
    control=load(HERE/'scope_control.py',SCOPE_SHA,'fixed_native_storage_scope')
    previous=entry.capture;observed_roots=[];unit_policy_diagnostics=[]
    def capture(row,name):
        module=previous(row,name)
        if name=='pult_synthetic_live_profile_v1':
            original=module.Profile.capture
            original_properties=module.Profile._unit_properties
            def properties(profile,role,pid):
                try:
                    result=original_properties(profile,role,pid)
                    expected=os.readlink('/proc/self/ns/net')
                    if (expected==os.readlink('/proc/1/ns/net') or os.readlink('/proc/'+str(pid)+'/ns/net')!=expected
                            or len(Path('/proc/'+str(pid)+'/net/route').read_text().splitlines())!=1):
                        raise RuntimeError('test actor private netns unproven')
                    return result
                except module.Refused:
                    fields='MainPID,User,Group,Type,ExitType,Restart,NRestarts,RuntimeMaxUSec,TimeoutStopUSec,KillMode,KillSignal,FinalKillSignal,SendSIGKILL,PrivateDevices,PrivateTmp,RestrictAddressFamilies,NoNewPrivileges,PrivateNetwork,ProtectSystem,ProtectControlGroups,AmbientCapabilities,RootDirectory,RootImage,BindPaths,BindReadOnlyPaths,TemporaryFileSystem'
                    data=module.show(profile.spec[role]['unit'],fields)
                    unit_policy_diagnostics[:]=[dict(role=role,metadata=data)]
                    raise
            module.Profile._unit_properties=properties
            def scoped(profile,key):
                result=original(profile,key)
                if key=='root' and not getattr(result,'_native_scope_only',False):
                    observed_roots.append(result)
                    result._native_scope_only=True;base=result.Action
                    class ScopedAction(base):
                        def command(self,role):
                            args=super().command(role)
                            # Even a test-adapter failure must not open external
                            # network: join this original controller's PRIVATE
                            # netns. This is explicit test wiring, not a profile
                            # option or a provider/network-isolation proof.
                            args.insert(args.index('/usr/bin/setpriv'),
                                '--property=NetworkNamespacePath=/proc/'+str(os.getpid())+'/ns/net')
                            return control.bound_command(args)
                    result.Action=ScopedAction
                return result
            module.Profile.capture=scoped
        return module
    entry.capture=capture
    # The actual entry validates __file__ bytes. This adapter is the root/OS
    # test entry, so the independently approved launch digest names this file;
    # actual entry source is separately pinned by ENTRY_SHA before execution.
    entry.__file__=__file__
    try:return entry.run(sys.argv[1],sys.argv[2])
    except BaseException as error:
        locations=[];tb=error.__traceback__
        while tb is not None:
            locations.append([Path(tb.tb_frame.f_code.co_filename).name,tb.tb_frame.f_code.co_name,tb.tb_lineno]);tb=tb.tb_next
        root_failures=[]
        for root in observed_roots:
            for action in root.__dict__.get('_FAILED',()):
                original=action.failure;tb=original.__traceback__ if original is not None else None;source=[]
                while tb is not None:
                    source.append([Path(tb.tb_frame.f_code.co_filename).name,tb.tb_frame.f_code.co_name,tb.tb_lineno]);tb=tb.tb_next
                root_failures.append(dict(type=type(original).__name__,code_locations=source[-7:],
                    role_stdout_bytes={r:len(v.get('stdout',b'')) for r,v in action.buffers.items()},
                    role_stderr_bytes={r:len(v.get('stderr',b'')) for r,v in action.buffers.items()}))
        print(json.dumps(dict(status='UNKNOWN_RETAIN_DURABLE_STATE',purge=False,restore=False,
            error_type=type(error).__name__,code_locations=locations[-7:],root_failures=root_failures[-2:],
            unit_policy_diagnostics=unit_policy_diagnostics),sort_keys=True),flush=True)
        return 70

if __name__=='__main__':raise SystemExit(main())
