"""Test-only exact predecessor imports, no runtime transport/config branch."""
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2/synthetic_ssh_s3'
BASE=REPO/'ops/backup/custodian_v2'
CATALOGUE_SHA='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'

def need(ok):
    if not ok:raise RuntimeError('EXACT_TEST_SOURCE_REFUSED')

def sha(raw):return hashlib.sha256(raw).hexdigest()

def load(file,name,wanted=None):
    raw=file.read_bytes()
    if wanted is not None:need(sha(raw)==wanted)
    m=ModuleType(name);m.__file__=str(file);sys.modules[name]=m
    exec(compile(raw,str(file),'exec',dont_inherit=True,optimize=0),m.__dict__)
    return m

def predecessor_sources():
    raw=(BASE/'source-profile.json').read_bytes();need(sha(raw)==CATALOGUE_SHA)
    rows={}
    for logical,wanted in json.loads(raw)['sources'].items():
        relative=('ops/backup/custodian_v2/'+logical if logical=='frozen/ops/backup/erasure_journal_s3.py'
                  or not logical.startswith('frozen/') else logical.removeprefix('frozen/'))
        file=REPO/relative;need(sha(file.read_bytes())==wanted);rows[logical]=(file,wanted)
    return rows

def base_store():
    rows=predecessor_sources()
    fixture_file=REPO/'ops/backup/tests/custodian_v2/support.py'
    fixture=load(fixture_file,'fixed_public_ci_fixture_support','d343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d')
    wheels=REPO/'ops/backup/tests/custodian_v2/wheels'
    for name,digest in fixture.wheel_pins().items():
        file=wheels/name;raw=file.read_bytes();need(sha(raw)==digest and 0<len(raw)<2*1024*1024)
        sys.path.insert(0,str(file))
    for name in ('ops','ops.backup','ops.canary'):
        m=ModuleType(name);m.__path__=[];sys.modules[name]=m
    for logical,name in (
        ('frozen/ops/canary/canary.py','ops.canary.canary'),
        ('frozen/ops/backup/erasure_journal.py','ops.backup.erasure_journal'),
        ('frozen/ops/backup/erasure_journal_s3.py','ops.backup.erasure_journal_s3'),
        ('codec/event_codec.py','event_codec')):
        file,wanted=rows[logical];load(file,name,wanted)
    return load(SOURCE/'live_event_store.py','pult_synthetic_store_v1'),sys.modules['event_codec']

def root_modules():
    # Linux-only lifecycle unit tests; never invoke systemd or target payload.
    common=load(SOURCE/'action_profile.py','pult_synthetic_live_profile_v1')
    root=load(SOURCE/'action_root.py','test_synthetic_action_root')
    return common,root
