"""Independent source-intake receipt/current-input/resource check; no authority."""
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[4]
CASES={'happy','capture_only','wrong_approval','old_profile','wrong_mode','extra_field',
 'duplicate_profile','noncanonical','profile_symlink','profile_hardlink','profile_userowned',
 'profile_writable','profile_acl','cold_tamper','stage_tamper','base_tamper','wheel_tamper',
 'dependency_tamper','release_extra','staged_inside_release','preloaded_app','preloaded_stage',
 'foreign_finder','foreign_path','optimized','profile_checkpoint','captured_source_checkpoint',
 'uncaptured_probe_checkpoint','loader_drift','repeat_capture','repeat_activate'}
FIELDS={'format','outcome','scope','tests','failures','skips','results','source_hashes_before','source_hashes_after',
 'sources_unchanged','remaining_owned_processes','native_tree_removed','private_pid_network_namespace',
 'elapsed_seconds','new_paid_resources','additional_spend_rub'}

def require(value):
    if not value: raise ValueError('INTAKE_RECEIPT_REFUSED')

def valid_summary(value):
    try:
        require(type(value) is dict and set(value)==FIELDS)
        require(value['format']=='pult-staged-source-intake-native-proof-v1' and value['outcome']=='PASS')
        require(value['scope']=='LOCAL_SOURCE_INTAKE_ONLY_NOT_HOST_WRITE_OR_OUTER_STOP')
        require(type(value['tests']) is int and value['tests']==31 and value['failures']==[])
        for key in ('skips','new_paid_resources','additional_spend_rub'): require(type(value[key]) is int and value[key]==0)
        for key in ('sources_unchanged','native_tree_removed','private_pid_network_namespace'): require(value[key] is True)
        require(value['remaining_owned_processes']==[])
        elapsed=value['elapsed_seconds'];require(type(elapsed) in (int,float) and math.isfinite(elapsed) and 0<=elapsed<=900)
        before=value['source_hashes_before'];require(type(before) is dict and bool(before) and before==value['source_hashes_after'])
        require(all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for k,v in before.items()))
        rows=value['results'];require(type(rows) is list and len(rows)==31 and {r['case'] for r in rows}==CASES)
        active={'happy','captured_source_checkpoint','uncaptured_probe_checkpoint','loader_drift','repeat_capture','repeat_activate'}
        captured=active|{'capture_only','profile_checkpoint'}
        for row in rows:
            require(set(row)=={'case','outcome','metrics'} and row['outcome']=='PASS')
            name=row['case'];m=row['metrics'];require(type(m) is dict)
            positive=name in active or name=='capture_only'
            require(m['ok'] is positive and m['refused'] is (not positive) and m['interrupted'] is False)
            require(m['outcome']==('ok' if positive else 'refused'))
            for key in ('sql','credential_opens','credential_body_reads','network_attempts','listener_attempts','capture_payload_exec'):
                require(type(m[key]) is int and m[key]==0)
            for key in ('candidate_pin','modules_clean','finder_clean'): require(m[key] is True)
            require(m['captured'] is (name in captured) and m['activated'] is (name in active))
            if name in captured: require(m['capture_clean'] is True and m['no_host_methods'] is True)
            if name in active:
                require(type(m['payload_exec']) is int and m['payload_exec']>0)
                for key in ('summary_exact','all_false_flags','no_module_export','modules_identity','captured_source_used','marker_absent'):
                    require(m[key] is True)
            else: require(type(m['payload_exec']) is int and m['payload_exec']==0)
            if name not in captured or name=='profile_checkpoint': require(m['refused'] is True)
            for case,key in (('repeat_capture','repeat_capture_refused'),('repeat_activate','repeat_activate_refused'),
                ('loader_drift','drift_blocked'),('uncaptured_probe_checkpoint','probe_blocked')):
                if name==case: require(m[key] is True)
            if name in ('repeat_capture','repeat_activate'): require(m['repeat_no_exec'] is True)
            if name=='uncaptured_probe_checkpoint': require(m['probe_no_exec'] is True)
            if name=='optimized': require(m['optimized'] is True and type(m['profile_reads']) is int and m['profile_reads']==0)
        return True
    except (KeyError,TypeError,ValueError,OverflowError): return False

def expected_paths():
    source=REPO/'ops/backup/custodian_v2';tests=ROOT.parent
    catalogue=source/'source-profile.json';cold=source/'cold/cold_host.py'
    require(hashlib.sha256(catalogue.read_bytes()).hexdigest()=='8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3')
    require(hashlib.sha256(cold.read_bytes()).hexdigest()=='091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c')
    paths={}
    for name,wanted in json.loads(catalogue.read_bytes())['sources'].items():
        file=REPO/name.removeprefix('frozen/') if name.startswith('frozen/') and name!='frozen/ops/backup/erasure_journal_s3.py' else source/name
        require(hashlib.sha256(file.read_bytes()).hexdigest()==wanted);paths[name]=file
    paths.update(catalogue=catalogue,cold=cold,staged=source/'staged/staged_ipc.py',helper=tests/'support.py',
        entry=source/'intake/staged_intake.py',worker=ROOT/'worker.py',gate=ROOT/'run_gate.py',
        scope=tests/'controller/scope_control.py',supervisor=tests/'controller/ci_gate.py',
        wrapper=ROOT/'ci_gate.py',verifier=ROOT/'verify_receipt.py',draft=source/'intake/source-profile.json')
    pins={'entry':'48c238948c9f2c94ed485093b0a583ebcaf7dca760612622e5a01eebd063617c',
        'worker':'c8853243aad6e27a04193af99d611cf1ece92ab56a0e925f9965e38bb5b344b2',
        'staged':'f8010c39d6162dcac44b5e20cef1e79323c0e88a2e8a01eac34e8fe9163dbf02',
        'helper':'d343ebf2d29e4f75180eac08dbf71f2f8f0c6cc7de58c59e1e6fc869148e218d',
        'scope':'d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234',
        'supervisor':'d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b'}
    for name,wanted in pins.items(): require(hashlib.sha256(paths[name].read_bytes()).hexdigest()==wanted)
    tree=ast.parse(cold.read_bytes())
    wheels=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='WHEELS' for t in n.targets))
    require(len(wheels)==7)
    for name,wanted in wheels.items():
        file=tests/'wheels'/name;require(hashlib.sha256(file.read_bytes()).hexdigest()==wanted);paths['wheel:'+name]=file
    require(len(paths)==32);return paths

def verify(file):
    require(sys.platform=='linux' and os.geteuid()==0)
    file=Path(file);require(file.parent.resolve()==ROOT and re.fullmatch('RUN_[0-9]+\\.json',file.name))
    info=file.lstat();require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1
        and stat.S_IMODE(info.st_mode) in (0o444,0o644) and 0<info.st_size<1048576)
    require(not any(name.startswith('system.posix_acl_') for name in os.listxattr(file,follow_symlinks=False)))
    raw=file.read_bytes();value=json.loads(raw);require(valid_summary(value));paths=expected_paths()
    require(set(paths)==set(value['source_hashes_before']))
    for name,path in paths.items(): require(hashlib.sha256(path.read_bytes()).hexdigest()==value['source_hashes_before'][name])
    for directory,pattern in (('/run','pult-controller-scope-*'),('/tmp','pult-cc-*')): require(not list(Path(directory).glob(pattern)))
    units=subprocess.run(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend',
        'pult-controller-ci-run-*','pult-call-gate-*'],capture_output=True,timeout=10)
    require(units.returncode==0 and not units.stdout.strip() and not units.stderr)
    return hashlib.sha256(raw).hexdigest()

def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_intake_verifier_refused"}');return 2
    if len(sys.argv)!=2: return 2
    try: digest=verify(Path(sys.argv[1]))
    except Exception:
        print('{"outcome":"REFUSED","reason":"intake_receipt_verification_failed"}');return 1
    print('INTAKE31_INPUTS32_REHASHED_RESOURCES_EMPTY '+digest);return 0

if __name__=='__main__': raise SystemExit(main())
