"""Linux-only bounded failure contracts; not a systemd/kernel completion proof."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2/synthetic_ssh_s3'
sys.path.insert(0,str(HERE))
import test_support
c,r=test_support.root_modules()

class LifecycleTests(unittest.TestCase):
    def fixture(self):
        cfg=dict(sources={'core':dict(path='/opt/artificial/core.py')},controller={'unit':'pult-artificial.service'},
            custodian=dict(unit='pult-call-gate-'+'a'*32+'.service'),writer=dict(unit='pult-call-gate-'+'b'*32+'.service'))
        profile=object.__new__(c.Profile);profile.raw=c.canonical(cfg);profile.sha='a'*64
        profile.controller_guard=mock.Mock()
        calls=[]
        manager=SimpleNamespace(cleanup=lambda owner,unit:calls.append(unit))
        supervisor=SimpleNamespace(load_core=mock.Mock(),_state=mock.Mock(return_value={'LoadState':'loaded'}),
            _empty=mock.Mock(return_value=True),_core=lambda:SimpleNamespace(Manager=manager))
        profile.capture=mock.Mock(return_value=supervisor)
        action=r.Action(profile);action.check=mock.Mock();action.claim=mock.Mock()
        action._role_slots_claimed=True  # This fixture replaces successful claim.
        action.launch=mock.Mock(side_effect=r.Unknown('artificial launch fault'))
        action.close_admission=mock.Mock()
        return action,supervisor,calls,cfg
    def invoke_unknown(self,action):
        r._ATTEMPTED=False
        with self.assertRaises(r.Unknown):action.run()
        self.assertTrue(action.failed);self.assertFalse(action.closed)
        self.assertIn(action,r._FAILED)
    def test_admission_failure_still_attempts_both_role_groups(self):
        action,supervisor,calls,cfg=self.fixture();action.fence=12345
        action.close_admission.side_effect=r.Unknown('artificial gate closure fault')
        self.invoke_unknown(action)
        self.assertEqual(calls,[cfg['writer']['unit'],cfg['custodian']['unit']])
        self.assertEqual(action.fence,12345)
    def test_first_group_cleanup_failure_does_not_skip_second(self):
        action,supervisor,calls,cfg=self.fixture();action.fence=12345
        def cleanup(owner,unit):
            calls.append(unit)
            if unit==cfg['writer']['unit']:raise r.Unknown('artificial stop fault')
        supervisor._core=lambda:SimpleNamespace(Manager=SimpleNamespace(cleanup=cleanup))
        self.invoke_unknown(action)
        self.assertEqual(calls,[cfg['writer']['unit'],cfg['custodian']['unit']])
    def test_unsubmitted_but_prospective_roles_are_checked_after_fence(self):
        action,supervisor,calls,cfg=self.fixture();action.fence=12345
        self.invoke_unknown(action)
        self.assertEqual(action.submitted,[])
        self.assertEqual(calls,[cfg['writer']['unit'],cfg['custodian']['unit']])
    def test_absent_units_require_empty_groups_and_are_not_adopted(self):
        action,supervisor,calls,cfg=self.fixture();action.fence=12345
        supervisor._state.return_value={'LoadState':'not-found'}
        self.invoke_unknown(action);self.assertEqual(calls,[])
        for role in ('writer','custodian'):
            self.assertIn(mock.call(cfg[role]['unit']),supervisor._empty.call_args_list)
    def test_supervisor_capture_failure_retains_fence_and_is_unknown(self):
        action,supervisor,calls,cfg=self.fixture();action.fence=12345
        action.profile.capture.side_effect=r.Unknown('untrusted source failure')
        self.invoke_unknown(action);self.assertEqual(calls,[])
        action.close_admission.assert_called_once();self.assertEqual(action.fence,12345)
    def test_second_attempt_refuses_before_claim(self):
        action,*_=self.fixture();r._ATTEMPTED=True
        with self.assertRaises(c.Refused):action.run()
        action.claim.assert_not_called();action.selector.close()
    def test_no_fence_failure_does_not_touch_unsubmitted_unit(self):
        action,supervisor,calls,cfg=self.fixture()
        action.claim.side_effect=r.Unknown('before permanent fence')
        self.invoke_unknown(action);self.assertEqual(calls,[]);supervisor._state.assert_not_called()
    def test_no_permanent_fence_unlink_in_root_run(self):
        import ast,inspect,textwrap
        node=ast.parse(textwrap.dedent(inspect.getsource(r.Action.run)))
        self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in ('unlink','remove','rmdir') for n in ast.walk(node)))
    def test_existing_role_collision_is_refused_before_fence_and_never_stopped(self):
        for role in ('custodian','writer'):
            action,supervisor,calls,cfg=self.fixture();action._role_slots_claimed=False
            action.claim=r.Action.claim.__get__(action,r.Action)
            supervisor._state.side_effect=lambda unit:dict(LoadState='loaded' if unit==cfg[role]['unit'] else 'not-found')
            with mock.patch.object(os,'open',side_effect=AssertionError('no file before collision refusal')):
                self.invoke_unknown(action)
            self.assertEqual(calls,[]);self.assertIsNone(action.fence)
            self.assertFalse(action._role_slots_claimed);action.launch.assert_not_called()
    def test_absent_but_nonempty_role_group_is_never_adopted(self):
        action,supervisor,calls,cfg=self.fixture();action._role_slots_claimed=False
        action.claim=r.Action.claim.__get__(action,r.Action)
        supervisor._state.return_value={'LoadState':'not-found'};supervisor._empty.return_value=False
        with mock.patch.object(os,'open',side_effect=AssertionError('no file before nonempty refusal')):
            self.invoke_unknown(action)
        self.assertEqual(calls,[]);self.assertIsNone(action.fence);action.launch.assert_not_called()

class ManagerRulesTests(unittest.TestCase):
    def answer(self,value):return mock.patch.object(c.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps(value).encode()))
    def test_typed_manager_rules(self):
        value=dict(type='a(iayu)',data=[[2,[1,2,3,4],32]])
        with self.answer(value):self.assertEqual(c.ip_rules('pult-artificial.service','IPAddressAllow'),{(2,(1,2,3,4),32)})
        with self.answer(dict(type='a(iayu)',data=[])):
            self.assertEqual(c.ip_rules('-.slice','IPAddressAllow'),set())
    def test_manager_rule_type_bounds_and_duplicates_refuse(self):
        values=[{},dict(type='s',data=[]),dict(type='a(iayu)',data=None)]
        for row in ([True,[1,2,3,4],32],[2,[1,2,3,True],32],[2,[1,2,3,256],32],
                    [2,[1,2,3,4],33],[10,[0]*15,0],[2,[0]*4,False]):
            values.append(dict(type='a(iayu)',data=[row]))
        values.append(dict(type='a(iayu)',data=[[2,[0]*4,0]]*2))
        for value in values:
            with self.answer(value),self.assertRaises(c.Refused):c.ip_rules('pult-artificial.service','IPAddressDeny')
    def test_invalid_unit_or_field_never_invokes_bus(self):
        with mock.patch.object(c.subprocess,'run',side_effect=AssertionError('external call')):
            for unit,field in (('pult;id.service','IPAddressAllow'),('other.service','IPAddressAllow'),
                ('pult-test.service','ExecStart'),('user.slice','IPAddressAllow')):
                with self.assertRaises(c.Refused):c.ip_rules(unit,field)
    def test_full_unit_property_check_accepts_kernel_family_order_not_extra_families(self):
        cfg=dict(controller=dict(unit='pult-root.service'),network=dict(peer_ipv4='1.1.1.1'),
            custodian=dict(unit='pult-call-gate-'+'a'*32+'.service',host_profile='/etc/host.json',ipc_directory='/run/ipc'),
            admission=dict(root='/run/admission'))
        profile=object.__new__(c.Profile);profile.raw=c.canonical(cfg)
        own=dict(MainPID='681',User='0',Group='0',Type='exec',ExitType='cgroup',Restart='no',NRestarts='0',
            RuntimeMaxUSec='40s',TimeoutStopUSec='2s',KillMode='control-group',KillSignal='15',FinalKillSignal='9',SendSIGKILL='yes',
            PrivateDevices='yes',PrivateTmp='no',RestrictAddressFamilies='AF_INET AF_UNIX',NoNewPrivileges='yes',PrivateNetwork='no',
            ProtectSystem='strict',ProtectControlGroups='yes',AmbientCapabilities='cap_setuid',RootDirectory='',RootImage='',
            BindPaths='',BindReadOnlyPaths='',TemporaryFileSystem='',CapabilityBoundingSet=' '.join(c.LAUNCH_CAPS),
            InaccessiblePaths=' '.join(c.MASKS),BindsTo='pult-root.service',After='pult-root.service',PartOf='pult-root.service',
            ReadWritePaths='/var/lib/state /run/ipc')
        def rules(unit,field):
            if unit in ('-.slice','system.slice'):return set()
            return {(2,(1,1,1,1),32)} if field=='IPAddressAllow' else {(2,(0,)*4,0),(10,(0,)*16,0)}
        with mock.patch.object(c,'show',return_value=own),mock.patch.object(c,'ip_rules',side_effect=rules), \
             mock.patch.object(c,'root_bytes',return_value=c.canonical(dict(state_file='/var/lib/state/anchor.sqlite'))), \
             mock.patch.object(c,'conditions',return_value=[['ConditionPathExists',False,False,'/run/admission/'+c.FENCE,1]]):
            profile._unit_properties('custodian',681)
            for invalid in ('AF_UNIX','AF_INET','AF_UNIX AF_INET AF_INET6','AF_UNIX AF_UNIX AF_INET','~AF_INET AF_UNIX',''):
                own['RestrictAddressFamilies']=invalid
                with self.subTest(invalid=invalid),self.assertRaises(c.Refused):profile._unit_properties('custodian',681)

if __name__=='__main__':unittest.main()
