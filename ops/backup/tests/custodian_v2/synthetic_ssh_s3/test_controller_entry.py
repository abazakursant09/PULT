"""Bootstrap grammar and actual local recvmsg effects; no service or S3 calls."""
import array
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import socket
import struct
import sys
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2/synthetic_ssh_s3'
spec=importlib.util.spec_from_file_location('fixed_controller_entry',SOURCE/'controller_entry.py')
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)

def proposal():
    return dict(format=e.FORMAT,entry_sha256='a'*64,runtime_profile='/etc/pult-synthetic/runtime.json',
        controller_unit='pult-synthetic-controller.service',action=dict(controller=None),
        ingress=dict(uid=50001,gid=50002,directory='/run/pult-synthetic-ingress',
            socket_file='/run/pult-synthetic-ingress/control.sock',config_file='/run/pult-synthetic-ingress/config.json',
            source=dict(path='/opt/pult-synthetic/ssh_ingress.py',sha256='b'*64)))

class EntryTests(unittest.TestCase):
    def test_static_root_plan_has_no_preapproved_pid(self):
        value=proposal();self.assertEqual(e.document(e.canonical(value)),value)
        for extra in ('command','event','credential','reset','runtime_controller','shell','bootstrap'):
            with self.assertRaises(e.Refused):e.document(e.canonical(dict(value,**{extra:True})))
    def test_root_plan_cannot_adopt_previous_controller(self):
        for controller in ({},dict(pid=1),False,'auto'):
            value=proposal();value['action']['controller']=controller
            with self.assertRaises(e.Refused):e.document(e.canonical(value))
    def test_ingress_scope_and_principal_bounds(self):
        for field,bad in (('uid',0),('gid',999),('uid',True),('gid',2**31),('socket_file','/elsewhere/a'),
                          ('config_file','/run/pult-synthetic-ingress/control.sock'),('directory','/run/../pult')):
            value=proposal();value['ingress'][field]=bad
            with self.assertRaises(e.Refused):e.document(e.canonical(value))
    def test_duplicate_noncanonical_or_missing_fields_refuse(self):
        raw=e.canonical(proposal())
        for bad in (raw[:-1],raw+b' ',b'[]\n',b'{}\n',raw.replace(b'{',b'{"action":null,',1),b'x'*49153):
            with self.assertRaises(e.Refused):e.document(bad)
    def test_parser_is_effect_free(self):
        with ExitStack() as stack:
            for owner,name in ((os,'open'),(os,'write'),(socket,'socket'),(Path,'read_text')):
                stack.enter_context(mock.patch.object(owner,name,side_effect=AssertionError('effect')))
            self.assertEqual(e.document(e.canonical(proposal())),proposal())
    def test_wrong_runtime_precedes_any_plan_open(self):
        with mock.patch.object(sys,'platform','win32'),mock.patch.object(e,'root_bytes',side_effect=AssertionError('read')):
            with self.assertRaises(e.Refused):e.run('/not-read','a'*64)
    def test_root_reader_ignores_only_atime_changes(self):
        before=SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|0o644,st_uid=0,st_gid=0,
            st_nlink=1,st_size=3,st_mtime_ns=10,st_ctime_ns=11,st_atime_ns=12)
        after=SimpleNamespace(**dict(before.__dict__,st_atime_ns=99))
        directory=SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0)
        with mock.patch.object(Path,'lstat',side_effect=[directory,directory,after]), \
             mock.patch.object(os,'open',return_value=9),mock.patch.object(os,'close'), \
             mock.patch.object(os,'fstat',side_effect=[before,after]), \
             mock.patch.object(os,'listxattr',return_value=[]),mock.patch.object(os,'pread',return_value=b'abc'):
            self.assertEqual(e.root_bytes('/opt/source.py',10),b'abc')
    def test_root_stability_preserves_every_security_field(self):
        before=SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|0o644,st_uid=0,st_gid=0,
            st_nlink=1,st_size=3,st_mtime_ns=10,st_ctime_ns=11,st_atime_ns=12)
        self.assertEqual(e.stable(before),e.stable(SimpleNamespace(**dict(before.__dict__,st_atime_ns=99))))
        for field in before.__dict__:
            if field!='st_atime_ns':
                self.assertNotEqual(e.stable(before),e.stable(SimpleNamespace(**dict(before.__dict__,**{field:getattr(before,field)+1}))))
    def test_child_bootstrap_uses_stable_security_fingerprint(self):
        spec=importlib.util.spec_from_file_location('test_child_stable',SOURCE/'action_child.py')
        child=importlib.util.module_from_spec(spec);spec.loader.exec_module(child)
        before=SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|0o644,st_uid=0,st_gid=0,
            st_nlink=1,st_size=3,st_mtime_ns=10,st_ctime_ns=11,st_atime_ns=12)
        self.assertEqual(child.stable(before),e.stable(before))
        self.assertEqual(child.stable(before),child.stable(SimpleNamespace(**dict(before.__dict__,st_atime_ns=99))))
        import ast
        tree=ast.parse((SOURCE/'action_child.py').read_bytes())
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='stable']
        self.assertEqual(len(calls),4)
    def test_actual_credential_packet_accepted_with_kernel_cloexec_flag(self):
        first,second=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        try:
            second.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
            peer=(os.getpid(),os.getuid(),os.getgid())
            caller=object.__new__(e.Caller);caller.connection=second;caller.peer=peer;caller.check=mock.Mock()
            first.send(b'TEST_FIXED_TRIGGER\n');caller.receive(b'TEST_FIXED_TRIGGER\n')
            caller.check.assert_called_once()
        finally:first.close();second.close()
    def test_delivered_rights_are_closed_before_refusal(self):
        first,second=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        fd=os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC)
        try:
            second.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
            caller=object.__new__(e.Caller);caller.connection=second
            caller.peer=(os.getpid(),os.getuid(),os.getgid());caller.check=mock.Mock()
            first.sendmsg([b'TEST\n'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])
            before=set(os.listdir('/proc/self/fd'))
            with self.assertRaises(e.Refused):caller.receive(b'TEST\n')
            self.assertEqual(set(os.listdir('/proc/self/fd')),before);caller.check.assert_not_called()
        finally:os.close(fd);first.close();second.close()
    def test_wrong_packet_and_extra_credential_refuse(self):
        caller=object.__new__(e.Caller);caller.peer=(123,50001,50002);caller.check=mock.Mock()
        cred=(socket.SOL_SOCKET,socket.SCM_CREDENTIALS,struct.pack('3i',*caller.peer))
        for body,ancillary,flags in ((b'BAD', [cred],0),(b'OK',[],0),(b'OK',[cred,cred],0),
                                      (b'OK',[cred],socket.MSG_TRUNC)):
            caller.connection=mock.Mock();caller.connection.recvmsg.return_value=(body,ancillary,flags,None)
            with self.assertRaises(e.Refused):caller.receive(b'OK')
        caller.check.assert_not_called()
    def test_fault_gate_rejects_missing_get_wrong_fault_and_retries(self):
        spec=importlib.util.spec_from_file_location('test_native_fault_contract',HERE/'native_storage_chain.py')
        native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
        native.fault_trace('happy',['PUT','GET'],None)
        native.fault_trace('write_unknown',['PUT'],'write_unknown_after_put')
        native.fault_trace('read_fail',['PUT','GET'],'read_fail_get_error')
        for mode,methods,activation in (('read_fail',['PUT'],None),('read_fail',['PUT'],'read_fail_get_error'),
            ('read_fail',['PUT','GET'],None),('read_fail',['PUT','GET','GET'],'read_fail_get_error'),
            ('write_unknown',['PUT','PUT'],'write_unknown_after_put'),('happy',['PUT','GET'],'read_fail_get_error')):
            with self.assertRaises(RuntimeError):native.fault_trace(mode,methods,activation)

if __name__=='__main__':unittest.main()
