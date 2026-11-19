"""Offline packaging contracts; pure report tests never confer host authority."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'ops/backup/custodian_v2'
TESTS=ROOT/'ops/backup/tests/custodian_v2/intake'
WORKFLOW='.github/workflows/custodian_source_intake.yml'
GUARD='backend/tests/test_custodian_source_intake_ci.py'
ENTRY='ops/backup/tests/custodian_v2/intake/'
ENTRY_SHA='48c238948c9f2c94ed485093b0a583ebcaf7dca760612622e5a01eebd063617c'
WORKER_SHA='c8853243aad6e27a04193af99d611cf1ece92ab56a0e925f9965e38bb5b344b2'
LIBS={'ci_gate.py':'d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b',
      'scope_control.py':'d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234'}
PATHS=['ops/backup/**','ops/canary/canary.py',GUARD,WORKFLOW,'.gitattributes']
REFUSALS={'ci_gate.py':'optimized_intake_ci_refused','run_gate.py':'optimized_intake_gate_refused',
          'verify_receipt.py':'optimized_intake_verifier_refused'}
NAMES=['Checkout repository','Set up Python','Install locked CI dependencies',
       'Guard dormant source intake and workflow','Download seven hash-pinned synthetic wheels',
       'Native source intake and independent postcheck','Reject optimized intake runtimes']
COMMANDS={2:'python -m pip install --require-hashes -r backend/requirements-ci.lock',
          3:'python -m pytest '+GUARD+' -q',
          4:'set -euo pipefail\ntest -x /usr/bin/python3.12\npython -m pip download --only-binary=:all: --no-deps --require-hashes -r ops/backup/custodian/requirements.lock -d ops/backup/tests/custodian_v2/wheels',
          5:'sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B '+ENTRY+'ci_gate.py --reviewed-by-main'}
OPTIMIZED='''\
import json
import subprocess
root = "ops/backup/tests/custodian_v2/intake/"
for file, reason in (("ci_gate.py", "optimized_intake_ci_refused"), ("run_gate.py", "optimized_intake_gate_refused"), ("verify_receipt.py", "optimized_intake_verifier_refused")):
    result = subprocess.run(["sudo", "-n", "--", "unshare", "--net", "/usr/bin/python3.12", "-I", "-S", "-B", "-O", root + file, "--reviewed-by-main"], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 2 or result.stderr or json.loads(result.stdout) != {"outcome": "NOT_RUN", "reason": reason}:
        raise SystemExit("optimized source-intake runtime did not return exact refusal")
'''

def shape(source): return ast.dump(ast.parse(source))

def literal(tree,name):
    return ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)))

def require_nodes(source,*statements):
    nodes={ast.dump(n) for n in ast.walk(ast.parse(source))}
    for statement in statements:
        assert ast.dump(ast.parse(statement).body[0]) in nodes

def workflow(): return yaml.load((ROOT/WORKFLOW).read_text(encoding='utf-8'),Loader=yaml.BaseLoader)

def check_workflow(value):
    assert set(value)=={'name','on','permissions','jobs'} and value['name']=='Custodian Source Intake'
    assert value['on']=={'pull_request':{'paths':PATHS},'push':{'branches':['master'],'paths':PATHS},'workflow_dispatch':''}
    assert value['permissions']=={'contents':'read'} and set(value['jobs'])=={'source-intake'}
    job=value['jobs']['source-intake']
    assert set(job)=={'runs-on','timeout-minutes','steps'} and job['runs-on']=='ubuntu-24.04' and job['timeout-minutes']=='20'
    steps=job['steps'];assert [s['name'] for s in steps]==NAMES
    assert steps[0]==dict(name=NAMES[0],uses='actions/checkout@11d5960a326750d5838078e36cf38b85af677262',**{'with':{'persist-credentials':'false'}})
    assert steps[1]==dict(name=NAMES[1],uses='actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',**{'with':{'python-version':'3.13'}})
    for index in range(2,7):
        assert set(steps[index])=={'name','run'}
        if index in COMMANDS: assert steps[index]['run'].strip()==COMMANDS[index]
    lines=steps[6]['run'].splitlines()
    assert lines[:2]==['set -euo pipefail',"python - <<'PY'"] and lines[-1]=='PY'
    assert shape('\n'.join(lines[2:-1]))==shape(OPTIMIZED)

def check_draft(value):
    assert value==dict(format='pult-staged-source-intake-preparation-v1',status='DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED',
        operator_approval=None,admitted_operations=[],working_credentials_included=False,database_or_pending_included=False,
        installation_service_or_network_actions=False,scope='SOURCE_INTAKE_ONLY_NOT_HOST_WRITE_OUTER_STOP_OR_DEPLOYMENT',
        runtime=dict(implementation='cpython',major_minor=[3,12],platform='linux'),sources={'staged_intake.py':ENTRY_SHA})
    for key in ('working_credentials_included','database_or_pending_included','installation_service_or_network_actions'):
        assert value[key] is False

def test_frozen_sources_draft_and_workflow():
    for file,wanted in ((SOURCE/'intake/staged_intake.py',ENTRY_SHA),(TESTS/'worker.py',WORKER_SHA),
        (SOURCE/'cold/cold_host.py','091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'),
        (SOURCE/'source-profile.json','8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'),
        *((TESTS.parent/'controller'/name,wanted) for name,wanted in LIBS.items())):
        assert hashlib.sha256(file.read_bytes()).hexdigest()==wanted
    check_draft(json.loads((SOURCE/'intake/source-profile.json').read_bytes()));check_workflow(workflow())
    assert {p.name for p in (SOURCE/'intake').iterdir()}=={'staged_intake.py','source-profile.json'}
    assert {p.name for p in TESTS.glob('*.py')}=={'run_gate.py','worker.py','ci_gate.py','verify_receipt.py'}

@pytest.mark.parametrize('key,value',[('operator_approval','yes'),('admitted_operations',['append']),
    ('working_credentials_included',True),('database_or_pending_included',True),('status','APPROVED')])
def test_draft_cannot_activate(key,value):
    body=json.loads((SOURCE/'intake/source-profile.json').read_bytes());body[key]=value
    with pytest.raises(AssertionError): check_draft(body)

@pytest.mark.parametrize('mutation',['skip','continue','permissions','services','upload','credentials','unpinned','direct_gate','hashes','network','optimized'])
def test_workflow_boundary_mutations(mutation):
    value=workflow();job=value['jobs']['source-intake'];steps=job['steps']
    if mutation=='skip': steps[5]['if']='false'
    elif mutation=='continue': steps[5]['continue-on-error']='true'
    elif mutation=='permissions': value['permissions']['contents']='write'
    elif mutation=='services': job['services']={'unapproved':{}}
    elif mutation=='upload': steps.append({'name':'Upload','uses':'actions/upload-artifact@'+'0'*40})
    elif mutation=='credentials': steps[0]['with']['persist-credentials']='true'
    elif mutation=='unpinned': steps[0]['uses']='actions/checkout@v4'
    elif mutation=='direct_gate': steps[5]['run']=steps[5]['run'].replace('ci_gate.py','run_gate.py')
    elif mutation=='hashes': steps[4]['run']=steps[4]['run'].replace('--require-hashes ','')
    elif mutation=='network': steps[5]['run']=steps[5]['run'].replace('--net ','')
    else: steps[6]['run']=steps[6]['run'].replace('result.returncode != 2','False')
    with pytest.raises(AssertionError): check_workflow(value)

@pytest.mark.parametrize('file,reason',REFUSALS.items())
def test_optimization_refused_before_work(file,reason):
    tree=ast.parse((TESTS/file).read_bytes())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    body=[n for n in main.body if not isinstance(n,ast.Global)]
    expected=ast.parse('if sys.flags.optimize:\n    print('+repr(json.dumps(dict(outcome='NOT_RUN',reason=reason),separators=(',',':')))+');return 2')
    assert ast.dump(body[0])==ast.dump(expected.body[0])

def test_native_scope_closure_and_reviewed_supervisor():
    gate=(TESTS/'run_gate.py').read_text(encoding='utf-8')
    assert len(literal(ast.parse(gate),'CASES'))==31
    require_nodes(gate,"REPO=HERE.parents[4]","base=Path(tempfile.mkdtemp(prefix='i-',dir=scope['work'])).resolve()",
        "assert base.parent==Path(scope['work']) and base.name.startswith('i-')",
        "assert sys.platform=='linux' and sys.version_info[:2]==(3,12) and os.geteuid()==0 and os.getpid()==1",
        "assert [row.split(':',1)[0].strip() for row in Path('/proc/self/net/dev').read_text().splitlines()[2:]]==['lo']",
        "assert value['ok'] is (mode in positive) and value['refused'] is (mode not in positive)")
    wrapper=(TESTS/'ci_gate.py').read_text(encoding='utf-8')
    assert literal(ast.parse(wrapper),'PINS')==LIBS
    require_nodes(wrapper,"if hashlib.sha256(raw).hexdigest()!=wanted: raise RuntimeError('FIXED_CI_LIBRARY_REFUSED')",
        "with supervisor.create_scope(control) as scope:\n    result,clean,reason=supervisor.supervised_run(scope,control,ROOT/'run_gate.py')",
        "digest=verifier().verify(file)")
    verifier=(TESTS/'verify_receipt.py').read_text(encoding='utf-8')
    require_nodes(verifier,"require(len(paths)==32)","require(set(paths)==set(value['source_hashes_before']))",
        "for name,path in paths.items(): require(hashlib.sha256(path.read_bytes()).hexdigest()==value['source_hashes_before'][name])",
        "require(not any(name.startswith('system.posix_acl_') for name in os.listxattr(file,follow_symlinks=False)))")
    assert 'info.st_uid==0' in verifier and 'stat.S_IMODE(info.st_mode) in (0o444,0o644)' in verifier

@pytest.fixture(scope='module')
def verifier():
    file=TESTS/'verify_receipt.py';spec=importlib.util.spec_from_file_location('pure_intake_report_contract',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def report(verifier):
    active={'happy','captured_source_checkpoint','uncaptured_probe_checkpoint','loader_drift','repeat_capture','repeat_activate'}
    captured=active|{'capture_only','profile_checkpoint'}
    rows=[]
    for name in sorted(verifier.CASES):
        positive=name in active or name=='capture_only'
        m={key:0 for key in ('sql','credential_opens','credential_body_reads','network_attempts','listener_attempts','capture_payload_exec','profile_reads')}
        m.update({key:True for key in ('candidate_pin','modules_clean','finder_clean','capture_clean','no_host_methods',
            'summary_exact','all_false_flags','no_module_export','modules_identity','captured_source_used','marker_absent',
            'repeat_capture_refused','repeat_activate_refused','drift_blocked','probe_blocked','repeat_no_exec','probe_no_exec','optimized')})
        m.update(ok=positive,refused=not positive,interrupted=False,outcome='ok' if positive else 'refused',
                 captured=name in captured,activated=name in active,payload_exec=1 if name in active else 0)
        rows.append(dict(case=name,outcome='PASS',metrics=m))
    return dict(format='pult-staged-source-intake-native-proof-v1',outcome='PASS',
        scope='LOCAL_SOURCE_INTAKE_ONLY_NOT_HOST_WRITE_OR_OUTER_STOP',tests=31,failures=[],skips=0,results=rows,
        source_hashes_before={'fixture':'0'*64},source_hashes_after={'fixture':'0'*64},sources_unchanged=True,
        remaining_owned_processes=[],native_tree_removed=True,private_pid_network_namespace=True,
        elapsed_seconds=1.0,new_paid_resources=0,additional_spend_rub=0)

def test_complete_report(verifier):
    value=report(verifier);assert set(value)==verifier.FIELDS and verifier.valid_summary(value)
    assert verifier.CASES==set(literal(ast.parse((TESTS/'run_gate.py').read_bytes()),'CASES'))

@pytest.mark.parametrize('key,value',[('tests',True),('tests',30),('tests',32),('skips',1),
    ('sources_unchanged',1),('native_tree_removed',False),('private_pid_network_namespace',False),
    ('remaining_owned_processes',[1]),('source_hashes_after',{}),('elapsed_seconds',float('nan')),
    ('elapsed_seconds',float('inf')),('elapsed_seconds',10**400),('elapsed_seconds',901),
    ('new_paid_resources',False),('additional_spend_rub',1),('scope','HOST_WRITE')])
def test_bad_report(verifier,key,value):
    body=report(verifier);body[key]=value;assert not verifier.valid_summary(body)

@pytest.mark.parametrize('key',list(('format','outcome','tests','failures','results','source_hashes_before','native_tree_removed','extra_authority')))
def test_report_field_closure(verifier,key):
    body=report(verifier)
    if key in body: del body[key]
    else: body[key]=True
    assert not verifier.valid_summary(body)

@pytest.mark.parametrize('case,key,value',[('happy','ok',False),('capture_only','refused',True),
    ('happy','outcome','refused'),('happy','interrupted',True),('wrong_mode','ok',True),
    ('wrong_mode','refused',False),('happy','sql',False),('happy','credential_opens',1),
    ('happy','network_attempts',1),('happy','listener_attempts',1),('happy','capture_payload_exec',1),
    ('happy','all_false_flags',False),('happy','no_module_export',False),('profile_checkpoint','payload_exec',1),
    ('repeat_capture','repeat_no_exec',False),('loader_drift','drift_blocked',False),
    ('uncaptured_probe_checkpoint','probe_no_exec',False),('optimized','profile_reads',1)])
def test_bad_case_metrics(verifier,case,key,value):
    body=deepcopy(report(verifier));next(r for r in body['results'] if r['case']==case)['metrics'][key]=value
    assert not verifier.valid_summary(body)

def test_missing_duplicate_or_failed_case(verifier):
    for mutation in ('missing','duplicate','failed'):
        body=report(verifier)
        if mutation=='missing': body['results'].pop()
        elif mutation=='duplicate': body['results'][0]=deepcopy(body['results'][1])
        else: body['results'][0]['outcome']='FAIL'
        assert not verifier.valid_summary(body)
