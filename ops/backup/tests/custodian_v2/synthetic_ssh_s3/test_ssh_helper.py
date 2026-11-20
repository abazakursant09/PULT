"""Linux helper contracts and real owned-child timeout; no SSH/network/key use."""
import base64
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import struct
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2/synthetic_ssh_s3'
def load(file,name):
    spec=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m
h=load(SOURCE/'ssh_helper.py','test_fixed_helper')
t=load(SOURCE/'bounded_ssh.py','test_fixed_bounded_ssh')

def proposal():
    return dict(format=h.FORMAT,client_uid=49001,client_gid=49002,custody_host='1.1.1.2',
        server_key_sha256='SHA256:'+'A'*43,client_key_sha256='SHA256:'+'B'*43,
        identity_file='/var/lib/pult-synthetic-client/key',known_hosts_file='/etc/pult-synthetic-client/known_hosts',
        source=dict(path='/opt/pult-synthetic-client/bounded_ssh.py',sha256='a'*64))

class HelperTests(unittest.TestCase):
    def test_exact_closed_nonsecret_plan(self):
        value=proposal();self.assertEqual(h.document(h.canonical(value)),value)
        for key in ('command','port','username','options','s3','event','approval','transport','proxy'):
            with self.assertRaises(h.Refused):h.document(h.canonical(dict(value,**{key:'bad'})))
    def test_bad_paths_addresses_ids_and_pins_refuse(self):
        for field,values in (('custody_host',('127.0.0.1','10.0.0.1','224.0.0.1','0.0.0.0','::1','1.1.1.01','example.com',True)),
            ('client_uid',(0,999,True,-1,2**31)),('client_gid',(0,999,True,-1,2**31)),
            ('identity_file',('/','/a/../key','/a//key','key','/a/./key','/a\x00b')),
            ('client_key_sha256',('','SHA256:no','a'*64,True))):
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaises(h.Refused):
                    h.document(h.canonical(dict(proposal(),**{field:value})))
    def test_duplicate_noncanonical_and_bounds_refuse(self):
        raw=h.canonical(proposal())
        for body in (raw[:-1],raw+b' ',b' '+raw,raw.replace(b'{',b'{"format":null,',1),
                     raw.replace(b'{',b'{"x":NaN,',1),b'x'*8193,b'[]\n',None):
            with self.assertRaises(h.Refused):h.document(body)
    def test_parser_and_argv_builder_have_no_io(self):
        with mock.patch.object(os,'open',side_effect=AssertionError('unexpected IO')), \
             mock.patch.object(t.subprocess,'Popen',side_effect=AssertionError('unexpected process')):
            h.document(h.canonical(proposal()));args=t.build_ssh_argv('1.1.1.2')
        self.assertEqual(args[-1],'pult-synthetic-one-v1');self.assertEqual(args[args.index('-l')+1],'pult-synthetic-trigger')
        self.assertEqual(args[args.index('-p')+1],'22');self.assertIn('StrictHostKeyChecking=yes',args)
        self.assertIn('IdentityAgent=none',args);self.assertIn('ProxyCommand=none',args);self.assertIn('ClearAllForwardings=yes',args)
        self.assertIn('SendEnv=-*',args);self.assertEqual(args[:3],('/usr/bin/ssh','-F','none'))
    def test_wire_constants_match_ingress(self):
        ingress=load(SOURCE/'ssh_ingress.py','helper_ingress_wire_proof')
        self.assertEqual(h.TRIGGER,ingress.TRIGGER);self.assertEqual(h.REPLY,ingress.STORAGE_ONLY_REPLY)
        self.assertLessEqual(len(h.TRIGGER),t.proto.MAX_REQUEST);self.assertLessEqual(len(h.REPLY),t.proto.MAX_RESPONSE)
    def test_fingerprint_exact_ed25519_binary_format(self):
        blob=struct.pack('>I',11)+b'ssh-ed25519'+struct.pack('>I',32)+b'\x01'*32
        value=h.fingerprint(b'ssh-ed25519',base64.b64encode(blob))
        self.assertRegex(value,r'^SHA256:[A-Za-z0-9+/]{43}$')
        for kind,data in ((b'ssh-rsa',base64.b64encode(blob)),(b'ssh-ed25519',b'bad'),
                          (b'ssh-ed25519',base64.b64encode(blob+b'x')),
                          (b'ssh-ed25519',base64.b64encode(blob.replace(struct.pack('>I',32),struct.pack('>I',31))))):
            with self.assertRaises(h.Refused):h.fingerprint(kind,data)
    def test_real_memfd_seals_not_plaintext_temporary_file(self):
        before=set(os.listdir('/proc/self/fd'));fd=h.sealed(b'ARTIFICIAL_ONLY_NOT_A_KEY\n')
        try:
            self.assertEqual(os.read(fd,100),b'ARTIFICIAL_ONLY_NOT_A_KEY\n')
            self.assertFalse(os.get_inheritable(fd));self.assertEqual(os.fstat(fd).st_mode&0o777,0o400)
            for change in (lambda:os.write(fd,b'X'),lambda:os.ftruncate(fd,0)):
                with self.assertRaises(OSError):change()
            self.assertTrue(fcntl.fcntl(fd,fcntl.F_GET_SEALS)&fcntl.F_SEAL_WRITE)
        finally:os.close(fd)
        self.assertEqual(set(os.listdir('/proc/self/fd')),before)
    def test_wrong_runtime_before_file_or_key_read(self):
        with mock.patch.object(sys,'platform','win32'),mock.patch.object(h,'read_file',side_effect=AssertionError('read')):
            with self.assertRaises(h.Refused):h.run('/etc/not-read','a'*64)
    def test_helper_reader_ignores_only_atime_changes(self):
        before=SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|0o644,st_uid=0,st_gid=0,
            st_nlink=1,st_size=3,st_mtime_ns=10,st_ctime_ns=11,st_atime_ns=12)
        after=SimpleNamespace(**dict(before.__dict__,st_atime_ns=99))
        directory=SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0)
        with mock.patch.object(Path,'lstat',side_effect=[directory,directory,after]), \
             mock.patch.object(os,'open',return_value=9),mock.patch.object(os,'close'), \
             mock.patch.object(os,'fstat',side_effect=[before,after]), \
             mock.patch.object(os,'listxattr',return_value=[]),mock.patch.object(os,'pread',return_value=b'abc'):
            self.assertEqual(h.read_file('/opt/source.py',10),b'abc')
    def test_helper_stability_preserves_every_security_field(self):
        before=SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|0o644,st_uid=0,st_gid=0,
            st_nlink=1,st_size=3,st_mtime_ns=10,st_ctime_ns=11,st_atime_ns=12)
        self.assertEqual(h.stable(before),h.stable(SimpleNamespace(**dict(before.__dict__,st_atime_ns=99))))
        for field in before.__dict__:
            if field!='st_atime_ns':
                self.assertNotEqual(h.stable(before),h.stable(SimpleNamespace(**dict(before.__dict__,**{field:getattr(before,field)+1}))))
    def test_ssh_builder_refuses_injection_or_nonliteral_target(self):
        for host in ('-oProxyCommand=id','example.com','1.2.3.4;id','127.0.0.1','0.0.0.0','224.0.0.1','::1'):
            with self.assertRaises(t.TransportRefused):t.build_ssh_argv(host)
    def test_real_child_success_requires_reaping_and_closed_fds(self):
        before=set(os.listdir('/proc/self/fd'))
        result=t._run_child(b'',('/usr/bin/python3.12','-I','-S','-B','-c','import os;os.write(1,b"OK\\n")'),timeout=3.0)
        self.assertEqual(result,(b'OK\n',0));self.assertEqual(set(os.listdir('/proc/self/fd')),before)
    def test_real_hung_child_is_killed_and_reaped_before_unknown(self):
        before=set(os.listdir('/proc/self/fd'));start=time.monotonic()
        code='import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)'
        self.assertIsNone(t._run_child(b'',('/usr/bin/python3.12','-I','-S','-B','-c',code),timeout=.2))
        self.assertLess(time.monotonic()-start,3);self.assertEqual(set(os.listdir('/proc/self/fd')),before)
        with self.assertRaises(ChildProcessError):os.waitid(os.P_ALL,0,os.WEXITED|os.WNOHANG|os.WNOWAIT)
    def test_overlong_response_never_becomes_success(self):
        code='import os;os.write(1,b"X"*1000)'
        self.assertIsNone(t._run_child(b'',('/usr/bin/python3.12','-I','-S','-B','-c',code),timeout=3.0))
    def test_stderr_is_not_silent_success_even_with_zero_exit(self):
        code='import os;os.write(1,b"OK\\n");os.write(2,b"UNEXPECTED\\n")'
        self.assertIsNone(t._run_child(b'',('/usr/bin/python3.12','-I','-S','-B','-c',code),timeout=3.0))
    def test_terminal_output_is_bounded_and_fd_is_closed(self):
        with mock.patch.object(os,'dup',return_value=9),mock.patch.object(os,'set_inheritable'), \
             mock.patch.object(os,'get_blocking',return_value=True),mock.patch.object(os,'set_blocking') as blocking, \
             mock.patch.object(os,'close') as close,mock.patch.object(h.time,'monotonic',side_effect=[0,3]), \
             mock.patch.object(os,'write',side_effect=AssertionError('deadline before write')):
            with self.assertRaises(h.Refused):h.emit_reply()
            close.assert_called_once_with(9);self.assertEqual(blocking.call_args_list, [mock.call(9,False),mock.call(9,True)])

if __name__=='__main__':unittest.main()
