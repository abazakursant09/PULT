"""Real Linux/process/cgroup integration plus focused controller guards."""
import copy
import fcntl
import json
import os
from pathlib import Path
import signal
import threading
import time
import proof_repeat as p

class RepeatableTests(p.Case):
    def refused(self,row):
        self.assertEqual(row['answer'],p.repeat.REFUSED.decode());self.assertFalse(row['metadata']['launched'])
    def cleaned(self):
        for unit in self.units:
            self.assertTrue(self.rprofile.a._empty(unit));self.assertIn(self.rprofile.a._state(unit)['ActiveState'],('inactive','failed'))
    def observed(self,row,sequence=0):
        value=json.loads(row['answer']);self.assertEqual(value['format'],p.v2.observer.REPLY)
        self.assertEqual(value['observation']['head']['sequence'],sequence)
        self.assertIs(value['observation']['write_admitted'],False);self.assertIs(value['observation']['purge_or_restore_admitted'],False)
        for key in ('cleanup_confirmed','cgroup_empty','pipe_eof_confirmed'): self.assertIs(row['metadata'][key],True)
        self.assertEqual(row['metadata']['supervisor_child_exit'],0)
    def legacy_refused(self):
        old=self.control/p.v2.a.CLAIM_NAME;raw=old.read_bytes();inode=old.stat().st_ino;units=set(self.units)
        reply,_=self.exchange();self.assertEqual(reply['answer'],p.v2.a.REFUSED.decode());self.assertFalse(reply['metadata']['launched'])
        self.assertEqual(self.units,units);self.assertEqual(old.read_bytes(),raw);self.assertEqual(old.stat().st_ino,inode)
    def drain(self,count):
        for index in range(1,count+1): self.assertEqual(self.served()['metrics']['connections'],index)

    def test_three_real_observations_same_controller_head129_and_both_restart_paths_block(self):
        self.seed_head(129);self.start_owner();replies,records=self.loop(calls=3)
        self.assertEqual([r['state'] for r in replies],['READY']*3);self.assertEqual([r['completed'] for r in replies],[1,2,3])
        for row in replies: self.observed(row,129)
        self.assertEqual(len(self.units),3);self.assertEqual(len({r['metadata']['unit'] for r in replies}),3);self.drain(3)
        self.cleaned();self.fresh_refused();self.legacy_refused()
        self.assertEqual(len(list((self.rcontrol/'operations').iterdir())),3)
        self.assertEqual(self.probe(),dict(ok=False,sql=0,key=0));self.stop_owner();self.assertEqual(self.probe()['sequence'],129);self.unchanged()

    def test_unknown_after_one_completion_poison_same_and_new_controller(self):
        self.start_owner();rows,_=self.loop(calls=3,fail_call=2)
        self.observed(rows[0]);self.assertEqual(rows[1]['answer'],p.repeat.UNKNOWN.decode());self.refused(rows[2])
        self.assertEqual([r['completed'] for r in rows],[1,1,1]);self.assertEqual(rows[2]['state'],'BLOCKED')
        self.assertFalse((self.rcontrol/'operations/000002/complete.json').exists());self.assertEqual(len(self.units),2)
        self.drain(2);self.cleaned();self.fresh_refused();self.legacy_refused();self.stop_owner()

    def test_real_pending_no_adoption(self):
        self.seed_pending();self.start_owner();rows,_=self.loop(calls=2)
        self.assertEqual(rows[0]['answer'],p.repeat.UNKNOWN.decode());self.refused(rows[1]);self.assertEqual(rows[0]['completed'],0)
        self.served(False);self.cleaned();self.fresh_refused();self.legacy_refused();self.stop_owner();self.assertEqual(self.probe()['revision'],2)

    def test_invalid_caller_leaves_no_fence_valid_fresh_request_can_run(self):
        self.start_owner();rows,_=self.loop(uid=48002);self.refused(rows[0]);self.assertFalse(self.units)
        self.assertFalse((self.rcontrol/'session.claim').exists());self.assertFalse((self.control/p.v2.a.CLAIM_NAME).exists())
        rows,_=self.loop();self.observed(rows[0]);self.drain(1);self.stop_owner()

    def test_mutated_request_has_no_side_effect(self):
        self.start_owner();body=json.loads(self.request);body['op']='append';rows,_=self.loop(body=p.repeat.canonical(body))
        self.refused(rows[0]);self.assertFalse(self.units);self.assertFalse((self.rcontrol/'session.claim').exists())
        self.assertEqual(self.stop_owner()['metrics']['connections'],0)

    def test_preexisting_one_shot_claim_not_adopted_or_replaced(self):
        self.start_owner();reply,_=self.exchange();self.served();self.assertEqual(json.loads(reply['answer'])['observation']['status'],'OBSERVED_LOCAL_ONLY')
        marker=self.control/p.v2.a.CLAIM_NAME;raw=marker.read_bytes();inode=marker.stat().st_ino;units=set(self.units)
        rows,_=self.loop();self.refused(rows[0]);self.assertEqual(self.units,units)
        self.assertEqual(marker.read_bytes(),raw);self.assertEqual(marker.stat().st_ino,inode)
        self.assertFalse((self.rcontrol/'session.claim').exists());self.stop_owner()

    def test_base_lock_live_other_controller_refuses_without_fence_creation(self):
        self.start_owner();fd=os.open(self.control/p.v2.a.LOCK_NAME,os.O_RDWR|os.O_CLOEXEC)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);rows,_=self.loop();self.refused(rows[0]);self.assertFalse(self.units)
            self.assertFalse((self.rcontrol/'session.claim').exists())
        finally: os.close(fd)
        self.assertEqual(self.stop_owner()['metrics']['connections'],0)

    def test_old_v2_concurrent_call_excluded_before_compatibility_fence(self):
        self.start_owner();checked=[]
        def inspect(case,event,caller,controller):
            if event['event']=='PHASE' and event['phase']=='SESSION_FENCE_OPENED':
                reply,_=self.exchange();self.assertEqual(reply['answer'],p.v2.a.REFUSED.decode());self.assertFalse(reply['metadata']['launched']);checked.append(True)
        rows,_=self.loop(on_event=inspect);self.observed(rows[0]);self.assertEqual(checked,[True]);self.drain(1);self.stop_owner()

    def test_completion_acknowledgement_failure_never_rearms(self):
        self.start_owner();rows,_=self.loop(calls=2,phase='COMPLETION_FILE_FSYNCED',action='raise')
        self.assertEqual(rows[0]['answer'],p.repeat.UNKNOWN.decode());self.refused(rows[1]);self.assertEqual(rows[1]['completed'],0)
        self.assertTrue((self.rcontrol/'operations/000001/complete.json').exists())
        self.drain(1);self.cleaned();self.fresh_refused();self.legacy_refused();self.stop_owner()

    def test_delivery_keyboardinterrupt_blocks_same_live_controller(self):
        self.start_owner();rows,_=self.loop(calls=2,delivery_cancel=True)
        self.assertTrue(rows[0]['metadata']['interrupted_during_delivery']);self.assertEqual(rows[0]['state'],'BLOCKED')
        self.refused(rows[1]);self.assertEqual(rows[1]['completed'],1);self.assertEqual(len(self.units),1)
        self.drain(1);self.cleaned();self.fresh_refused();self.legacy_refused();self.stop_owner()

    def test_same_thread_reentry_guard(self):
        self.start_owner();rows,_=self.loop(calls=2,preheld_mutex=True)
        self.refused(rows[0]);self.assertEqual(rows[0]['state'],'NEW');self.observed(rows[1]);self.assertEqual(len(self.units),1)
        self.drain(1);self.stop_owner()

    def test_fork_inherited_capability_refused(self):
        self.start_owner();session=p.repeat.Session(self.rprofile);pid=os.fork()
        if pid==0:
            try: session.same()
            except p.repeat.Refused: os._exit(0)
            except BaseException: os._exit(2)
            os._exit(1)
        self.register_fork(pid);self.assertEqual(self.reap(pid),0);session.close();self.assertFalse(self.units);self.stop_owner()

    def test_thread_identity_not_just_recycled_native_tid(self):
        self.start_owner();session=p.repeat.Session(self.rprofile);result=[];original_tid=session.tid
        def other():
            session.tid=threading.get_native_id()
            try: session.same()
            except p.repeat.Refused: result.append('REFUSED')
        thread=threading.Thread(target=other);thread.start();thread.join(timeout=5);self.assertFalse(thread.is_alive())
        session.tid=original_tid;self.assertEqual(result,['REFUSED']);session.close();self.stop_owner()

    def test_byte_identical_completed_inode_replacement_poison(self):
        self.start_owner();saved=self.root/'saved-complete';changed=[]
        def inspect(case,event,caller,controller):
            if event['event']=='RESULT' and event['call']==1:
                file=self.rcontrol/'operations/000001/complete.json';file.rename(saved);file.write_bytes(saved.read_bytes());file.chmod(0o600);changed.append(True)
        try:
            rows,_=self.loop(calls=2,on_event=inspect);self.observed(rows[0]);self.refused(rows[1]);self.assertEqual(changed,[True]);self.assertEqual(len(self.units),1)
        finally:
            file=self.rcontrol/'operations/000001/complete.json'
            if saved.exists(): file.unlink();saved.rename(file)
        self.drain(1);self.fresh_refused();self.stop_owner()

    def test_session_marker_inode_replacement_poison(self):
        self.start_owner();saved=self.root/'saved-session-marker'
        def inspect(case,event,caller,controller):
            if event['event']=='RESULT' and event['call']==1:
                file=self.rcontrol/'session.claim';file.rename(saved);file.write_bytes(saved.read_bytes());file.chmod(0o600)
        try:
            rows,_=self.loop(calls=2,on_event=inspect);self.observed(rows[0])
            self.assertEqual(rows[1]['answer'],p.repeat.UNKNOWN.decode());self.assertFalse(rows[1]['metadata']['launched']);self.assertEqual(len(self.units),1)
        finally:
            file=self.rcontrol/'session.claim'
            if saved.exists(): file.unlink();saved.rename(file)
        self.drain(1);self.fresh_refused();self.stop_owner()

    def test_profile_memory_drift_refused_prelaunch(self):
        self.start_owner();original=copy.deepcopy(self.rprofile.spec);self.rprofile.spec['epoch']=str(p.uuid4())
        try:
            rows,_=self.loop();self.refused(rows[0]);self.assertFalse(self.units);self.assertFalse((self.rcontrol/'session.claim').exists())
        finally: self.rprofile.spec=original
        self.stop_owner()

    def test_versioned_profiles_mutually_refused(self):
        self.start_owner()
        with self.assertRaises(p.v2.observer.Refused): p.v2.observer.prepare(self.rmanifest,p.v2.old.sha(self.rmanifest))
        with self.assertRaises(p.repeat.Refused): p.repeat.prepare(self.manifest,p.v2.old.sha(self.manifest))
        self.assertEqual(p.repeat.MAX_CALLS,64);self.assertFalse(self.units);self.stop_owner()

    def test_controller_death_running_descendant_pid1_runtime_stop(self):
        self.start_owner(helper_mode='descendant');seen=[];started=time.monotonic()
        def inspect(case,event,caller,controller):
            if event['event']=='UNIT_READY':
                self.served();file=Path('/sys/fs/cgroup/system.slice')/event['unit']/'cgroup.procs';limit=time.monotonic()+2
                while time.monotonic()<limit:
                    if len(file.read_text().split())>=2: break
                    time.sleep(.02)
                self.assertGreaterEqual(len(file.read_text().split()),2);seen.append(event['unit']);return 'crash'
        rows,_=self.loop(runtime=8,on_event=inspect);self.assertFalse(rows);self.assertEqual(len(seen),1)
        deadline=started+23
        while time.monotonic()<deadline:
            state=self.rprofile.a._state(seen[0])
            if (state.get('Result')=='timeout' and state.get('ActiveState') in ('inactive','failed')
                and self.rprofile.a._empty(seen[0])): break
            time.sleep(.05)
        self.assertEqual(state['Result'],'timeout');self.assertEqual(state['TimeoutStopUSec'],'2s')
        self.assertTrue(self.rprofile.a._empty(seen[0]));self.assertIn(state['ActiveState'],('inactive','failed'))
        poller=p.select.poll();poller.register(self.pins['cli:'+seen[0]],p.select.POLLIN)
        self.assertTrue(poller.poll(3000),'CLI_EXIT_MUST_BE_CONFIRMED_BEFORE_TEARDOWN')
        self.fresh_refused();self.legacy_refused();self.assertEqual(self.probe(),dict(ok=False,sql=0,key=0));self.stop_owner()

    def test_compatibility_inode_close_window_refuses_before_launch(self):
        self.start_owner();saved=self.root/'saved-compat-close-window';changed=[]
        def inspect(case,event,caller,controller):
            if event['event']=='PHASE' and event['phase']=='BASE_COMPAT_FENCE_CLOSED':
                file=self.control/p.v2.a.CLAIM_NAME;file.rename(saved);file.write_bytes(saved.read_bytes());file.chmod(0o600);changed.append(True)
        try:
            rows,_=self.loop(calls=2,on_event=inspect);self.refused(rows[0]);self.refused(rows[1]);self.assertFalse(self.units)
            self.assertEqual(changed,[True]);self.assertEqual(rows[1]['state'],'BLOCKED')
        finally:
            file=self.control/p.v2.a.CLAIM_NAME
            if saved.exists(): file.unlink();saved.rename(file)
        self.fresh_refused();self.legacy_refused();self.assertEqual(self.stop_owner()['metrics']['connections'],0)

    def test_completion_inode_pre_ready_window_unknown_not_rebaselined(self):
        self.start_owner();saved=self.root/'saved-pre-ready-complete';changed=[]
        def inspect(case,event,caller,controller):
            if event['event']=='PHASE' and event['phase']=='BEFORE_MEMORY_READY':
                file=self.rcontrol/'operations/000001/complete.json';file.rename(saved);file.write_bytes(saved.read_bytes());file.chmod(0o600);changed.append(True)
        try:
            rows,_=self.loop(calls=2,on_event=inspect);self.assertEqual(rows[0]['answer'],p.repeat.UNKNOWN.decode());self.refused(rows[1])
            self.assertEqual(changed,[True]);self.assertEqual(len(self.units),1)
        finally:
            file=self.rcontrol/'operations/000001/complete.json'
            if saved.exists(): file.unlink();saved.rename(file)
        self.drain(1);self.cleaned();self.fresh_refused();self.legacy_refused();self.stop_owner()

    def test_real_default40_stop2_stays_fenced(self):
        self.start_owner(helper_mode='hang');rows,records=self.loop(calls=2,runtime=40,on_event=lambda case,event,*args:self.served() if event['event']=='UNIT_READY' else None)
        self.assertEqual(rows[0]['answer'],p.repeat.UNKNOWN.decode());self.refused(rows[1])
        state=rows[0]['metadata']['manager'];self.assertEqual(state['Result'],'timeout');self.assertEqual(state['RuntimeMaxUSec'],'40s');self.assertEqual(state['TimeoutStopUSec'],'2s')
        self.cleaned();self.fresh_refused();self.legacy_refused();self.stop_owner()

    def test_uncertain_cold_close_still_holds_lease_after_controller_closes(self):
        self.start_owner('uncertain_close');rows,_=self.loop(calls=2)
        for row in rows: self.observed(row)
        self.drain(2);self.stop_owner(True);self.assertEqual(self.probe(),dict(ok=False,sql=0,key=0))
        self.fresh_refused();self.kill_owner();self.assertTrue(self.probe()['ok']);self.unchanged()

CRASH_PHASES=(
 'SESSION_FENCE_OPENED','SESSION_FENCE_WRITTEN','SESSION_FENCE_FILE_FSYNCED','SESSION_FENCE_DIR_FSYNCED','SESSION_FENCE_CLOSED',
 'BASE_COMPAT_FENCE_OPENED','BASE_COMPAT_FENCE_WRITTEN','BASE_COMPAT_FENCE_FILE_FSYNCED','BASE_COMPAT_FENCE_DIR_FSYNCED','BASE_COMPAT_FENCE_CLOSED',
 'OPERATION_DIRECTORY_CREATED','OPERATION_DIRECTORY_FSYNCED','OP_CLAIM_OPENED','OP_CLAIM_WRITTEN','OP_CLAIM_BEFORE_FILE_FSYNC',
 'OP_CLAIM_FILE_FSYNCED','OP_CLAIM_DIRECTORY_FSYNCED','OPERATION_CLAIM_DURABLE','MANAGER_RETURNED','OPERATION_FINISH_SYNCED',
 'FINISH_BEFORE_FILE_FSYNC','FINISH_FILE_FSYNCED','FINISH_DIRECTORY_FSYNCED',
 'COMPLETION_OPENED','COMPLETION_WRITTEN','COMPLETION_FILE_FSYNCED','COMPLETION_DIR_FSYNCED','COMPLETION_CLOSED','BEFORE_MEMORY_READY','AFTER_MEMORY_READY')

def crash_case(phase):
    def run(self):
        self.start_owner();rows,records=self.loop(phase=phase);self.assertFalse(rows)
        self.assertIn(phase,[row['phase'] for row in records if row['event']=='PHASE'])
        self.cleaned();self.fresh_refused()
        # Compatibility fence precedes all helper work; early pre-launch crashes
        # may occur before it exists, but no helper has been admitted then.
        if (self.control/p.v2.a.CLAIM_NAME).exists(): self.legacy_refused()
        else: self.assertFalse(self.units)
        served=sum(row['event']=='REGISTER_UNIT' for row in records)
        if served: self.drain(served)
        self.assertEqual(self.probe(),dict(ok=False,sql=0,key=0));self.stop_owner();self.unchanged()
    return run

for phase in CRASH_PHASES:
    setattr(RepeatableTests,'test_crash_'+phase.lower(),crash_case(phase))
