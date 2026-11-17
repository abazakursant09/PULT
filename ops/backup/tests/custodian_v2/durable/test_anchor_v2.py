"""Linux-native, socket-free schema2 proof; execution belongs to the root gate."""
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import anchor_v2
import owned_host
from event_codec import Binding, Event
from event_store import EventReader, EventWriter, StoreError
from protocol_v2 import AnchoredEvents, V2Custodian
from crash_worker import (ATTEMPT, BINDING, CODE_ROOT, CRASH_STATES, DATA_ROOT,
    GID, UID, CommitControl, ConnectionProxy, Fixture, HandlerAbort, RAW_CONNECT,
    READER_KEY, WRITER_KEY, event, identity, persist, reference)


class AnchorV2Proof(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='anchor-proof-', dir=DATA_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root, self.serial = Path(self.temporary.name), 0

    def fixture(self, opened=True):
        self.serial += 1
        fixture = Fixture.bootstrap(self.root / ('case-' + str(self.serial)))
        self.addCleanup(fixture.close)
        return fixture.open() if opened else fixture

    def append(self, fixture, item=None, attempt=None):
        result = fixture.runner.append(event() if item is None else item,
                                       attempt_id=identity(40000 + len(fixture.rows()[1])) if attempt is None else attempt)
        self.assertEqual(result.status, 'ANCHORED_STORAGE_ONLY')
        self.assertIs(result.purge_or_restore_admitted, False)
        self.assertFalse(fixture.custodian._active)
        return result

    def refused(self, fixture, action, errors=anchor_v2.AnchorError):
        before = fixture.rows(), fixture.db.read_bytes(), fixture.objects.contents(), list(fixture.objects.calls)
        with self.assertRaises(errors):
            action()
        self.assertEqual((fixture.rows(), fixture.db.read_bytes(), fixture.objects.contents(), fixture.objects.calls), before)

    def pending(self, fixture, revision=2, phase='DISPATCHED', objects=0):
        meta, rows = fixture.rows()
        self.assertEqual(meta, (0, reference().digest, revision, ATTEMPT))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][-1], phase)
        self.assertEqual(len(fixture.objects.contents()), objects)
        self.assertFalse(fixture.custodian._active)

    def test_native_identity_lease_and_cleanup_order(self):
        self.assertEqual((os.getuid(), os.geteuid(), os.getgid(), os.getegid()), (UID, UID, GID, GID))
        fixture = self.fixture()
        fixture.lease.check()
        self.assertIs(type(fixture.lease), owned_host.Lease)
        self.assertEqual(fixture.db.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual([p.stat().st_mode & 0o777 for p in (fixture.db, fixture.lock)], [0o600, 0o600])
        self.assertNotEqual(fixture.objects.directory, fixture.db.parent)
        self.assertTrue(READER_KEY['access_key'] != WRITER_KEY['access_key'])
        order, lease = [], fixture.lease
        def close_reader():
            self.assertIsNotNone(lease.fd)
            order.append('reader')
            reader_close()
        reader_close, lease_close = fixture.reader.close, lease.close
        def close_owner():
            order.append('lease')
            lease_close()
        with patch.object(fixture.reader, 'close', close_reader), patch.object(lease, 'close', close_owner):
            fixture.close()
        self.assertEqual(order, ['reader', 'lease'])
        self.assertIsNone(lease.fd)

    def test_happy_intent_complete_durable_revisions_and_reopen(self):
        fixture = self.fixture()
        first = self.append(fixture, attempt=ATTEMPT)
        self.assertEqual(fixture.rows()[0], (1, first.head.digest, 3, None))
        second = self.append(fixture, event(0, 'complete'))
        self.assertEqual(fixture.rows()[0], (2, second.head.digest, 6, None))
        self.assertEqual([row[-1] for row in fixture.rows()[1]], ['COMMITTED', 'COMMITTED'])
        before, blobs = fixture.rows(), fixture.objects.contents()
        fixture.close()
        fixture.open()
        self.assertEqual(fixture.anchor.load().head, second.head)
        self.assertEqual(fixture.rows(), before)
        self.assertEqual(fixture.objects.contents(), blobs)
        self.assertEqual(fixture.custodian.inspect(ATTEMPT).status, 'OBSERVED_COMMITTED_RECEIPT')

    def test_512_real_events_match_independent_json_hash_reference(self):
        fixture, head = self.fixture(), reference()
        expected = {}
        for index in range(512):
            item = event(index // 2, 'intent' if index % 2 == 0 else 'complete')
            body, head = reference(head, item)
            expected[f'{index + 1:020d}.json'] = body
            self.assertEqual(self.append(fixture, item).head, head)
        meta, rows = fixture.rows()
        self.assertEqual(meta, (512, head.digest, 1536, None))
        self.assertEqual(len(rows), 512)
        self.assertEqual([row[5] for row in rows], list(expected.values()))
        self.assertEqual(fixture.objects.contents(), expected)
        self.assertEqual(fixture.objects.calls, [(method, seq) for seq in range(1, 513) for method in ('PUT', 'GET')])
        from ops.backup.erasure_journal import MAX_EVENTS
        self.assertEqual(MAX_EVENTS, 128)
        fixture.close()
        self.assertEqual(fixture.open().anchor.load().head, head)

    def test_duplicate_attempt_cannot_prepare_a_different_event(self):
        fixture = self.fixture()
        self.append(fixture, attempt=ATTEMPT)
        self.refused(fixture, lambda: fixture.runner.append(event(1), attempt_id=ATTEMPT))

    def test_semantic_duplicates_and_unmatched_completion_refuse_before_put(self):
        for kind in ('intent_again', 'without_intent', 'wrong_subject', 'complete_again'):
            with self.subTest(kind=kind):
                fixture = self.fixture()
                if kind != 'without_intent':
                    self.append(fixture)
                if kind == 'complete_again':
                    self.append(fixture, event(0, 'complete'))
                item = (event() if kind == 'intent_again' else Event(event().purge_id, identity(29999), 'complete')
                        if kind == 'wrong_subject' else event(0, 'complete'))
                self.refused(fixture, lambda: fixture.runner.append(item, attempt_id=identity(49999)))

    def test_both_pending_phases_block_same_and_new_attempt_after_reopen(self):
        for dispatched in (False, True):
            with self.subTest(dispatched=dispatched):
                fixture = self.fixture()
                ticket = fixture.custodian.prepare(event(), attempt_id=ATTEMPT)
                if dispatched:
                    ticket = fixture.custodian.claim(ticket)
                fixture.custodian.abandon(ticket)
                fixture.close()
                fixture.open()
                for attempt in (ATTEMPT, identity(30001)):
                    self.refused(fixture, lambda: fixture.runner.append(event(), attempt_id=attempt))
                self.pending(fixture, 2 if dispatched else 1, 'DISPATCHED' if dispatched else 'PREPARED')

    def test_tickets_cannot_cross_custodian_reuse_or_survive_abandon(self):
        fixture = self.fixture()
        ticket = fixture.custodian.prepare(event(), attempt_id=ATTEMPT)
        foreign = V2Custodian(fixture.anchor, fixture.reader)
        self.refused(fixture, lambda: foreign.claim(ticket))
        claimed = fixture.custodian.claim(ticket)
        self.refused(fixture, lambda: fixture.custodian.claim(ticket))
        self.refused(fixture, lambda: foreign.confirm(claimed))
        fixture.custodian.abandon(ticket)
        self.refused(fixture, lambda: fixture.custodian.confirm(claimed))
        self.pending(fixture)

    def test_confirmation_ticket_consumed_and_not_resurrected_after_restart(self):
        fixture = self.fixture()
        ticket = fixture.custodian.claim(fixture.custodian.prepare(event(), attempt_id=ATTEMPT))
        fixture.writer.create(ticket.prepared.base, ticket.prepared.event)
        self.assertEqual(fixture.custodian.confirm(ticket).revision, 3)
        self.refused(fixture, lambda: fixture.custodian.confirm(ticket))
        fixture.close()
        fixture.open()
        self.refused(fixture, lambda: fixture.custodian.confirm(ticket))

    def test_reader_location_and_binding_must_match(self):
        fixture = self.fixture()
        for override in ({'bucket': 'other-fake-bucket'}, {'binding': Binding('other-scope', BINDING.generation)},
                         {'binding': Binding(BINDING.scope, identity(60000))}):
            with self.subTest(field=next(iter(override))):
                reader = fixture.capability(EventReader, **override)
                try:
                    self.refused(fixture, lambda: V2Custodian(fixture.anchor, reader))
                finally:
                    reader.close()

    def test_writer_location_and_binding_must_match(self):
        fixture = self.fixture()
        for override in ({'bucket': 'other-fake-bucket'}, {'binding': Binding('other-scope', BINDING.generation)}):
            writer = fixture.capability(EventWriter, **override)
            try:
                self.refused(fixture, lambda: AnchoredEvents(fixture.custodian, writer))
            finally:
                writer.close()

    def test_exact_capabilities_and_mandatory_mock_transport(self):
        fixture = self.fixture()
        for action in (lambda: V2Custodian(fixture.anchor, fixture.writer),
                       lambda: V2Custodian(object(), fixture.reader),
                       lambda: AnchoredEvents(fixture.custodian, fixture.reader),
                       lambda: AnchoredEvents(object(), fixture.writer)):
            self.refused(fixture, action)
        for kind in (EventReader, EventWriter):
            with self.assertRaises(StoreError):
                fixture.capability(kind, transport=None)

    def test_existing_exact_owner_required_and_runtime_bootstrap_absent(self):
        fixture = self.fixture()
        for owner in (None, object()):
            self.refused(fixture, lambda: anchor_v2.V2Anchor(fixture.db, binding=BINDING,
                         location=fixture.anchor.location, owner=owner))
        self.refused(fixture, lambda: fixture.anchor.create(reference()))
        fixture.lease.close()
        self.refused(fixture, fixture.anchor.load)

    def test_missing_state_never_created_or_repaired(self):
        fixture = self.fixture(opened=False)
        retained = fixture.db.with_suffix('.retained')
        fixture.db.rename(retained)
        with self.assertRaises((owned_host.OwnershipError, OSError)):
            fixture.open()
        self.assertFalse(fixture.db.exists())
        self.assertTrue(retained.is_file())
        self.assertFalse(fixture.objects.contents())

    def test_v1_schema_and_extra_objects_including_sqliteXevil_refuse(self):
        mutations = ('PRAGMA application_id=1347767380', 'PRAGMA user_version=1',
                     'CREATE TABLE extra(x)', 'CREATE TABLE sqliteXevil(x)',
                     'CREATE INDEX extra_index ON anchor_v2(revision)',
                     'CREATE VIEW extra_view AS SELECT revision FROM anchor_v2')
        for statement in mutations:
            with self.subTest(case=mutations.index(statement)):
                fixture = self.fixture(opened=False)
                fixture.sql(statement)
                self.refused(fixture, lambda: fixture.open().anchor.load())

    def test_metadata_corruption_refuses_without_remote_or_database_changes(self):
        for column, value in (('scope', 'wrong'), ('generation', identity(60001)), ('location', 'wrong'),
                              ('seq', 1), ('hash', 'f' * 64), ('revision', 1), ('pending', ATTEMPT)):
            with self.subTest(column=column):
                fixture = self.fixture(opened=False)
                fixture.sql('UPDATE anchor_v2 SET ' + column + '=?', (value,))
                self.refused(fixture, lambda: fixture.open().anchor.load())

    def test_real_v1_and_v2_databases_mutually_refuse_without_conversion(self):
        from ops.backup.erasure_anchor import SQLiteAnchor
        from ops.backup.erasure_journal import genesis as old_genesis
        import host_core
        fixture=self.fixture()
        before=fixture.db.read_bytes()
        legacy_reader=SQLiteAnchor(fixture.db,scope=BINDING.scope,generation=BINDING.generation,
            location=fixture.anchor.location)
        with self.assertRaises(anchor_v2.AnchorError): legacy_reader.load()
        self.assertEqual(fixture.db.read_bytes(),before)
        fixture.close()
        legacy_path=fixture.db.parent/'legacy.sqlite'
        legacy=SQLiteAnchor(legacy_path,scope=BINDING.scope,generation=BINDING.generation,
            location=fixture.anchor.location)
        original=legacy.create(old_genesis(BINDING.scope))
        before=legacy_path.read_bytes();info=legacy_path.stat()
        spec=dict(fixture.spec,state_file=str(legacy_path),state_identity={'device':info.st_dev,'inode':info.st_ino})
        lease=owned_host.Lease(host_core,spec)
        try:
            candidate=anchor_v2.V2Anchor(legacy_path,binding=BINDING,location=fixture.anchor.location,owner=lease)
            with self.assertRaises(anchor_v2.AnchorError): candidate.load()
            self.assertEqual(legacy_path.read_bytes(),before)
            self.assertEqual(legacy.load(),original)
        finally: lease.close()

    def test_receipt_body_chain_phase_and_gap_corruption_refuse(self):
        for case in ('body', 'base', 'target', 'phase', 'gap'):
            with self.subTest(case=case):
                fixture = self.fixture()
                self.append(fixture)
                self.append(fixture, event(0, 'complete'))
                fixture.close()
                if case == 'phase':
                    connection = RAW_CONNECT(fixture.db)
                    try:
                        connection.execute('PRAGMA ignore_check_constraints=ON')
                        connection.execute("UPDATE attempts_v2 SET phase='BROKEN' WHERE target_seq=1")
                        connection.commit()
                    finally:
                        connection.close()
                else:
                    sql = {'body': "UPDATE attempts_v2 SET body=x'00' WHERE target_seq=1",
                           'base': "UPDATE attempts_v2 SET base_hash='bad' WHERE target_seq=1",
                           'target': 'UPDATE attempts_v2 SET target_seq=9 WHERE target_seq=2',
                           'gap': 'DELETE FROM attempts_v2 WHERE target_seq=1'}[case]
                    fixture.sql(sql)
                self.refused(fixture, lambda: fixture.open().anchor.load())

    def test_persisted_canonical_but_semantically_invalid_history_refuses(self):
        for wrong in ('completion_without_intent', 'duplicate_intent', 'wrong_subject'):
            fixture = self.fixture(opened=False)
            head = reference()
            items = [event(0, 'complete')] if wrong == 'completion_without_intent' else [event(),
                event() if wrong == 'duplicate_intent' else Event(event().purge_id, identity(29999), 'complete')]
            for index, item in enumerate(items):
                body, target = reference(head, item)
                fixture.sql("INSERT INTO attempts_v2 VALUES(?,?,?,?,?,?,'COMMITTED')",
                            (identity(50000 + index), head.sequence, head.digest, target.sequence, target.digest, body))
                head = target
            fixture.sql('UPDATE anchor_v2 SET seq=?,hash=?,revision=?', (head.sequence, head.digest, len(items) * 3))
            self.refused(fixture, lambda: fixture.open().anchor.load())

    def test_1024_limit_configuration_and_small_isolated_boundary_not_full_history_proof(self):
        self.assertEqual(anchor_v2.MAX_RECEIPTS, 1024)
        fixture = self.fixture()
        with patch.object(anchor_v2, 'MAX_RECEIPTS', 2):
            self.append(fixture)
            self.append(fixture, event(0, 'complete'))
            self.refused(fixture, lambda: fixture.runner.append(event(1), attempt_id=ATTEMPT))
        with patch.object(anchor_v2, 'MAX_RECEIPTS', 1):
            self.refused(fixture, fixture.anchor.load)
        self.assertEqual(anchor_v2.MAX_RECEIPTS, 1024)

    def test_two_mib_file_boundary_and_oversize_refusal_before_put(self):
        fixture = self.fixture(opened=False)
        with fixture.db.open('r+b') as file:
            file.truncate(2 * 1024 * 1024)
            file.flush()
            os.fsync(file.fileno())
        self.assertEqual(fixture.open().anchor.load().head, reference())
        fixture.close()
        with fixture.db.open('r+b') as file:
            file.truncate(2 * 1024 * 1024 + 1)
        before = fixture.db.read_bytes(), fixture.objects.contents()
        with self.assertRaises((owned_host.OwnershipError, anchor_v2.AnchorError, OSError)):
            fixture.open()
        self.assertEqual((fixture.db.read_bytes(), fixture.objects.contents()), before)
        self.assertEqual(fixture.objects.calls, [])

    def test_lost_write_response_preserves_object_and_dispatched_pending(self):
        fixture = self.fixture()
        fixture.objects.mode = 'write_unknown'
        result = fixture.runner.append(event(), attempt_id=ATTEMPT)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertIs(result.purge_or_restore_admitted, False)
        self.pending(fixture, objects=1)
        self.assertEqual(fixture.objects.calls, [('PUT', 1)])
        self.assertEqual(next(iter(fixture.objects.contents().values())), reference(reference(), event())[0])

    def test_412_conflict_never_adopts_existing_object(self):
        fixture = self.fixture()
        persist(fixture.objects.directory / f'{1:020d}.json', b'synthetic-existing-immutable-object')
        before = fixture.objects.contents()
        result = fixture.runner.append(event(), attempt_id=ATTEMPT)
        self.assertEqual(result.status, 'CONFLICT')
        self.assertIs(result.purge_or_restore_admitted, False)
        self.pending(fixture, objects=1)
        self.assertEqual(fixture.objects.contents(), before)
        self.assertEqual(fixture.objects.calls, [('PUT', 1)])

    def test_500_is_unknown_without_retry(self):
        fixture = self.fixture()
        fixture.objects.mode = 'write_500'
        result = fixture.runner.append(event(), attempt_id=ATTEMPT)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertIs(result.purge_or_restore_admitted, False)
        self.pending(fixture)
        self.assertEqual(fixture.objects.calls, [('PUT', 1)])

    def test_independent_get_failure_or_body_tamper_cannot_confirm(self):
        for mode in ('read_fail', 'tamper'):
            with self.subTest(mode=mode):
                fixture = self.fixture()
                fixture.objects.mode = mode
                result = fixture.runner.append(event(), attempt_id=ATTEMPT)
                self.assertEqual(result.status, 'UNKNOWN')
                self.assertIs(result.purge_or_restore_admitted, False)
                self.pending(fixture, objects=1)
                self.assertEqual(fixture.objects.calls, [('PUT', 1), ('GET', 1)])

    def test_handler_baseexception_revokes_every_ticket_lineage(self):
        for mode in ('abort_put', 'abort_get'):
            with self.subTest(mode=mode):
                fixture = self.fixture()
                fixture.objects.mode = mode
                with self.assertRaises(HandlerAbort):
                    fixture.runner.append(event(), attempt_id=ATTEMPT)
                self.pending(fixture, objects=1)
                self.refused(fixture, lambda: fixture.runner.append(event(1), attempt_id=identity(30001)))

    def test_inspection_is_read_only_never_ack_reset_purge_or_restore(self):
        fixture = self.fixture()
        absent = fixture.custodian.inspect(ATTEMPT)
        self.assertEqual(absent.status, 'NO_SUCH_RECEIPT')
        ticket = fixture.custodian.prepare(event(), attempt_id=ATTEMPT)
        fixture.custodian.abandon(ticket)
        before, calls = fixture.rows(), list(fixture.objects.calls)
        for attempt in (ATTEMPT, identity(30001)):
            result = fixture.custodian.inspect(attempt)
            self.assertEqual(result.status, 'PENDING_REQUIRES_REVIEW')
            self.assertIs(result.purge_or_restore_admitted, False)
        self.assertIs(absent.purge_or_restore_admitted, False)
        self.assertEqual((fixture.rows(), fixture.objects.calls), (before, calls))
        for target in (fixture.anchor, fixture.custodian, fixture.runner):
            for name in ('reset', 'repair', 'drop_pending', 'purge', 'restore', 'reconcile'):
                self.assertFalse(hasattr(target, name))

    def test_real_begin_immediate_failure_is_visible_and_never_retried(self):
        fixture, begins = self.fixture(), []
        class ObserveBegin(ConnectionProxy):
            def execute(self, sql, parameters=()):
                if sql == 'BEGIN IMMEDIATE':
                    begins.append(True)
                return super().execute(sql, parameters)
        def connect(*args, **kwargs):
            return ObserveBegin(RAW_CONNECT(*args, **kwargs), CommitControl())
        holder = RAW_CONNECT(fixture.db, isolation_level=None, timeout=0)
        try:
            holder.execute('BEGIN IMMEDIATE')
            with patch.object(sqlite3, 'connect', connect):
                self.refused(fixture, lambda: fixture.runner.append(event(), attempt_id=ATTEMPT))
        finally:
            holder.rollback()
            holder.close()
        self.assertEqual(begins, [True])
        self.assertFalse(fixture.custodian._active)

    def test_actual_commit_fault_matrix_observes_persisted_state_without_retry(self):
        for stage in (name for name in CRASH_STATES if 'commit' in name):
            with self.subTest(stage=stage):
                fixture = self.fixture()
                control = CommitControl(fault=stage)
                number = int(stage[-1])
                with control.installed():
                    if number < 3:
                        with self.assertRaises(anchor_v2.AnchorCommitUncertain):
                            fixture.runner.append(event(), attempt_id=ATTEMPT)
                    else:
                        result = fixture.runner.append(event(), attempt_id=ATTEMPT)
                        self.assertEqual(result.status, 'ANCHOR_RESPONSE_UNKNOWN')
                        self.assertIs(result.purge_or_restore_admitted, False)
                self.assertEqual(control.count, number)
                phase, revision, objects = CRASH_STATES[stage]
                self.assertFalse(fixture.custodian._active)
                fixture.close()
                meta, rows = fixture.rows()
                target = reference(reference(), event())[1]
                self.assertEqual(meta, (int(phase == 'COMMITTED'), target.digest if phase == 'COMMITTED' else reference().digest,
                                       revision, ATTEMPT if phase not in (None, 'COMMITTED') else None))
                self.assertEqual([row[-1] for row in rows], [] if phase is None else [phase])
                self.assertEqual(len(fixture.objects.contents()), objects)
                self.assertEqual(fixture.objects.calls.count(('PUT', 1)), objects)

    def test_sigkill_nine_checkpoint_matrix_and_fresh_worker_reopen(self):
        for stage, (phase, revision, objects) in CRASH_STATES.items():
            with self.subTest(stage=stage):
                fixture = self.fixture(opened=False)
                command = [sys.executable, '-I', '-S', '-B', str(CODE_ROOT / 'candidate' / 'crash_worker.py'),
                           str(CODE_ROOT), str(fixture.root)]
                process = subprocess.Popen(command + [stage], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, close_fds=True)
                descriptor = None
                try:
                    descriptor = os.pidfd_open(process.pid, 0)
                    line, deadline = b'', time.monotonic() + 12
                    while b'\n' not in line and len(line) <= 128:
                        remaining = deadline - time.monotonic()
                        self.assertGreater(remaining, 0, 'synthetic checkpoint deadline exceeded')
                        ready, _, _ = select.select([process.stdout], [], [], remaining)
                        self.assertTrue(ready, 'synthetic checkpoint unavailable')
                        part = os.read(process.stdout.fileno(), 128)
                        self.assertTrue(part, 'synthetic worker exited before checkpoint')
                        line += part
                    self.assertEqual(line, ('CHECKPOINT:' + stage + '\n').encode('ascii'))
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    self.assertEqual(process.wait(timeout=5), -signal.SIGKILL)
                finally:
                    if process.poll() is None:
                        if descriptor is not None:
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                        else:
                            process.kill()
                        process.wait(timeout=5)
                    if descriptor is not None:
                        os.close(descriptor)
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        pipe.close()
                meta, rows = fixture.rows()
                body, target = reference(reference(), event())
                self.assertEqual(meta, (int(phase == 'COMMITTED'), target.digest if phase == 'COMMITTED' else reference().digest,
                                       revision, ATTEMPT if phase not in (None, 'COMMITTED') else None))
                self.assertEqual([row[-1] for row in rows], [] if phase is None else [phase])
                expected_blobs = {f'{1:020d}.json': body} if objects else {}
                self.assertEqual(fixture.objects.contents(), expected_blobs)
                database_before = fixture.db.read_bytes()
                reopened = subprocess.run(command + ['reopen-' + stage], capture_output=True, timeout=12, check=False)
                self.assertEqual(reopened.returncode, 0, 'synthetic reopen worker refused')
                self.assertEqual(reopened.stderr, b'')
                report = json.loads(reopened.stdout)
                self.assertIs(report['ok'], True)
                self.assertEqual(report['status'], 'OBSERVED_COMMITTED_RECEIPT' if phase == 'COMMITTED' else
                                 'PENDING_REQUIRES_REVIEW' if phase else 'NO_SUCH_RECEIPT')
                self.assertIs(report['explicit_new_attempt'], phase is None)
                if phase is not None:
                    self.assertEqual(fixture.db.read_bytes(), database_before)
                    self.assertEqual(fixture.objects.contents(), expected_blobs)
                else:
                    self.assertEqual(fixture.rows()[1][0][0], identity(30001))
                    self.assertEqual(fixture.objects.contents(), {f'{1:020d}.json': body})

    def test_close_failure_after_actual_confirmation_commit_is_not_success(self):
        fixture=self.fixture();control=CommitControl();faulted=[]
        class FailClose(ConnectionProxy):
            def close(self):
                self.connection.close()
                if control.count==3 and not faulted:
                    faulted.append(True)
                    raise sqlite3.OperationalError('synthetic postcommit close uncertainty')
        def connect(*args,**kwargs): return FailClose(RAW_CONNECT(*args,**kwargs),control)
        with patch.object(sqlite3,'connect',connect):
            result=fixture.runner.append(event(),attempt_id=ATTEMPT)
        self.assertEqual(result.status,'ANCHOR_RESPONSE_UNKNOWN')
        self.assertIsNone(result.head)
        self.assertIs(result.purge_or_restore_admitted,False)
        self.assertEqual((control.count,len(faulted)),(3,1))
        self.assertEqual(fixture.rows()[0],(1,reference(reference(),event())[1].digest,3,None))
        self.assertEqual(fixture.rows()[1][0][-1],'COMMITTED')
        self.assertFalse(fixture.custodian._active)
        self.assertEqual(fixture.objects.calls,[('PUT',1),('GET',1)])
        self.assertEqual(fixture.custodian.inspect(ATTEMPT).status,'OBSERVED_COMMITTED_RECEIPT')

    def test_owner_loss_or_reader_binding_drift_after_claim_blocks_put(self):
        for mode in ('closed_owner', 'reader_drift'):
            with self.subTest(mode=mode):
                fixture = self.fixture()
                original = fixture.custodian.claim
                def changed_claim(ticket):
                    claimed = original(ticket)
                    if mode == 'closed_owner':
                        fixture.lease.close()
                    else:
                        fixture.reader._binding = Binding('changed-after-claim', BINDING.generation)
                    return claimed
                with patch.object(fixture.custodian, 'claim', changed_claim):
                    result = fixture.runner.append(event(), attempt_id=ATTEMPT)
                self.assertEqual(result.status, 'UNKNOWN')
                self.assertIs(result.purge_or_restore_admitted, False)
                self.pending(fixture)
                self.assertEqual(fixture.objects.calls, [])

    def test_wire_ack_requires_exact_str_not_equality_spoof_or_subclass(self):
        class EqualToken:
            def __eq__(self, other):
                return True
        class TokenSubclass(str):
            pass
        for token in (EqualToken(), TokenSubclass('PUT_HTTP200_UNCONFIRMED'), None):
            fixture = self.fixture()
            with patch.object(fixture.writer, 'create', return_value=token):
                result = fixture.runner.append(event(), attempt_id=ATTEMPT)
            self.assertEqual(result.status, 'UNKNOWN')
            self.assertIs(result.purge_or_restore_admitted, False)
            self.pending(fixture)
            self.assertEqual(fixture.objects.calls, [])
