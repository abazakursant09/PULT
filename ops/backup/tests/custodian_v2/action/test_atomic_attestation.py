"""Deterministic real Linux publication race/overwrite regression, no services."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[5]/'ops/backup/custodian_v2/action'
def load(file,name):
    spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module
common=load(HERE/'action_profile.py','pult_action_profile_v3')
root=load(HERE/'action_root.py','atomic_publication_root')

class Publication(unittest.TestCase):
    def test_final_absent_through_all_writes_then_complete_single_link(self):
        with tempfile.TemporaryDirectory(prefix='pult-atomic-attest-',dir='/var/tmp') as directory:
            directory=Path(directory);directory.chmod(0o755)
            value=common.canonical(dict(format='synthetic-publication-race-proof',payload='x'*2000))
            observations=[]
            def writes(fd,raw):
                for offset in range(0,len(raw),17):
                    with self.assertRaises(FileNotFoundError): common.root_bytes(directory/'custodian.attested',4096)
                    observations.append(True)
                    self.assertEqual(os.write(fd,raw[offset:offset+17]),len(raw[offset:offset+17]))
            with patch.object(root,'write_all',writes):
                final=root.publish_attestation(directory,'custodian',value)
            self.assertGreater(len(observations),100)
            self.assertEqual(common.root_bytes(final,4096,(0o444,)),value)
            self.assertEqual(final.stat().st_nlink,1)
            self.assertFalse((directory/'custodian.pending').exists())

    def test_published_file_never_replaced_or_adopted(self):
        with tempfile.TemporaryDirectory(prefix='pult-atomic-attest-',dir='/var/tmp') as directory:
            directory=Path(directory);directory.chmod(0o755)
            final=root.publish_attestation(directory,'writer',b'ORIGINAL\n')
            before=common.identity(final.stat())
            with self.assertRaises(FileExistsError):root.publish_attestation(directory,'writer',b'REPLACEMENT\n')
            self.assertEqual(common.identity(final.stat()),before)
            self.assertEqual(common.root_bytes(final,64,(0o444,)),b'ORIGINAL\n')

    def test_old_direct_path_publication_is_rejected_by_race_observer(self):
        with tempfile.TemporaryDirectory(prefix='pult-atomic-attest-',dir='/var/tmp') as directory:
            directory=Path(directory);directory.chmod(0o755)
            final=directory/'custodian.attested'
            fd=os.open(final,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o444)
            try:
                # Deterministic old-red witness: child sees a published empty
                # file, not FileNotFoundError, before the first byte is written.
                with self.assertRaises(AssertionError):
                    with self.assertRaises(FileNotFoundError):common.root_bytes(final,4096,(0o444,))
            finally:os.close(fd)

if __name__=='__main__':unittest.main()
