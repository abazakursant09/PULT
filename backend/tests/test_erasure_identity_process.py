"""Real abrupt child-process exit on synthetic SQLite; not a power-loss proof."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_erasure_identity import candidate, initialize, registry

ROOT = Path(__file__).resolve().parents[2]
CHILD = r'''
import json,os,sqlite3,sys
from contextlib import contextmanager
sys.path.insert(0,sys.argv[1])
from ops.backup.erasure_identity import IdentityRegistry
spec=json.loads(sys.argv[2])
value=IdentityRegistry(spec['path'],scope=spec['scope'],generation=spec['generation'],registry_id=spec['registry_id'])
original=value._storage._connection
class Crash:
    def __init__(self,connection):self.connection=connection
    def execute(self,sql,*args):
        if spec['phase']=='before' and sql=='COMMIT':os._exit(31)
        result=self.connection.execute(sql,*args)
        if spec['phase']=='middle' and sql.startswith('INSERT INTO identity_subjects'):os._exit(32)
        if spec['phase']=='after' and sql=='COMMIT':os._exit(33)
        return result
@contextmanager
def connection(*args,**kwargs):
    with original(*args,**kwargs) as actual:yield Crash(actual)
value._storage._connection=connection
receipt=value.record_reviewed(**spec['candidate'])
print(json.dumps({'status':receipt.status,'subject_id':receipt.binding.subject_id,'purge_id':receipt.binding.purge_id}))
'''


@pytest.mark.parametrize("phase,exit_code,persisted", [
    ("middle", 32, False), ("before", 31, False), ("after", 33, True), ("normal", 0, True),
])
def test_actual_process_exit_preserves_atomic_binding_and_no_admission(tmp_path, phase, exit_code, persisted):
    value = initialize(tmp_path)
    request = candidate()
    spec = dict(path=str(value._storage._path), scope=value.scope, generation=value.generation,
                registry_id=value.registry_id, candidate=request, phase=phase)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    result = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, str(ROOT), json.dumps(spec)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == exit_code, result.stderr
    assert not result.stderr
    reopened = registry(value._storage._path)
    binding = reopened.inspect(request["account_id"])
    assert (binding is not None) is persisted
    assert reopened.inspect() == int(persisted)
    if persisted:
        observed = reopened.record_reviewed(**request)
        assert observed.status == "OBSERVED_BINDING_ONLY" and observed.binding == binding
        assert not observed.journal_dispatch_admitted and not observed.purge_or_restore_admitted
    if phase == "normal":
        receipt = json.loads(result.stdout)
        assert receipt == dict(status="RECORDED_BINDING_ONLY", subject_id=binding.subject_id, purge_id=binding.purge_id)
    else:
        assert not result.stdout
