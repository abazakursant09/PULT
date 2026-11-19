"""Portable source/workflow contracts; mocked protocol is tested separately."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock
from copy import deepcopy

REPO=Path(__file__).resolve().parents[2]
RUNTIME=REPO/'ops/backup/custodian_v2/action'
TESTS=REPO/'ops/backup/tests/custodian_v2/action'
CATALOGUE=RUNTIME/'source-profile.json'
WORKFLOW=REPO/'.github/workflows/custodian_authenticated_action.yml'

def pure_verifier():
    # Only portable require_trace/require_scope are called. This shim refuses
    # any accidental OS lock operation; it is NOT a native/runtime bypass.
    shim=ModuleType('fcntl');shim.LOCK_EX=2;shim.LOCK_NB=4
    shim.flock=mock.Mock(side_effect=AssertionError('real OS work not in portable proof'))
    spec=importlib.util.spec_from_file_location('pure_action_receipt_contract',TESTS/'verify_receipt.py')
    module=importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules,{'fcntl':shim}):spec.loader.exec_module(module)
    return module

def scope_receipt(token):
    scope=dict(id=token,work='/tmp/pult-cc-'+token[:16]);rows=[]
    modes=('happy','write_unknown','read_fail','kill_controller_after_put','freeze_roles_at_put')
    for n,mode in enumerate(modes):
        root=scope['work']+'/a-abcdefgh/case-'+format(n+1,'032x')
        units=['pult-call-gate-'+format(n*3+i+1,'032x')+'.service' for i in range(3)]
        negative=mode=='kill_controller_after_put'
        rows.append(dict(case=mode,evidence=dict(root=root,units=units),independent_evidence=dict(
            negative_condition=negative,kernel_unit=units[0 if negative else 2],
            kernel_fence=root+('/control/' if negative else '/admission/')+'constructor-stop.claim')))
    return dict(scope_id=token,scope_work=scope['work'],cases=rows),scope

class ActionGuard(unittest.TestCase):
    def test_catalogue_text_inputs_have_cross_platform_lf(self):
        text=(REPO/'.gitattributes').read_text()
        for relative in ('.github/workflows/custodian_authenticated_action.yml',
            'backend/tests/test_custodian_action_ci_guard.py','backend/tests/test_custodian_action_protocol.py'):
            self.assertIn(relative+' text eol=lf',text)

    def test_read_failure_must_reach_independent_get(self):
        verifier=pure_verifier();put=dict(method='PUT',sequence=1,uid=48002);get=dict(method='GET',sequence=1,uid=47003)
        verifier.require_trace(dict(case='read_fail',calls=[put,get]))
        for wrong in ([],[put],[get],[get,put],[put,put],[put,get,get]):
            with self.subTest(trace=wrong),self.assertRaises(RuntimeError):
                verifier.require_trace(dict(case='read_fail',calls=wrong))

    def test_all_case_traces_have_exact_uid_sequence_and_count(self):
        verifier=pure_verifier()
        for mode in verifier.MODES:
            calls=[dict(method='PUT',sequence=1,uid=48002)]
            if mode in ('happy','read_fail'):calls.append(dict(method='GET',sequence=1,uid=47003))
            verifier.require_trace(dict(case=mode,calls=calls))
            for field,value in (('uid',47003),('uid',True),('sequence',2),('sequence',True),('method','GET')):
                bad=deepcopy(calls);bad[0][field]=value
                with self.subTest(mode=mode,field=field,value=value),self.assertRaises(RuntimeError):
                    verifier.require_trace(dict(case=mode,calls=bad))

    def test_previous_receipt_rejected_by_fresh_scope(self):
        verifier=pure_verifier();old,first=scope_receipt('a'*32);_,fresh=scope_receipt('b'*32)
        verifier.require_scope(old,first)
        with self.assertRaises(RuntimeError):verifier.require_scope(old,fresh)
        relabelled=deepcopy(old);relabelled['scope_id']=fresh['id']
        with self.assertRaises(RuntimeError):verifier.require_scope(relabelled,fresh)
        relabelled['scope_work']=fresh['work']
        with self.assertRaises(RuntimeError):verifier.require_scope(relabelled,fresh)
        for row in relabelled['cases']:row['evidence']['root']=row['evidence']['root'].replace(first['work'],fresh['work'])
        with self.assertRaises(RuntimeError):verifier.require_scope(relabelled,fresh)

    def test_scope_roots_and_journal_path_are_not_relabelled(self):
        verifier=pure_verifier();good,scope=scope_receipt('a'*32)
        for field,value in (('root','/tmp/other/a-abcdefgh/case-'+'1'*32),('root',scope['work']+'/a-abcdefgh/../case-'+'1'*32)):
            bad=deepcopy(good);bad['cases'][0]['evidence'][field]=value
            with self.assertRaises(RuntimeError):verifier.require_scope(bad,scope)
        bad=deepcopy(good);bad['cases'][0]['independent_evidence']['kernel_unit']=good['cases'][1]['evidence']['units'][2]
        with self.assertRaises(RuntimeError):verifier.require_scope(bad,scope)

    def test_draft_is_not_deployment_or_operator_approval(self):
        value=json.loads(CATALOGUE.read_bytes())
        self.assertEqual(set(value),{'format','status','admitted_operations','working_credentials_included','sources'})
        self.assertEqual(value['format'],'pult-synthetic-action-ci-catalogue-v3')
        self.assertEqual(value['status'],'DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED')
        self.assertEqual(value['admitted_operations'],[])
        self.assertIs(value['working_credentials_included'],False)

    def test_exact_current_source_closure(self):
        values=json.loads(CATALOGUE.read_bytes())['sources']
        required={str(p.relative_to(REPO)).replace('\\','/') for p in [
            *RUNTIME.glob('*.py'),*TESTS.glob('*.py'),WORKFLOW,
            REPO/'backend/tests/test_custodian_action_protocol.py',Path(__file__)]}
        self.assertTrue(required.issubset(values))
        self.assertEqual(len(required),13)
        for relative,wanted in values.items():
            file=REPO/relative
            self.assertFalse(Path(relative).is_absolute())
            self.assertNotIn('..',Path(relative).parts)
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),wanted,relative)

    def test_fixed_predecessors_not_silently_upgraded(self):
        values=json.loads(CATALOGUE.read_bytes())['sources']
        for relative,wanted in {
            'ops/backup/custodian_v2/host/staged_host.py':'faf627fef860d560d01605e3717c9939fe81c2c4156b231b03e94c57320c2bae',
            'ops/backup/custodian_v2/stop/constructor_stop.py':'5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c',
            'ops/backup/custodian_v2/controller/supervisor_v2.py':'3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e',
            'ops/backup/custodian_v2/controller/frozen/admission.py':'961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7',
            'ops/backup/tests/custodian_v2/controller/scope_control.py':'d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234',
            'ops/backup/tests/custodian_v2/controller/ci_gate.py':'d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b',
        }.items():self.assertEqual(values[relative],wanted)

    def test_runtime_compiles_without_execution(self):
        for file in [*RUNTIME.glob('*.py'),*TESTS.glob('*.py')]:
            compile(file.read_bytes(),str(file),'exec',dont_inherit=True,optimize=0)

    def test_permanent_fence_precedes_both_launches_and_gate_closes_first(self):
        tree=ast.parse((RUNTIME/'action_root.py').read_bytes())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Action')
        run=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        text=ast.get_source_segment((RUNTIME/'action_root.py').read_text(),run)
        self.assertLess(text.index('self.claim()'),text.index("self.launch('custodian')"))
        self.assertLess(text.index('self.close_admission()'),text.index('Manager.cleanup'))
        self.assertIn('COMMITTED_STORAGE_ONLY_AND_STOPPED',text)
        self.assertNotIn('.unlink(',text)

    def test_atomic_publication_has_no_link_count_window_or_overwrite(self):
        text=(RUNTIME/'action_root.py').read_text()
        self.assertIn('library.renameat2',text)
        self.assertIn('os.fsencode(final),1)',text)
        self.assertNotIn('os.replace(',text)
        self.assertNotIn('os.link(',text)

    def test_no_dbus_delegation_to_numeric_actors(self):
        text=(RUNTIME/'action_profile.py').read_text();tree=ast.parse(text)
        actor=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='actor_guard')
        self.assertNotIn('show(',ast.get_source_segment(text,actor))
        self.assertIn('root/kernel attestation for this exact actor required',text)
        self.assertIn("('CapEff', 'CapPrm', 'CapInh', 'CapAmb', 'CapBnd')",text)

    def test_helper_is_fixed_setpriv_then_isolated_python(self):
        text=(RUNTIME/'action_root.py').read_text()
        for item in ('--clear-groups','--bounding-set=-all','--inh-caps=-all','--ambient-caps=-all','--no-new-privs',"'/usr/bin/python3.12','-I','-S','-B'"):
            self.assertIn(item,text)
        self.assertIn("AmbientCapabilities='CAP_SETUID'",text)
        for item in ("PrivateDevices='yes'","RestrictAddressFamilies='AF_UNIX'","ProtectSystem='strict'","Restart='no'"):
            self.assertIn(item,text)

    def test_five_native_cases_and_independent_read_only_postcheck(self):
        native=(TESTS/'native_chain.py').read_text();verify=(TESTS/'verify_receipt.py').read_text()
        for mode in ('happy','write_unknown','read_fail','kill_controller_after_put','freeze_roles_at_put'):
            self.assertIn(mode,native);self.assertIn(mode,verify)
        self.assertIn('verifier.observe(row)',native)
        self.assertIn("'?mode=ro'",verify)
        self.assertIn('kernel_record(',verify)
        self.assertNotIn('DELETE FROM',verify)

    def test_ci_supervisor_is_pinned_not_reimplemented(self):
        text=(TESTS/'ci_gate.py').read_text()
        self.assertIn('supervisor.create_scope(control)',text)
        self.assertIn('supervisor.supervised_run(',text)
        self.assertIn('verifier.verify(',text)
        self.assertIn('d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b',text)

    def test_workflow_keeps_immutable_actions_read_only_and_real_proofs(self):
        text=WORKFLOW.read_text()
        for item in ('contents: read','persist-credentials: false','ubuntu-24.04','timeout-minutes: 12',
            'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
            'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
            '--require-hashes','--only-binary=:all: --no-deps',
            'unshare --net /usr/bin/python3.12 -I -S -B', 'test_atomic_attestation.py', 'test_custodian_action_ci_guard.py'):
            self.assertIn(item,text)
        for forbidden in ('secrets.','write-all','continue-on-error','pull_request_target','curl ','ssh ','docker push','deploy:'):
            self.assertNotIn(forbidden,text)

if __name__=='__main__':unittest.main()
