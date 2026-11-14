"""Offline synthetic protocol proof. Never import application erasure executors."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup.erasure_journal import (
    MAX_BYTES, MAX_EVENTS, Conflict, Event, Head, Journal, JournalError, Missing,
    Stored, WriteUnknown, canonical, decode, empty, genesis,
)

SCOPE = "synthetic"
PURGE = "73e1c70a-3840-40ec-b5a2-6380343d9cbf"
SUBJECT = "770d659f-c20f-46dc-b3bc-3f6000204bc6"
OTHER = "3dc27e44-3920-47a4-aa51-a63a2bf8ed90"
INTENT = Event(PURGE, SUBJECT, "intent")
COMPLETE = Event(PURGE, SUBJECT, "complete")


class MemoryStore:
    """Fake durability/CAS; not provider proof. restart shares backing dict only."""
    def __init__(self, backing=None):
        self.backing = {} if backing is None else backing
        self.reads = self.writes = 0
        self.fail = None
        self.before_write = None

    def read(self):
        self.reads += 1
        if self.fail == "read":
            raise JournalError("synthetic read failure")
        if "body" not in self.backing:
            raise Missing("missing")
        return Stored(self.backing["body"], self.backing["etag"])

    def write(self, body, *, etag):
        self.writes += 1
        if self.before_write:
            self.before_write()
        if etag != self.backing.get("etag"):
            raise Conflict("conflict")
        if self.fail == "before":
            raise WriteUnknown("synthetic unknown")
        self.backing.update(body=body, etag='"' + hashlib.sha256(body).hexdigest() + '"')
        if self.fail in {"after", "after-read-fails"}:
            if self.fail == "after-read-fails":
                self.fail = "read"
            raise WriteUnknown("synthetic unknown")


def setup():
    store = MemoryStore()
    journal = Journal(store, SCOPE)
    assert journal.initialize(genesis(SCOPE)).status == "STORED_UNANCHORED"
    return journal, store


def test_intent_complete_and_exact_repeat_with_new_process_share_history():
    journal, store = setup()
    first = journal.append(INTENT, genesis(SCOPE))
    assert first.status == "STORED_UNANCHORED" and first.observed.sequence == 1
    second = Journal(MemoryStore(store.backing), SCOPE).append(COMPLETE, first.observed)
    assert second.status == "STORED_UNANCHORED" and second.observed.sequence == 2
    writes = store.writes
    assert journal.append(INTENT, genesis(SCOPE)).status == "ALREADY_STORED_UNANCHORED"
    assert journal.append(COMPLETE, second.observed).status == "ALREADY_STORED_UNANCHORED"
    assert store.writes == writes
    assert SUBJECT not in repr(journal.observe(genesis(SCOPE))[0]) + repr(INTENT) + repr(store.read())


def test_missing_never_initializes_even_with_genesis():
    store = MemoryStore()
    for floor in (genesis(SCOPE), Head(1, "a" * 64)):
        with pytest.raises(Missing):
            Journal(store, SCOPE).append(INTENT, floor)
    assert store.writes == 0


def test_explicit_initialize_cannot_overwrite_or_accept_non_genesis():
    journal, store = setup()
    assert journal.initialize(genesis(SCOPE)).status == "CONFLICT"
    with pytest.raises(JournalError):
        journal.initialize(Head(1, "a" * 64))
    assert decode(store.backing["body"], SCOPE).head == genesis(SCOPE)


@pytest.mark.parametrize("event", [COMPLETE, Event(PURGE, OTHER, "complete")])
def test_completion_without_matching_intent_never_writes(event):
    journal, store = setup()
    with pytest.raises(JournalError):
        journal.append(event, genesis(SCOPE))
    assert store.writes == 1


def test_conflicting_identity_and_stale_head_do_not_write():
    journal, store = setup()
    first = journal.append(INTENT, genesis(SCOPE)).observed
    with pytest.raises(JournalError):
        journal.append(Event(PURGE, OTHER, "intent"), first)
    with pytest.raises(JournalError):
        journal.append(Event(PURGE, OTHER, "complete"), first)
    with pytest.raises(Conflict):
        journal.append(COMPLETE, genesis(SCOPE))
    assert store.writes == 2


def test_cas_two_validated_writers_only_one_wins():
    journal, store = setup()
    rival = Journal(MemoryStore(store.backing), SCOPE)
    store.before_write = lambda: rival.append(Event(OTHER, SUBJECT, "intent"), genesis(SCOPE))
    assert journal.append(INTENT, genesis(SCOPE)).status == "CONFLICT"
    snapshot, _ = rival.observe(genesis(SCOPE))
    assert snapshot.head.sequence == 1 and snapshot.events[0]["purge_id"] == OTHER


@pytest.mark.parametrize("fault", ["before", "after", "after-read-fails"])
def test_unknown_write_single_attempt_single_reconcile_never_promoted(fault):
    journal, store = setup()
    store.fail = fault
    before_reads, before_writes = store.reads, store.writes
    result = journal.append(INTENT, genesis(SCOPE))
    assert result.status == "UNKNOWN"
    assert store.writes == before_writes + 1 and store.reads == before_reads + 2
    assert (result.observed is not None) == (fault == "after")


def test_unknown_initialization_and_committed_write_read_failure():
    store = MemoryStore()
    store.fail = "after"
    assert Journal(store, SCOPE).initialize(genesis(SCOPE)).status == "UNKNOWN"
    assert store.writes == store.reads == 1
    store.fail = None
    journal = Journal(store, SCOPE)
    def fail_after_write():
        store.fail = "read"
    store.before_write = fail_after_write
    assert journal.append(INTENT, genesis(SCOPE)).status == "UNKNOWN"


def test_rollback_equal_and_higher_forks_rejected_against_independent_floor():
    journal, store = setup()
    first = journal.append(INTENT, genesis(SCOPE)).observed
    second = journal.append(COMPLETE, first).observed
    original = deepcopy(store.backing)
    store.backing["body"] = empty(SCOPE).encode()
    with pytest.raises(JournalError):
        journal.observe(first)
    rival, rival_store = setup()
    fork = rival.append(Event(OTHER, SUBJECT, "intent"), genesis(SCOPE)).observed
    store.backing["body"] = rival_store.backing["body"]
    with pytest.raises(JournalError):
        journal.observe(first)
    rival.append(Event(OTHER, SUBJECT, "complete"), fork)
    store.backing["body"] = rival_store.backing["body"]
    with pytest.raises(JournalError):
        journal.observe(first)
    store.backing.update(original)
    assert journal.observe(first)[0].head == second
    # An old floor alone CANNOT establish freshness: deliberately no admission API.
    store.backing["body"] = empty(SCOPE).encode()
    assert journal.observe(genesis(SCOPE))[0].head.sequence == 0


@pytest.mark.parametrize("value", ["", "x/y", "a" * 65, None, 1])
def test_bad_scope(value):
    with pytest.raises(JournalError):
        genesis(value)


@pytest.mark.parametrize("value", ["email@example.test", "1", PURGE.upper(),
                                  "00000000-0000-0000-0000-000000000000", None, 12])
def test_nonopaque_or_noncanonical_uuid(value):
    with pytest.raises(JournalError):
        Event(value, SUBJECT, "intent")


@pytest.mark.parametrize("seq,digest", [(True, "a" * 64), (-1, "a" * 64),
    (MAX_EVENTS + 1, "a" * 64), (1.0, "a" * 64), (1, "A" * 64), (1, None)])
def test_invalid_head(seq, digest):
    with pytest.raises(JournalError):
        Head(seq, digest)


@pytest.mark.parametrize("etag", ["", "*", 'W/"x"', "unquoted", '"x\r\ny"', '"a b"'])
def test_bad_etag(etag):
    with pytest.raises(JournalError):
        Stored(b"", etag)


@pytest.mark.parametrize("body", [b"", b"x" * (MAX_BYTES + 1), b"null\n", b"[]\n",
    b'{"format":"x","format":"y"}\n', b'{"x":NaN}\n', b"\xff", b"[" * 3000,
    empty(SCOPE).encode().replace(b"\n", b"\r\n"), empty(SCOPE).encode().rstrip(),
    canonical({"format": "pult-erasure-journal-v2", "scope": SCOPE, "events": []}),
    canonical({"format": "pult-erasure-journal-v1", "scope": "other", "events": []}),
    canonical({"format": "pult-erasure-journal-v1", "scope": SCOPE, "events": [], "extra": 1})],
    ids=[f"envelope-{index}" for index in range(13)])
def test_malformed_envelope(body):
    with pytest.raises(JournalError):
        decode(body, SCOPE)


@pytest.mark.parametrize("mutation", ["sequence", "previous", "digest", "bool", "float", "extra",
                                    "complete", "duplicate", "unknown_phase", "subject"])
def test_invalid_chain_or_transitions(mutation):
    journal, store = setup()
    journal.append(INTENT, genesis(SCOPE))
    doc = json.loads(store.backing["body"])
    event = doc["events"][0]
    if mutation == "duplicate":
        duplicate = dict(event, sequence=2, previous=event["digest"])
        duplicate["digest"] = hashlib.sha256(canonical({k: v for k, v in duplicate.items() if k != "digest"})).hexdigest()
        doc["events"].append(duplicate)
    else:
        key, value = {"sequence": ("sequence", 2), "previous": ("previous", "a" * 64),
            "digest": ("digest", "b" * 64), "bool": ("sequence", True), "float": ("sequence", 1.0),
            "extra": ("extra", 1), "complete": ("phase", "complete"),
            "unknown_phase": ("phase", "deleted"), "subject": ("subject_id", "seller@example.test")}[mutation]
        event[key] = value
        if mutation != "digest":
            event["digest"] = hashlib.sha256(canonical({k: v for k, v in event.items() if k != "digest"})).hexdigest()
    with pytest.raises(JournalError):
        decode(canonical(doc), SCOPE)


def test_capacity_blocks_without_truncation_or_write():
    journal, store = setup()
    head = genesis(SCOPE)
    for index in range(MAX_EVENTS):
        event = Event(f"{index:08x}-0000-4000-8000-000000000001", SUBJECT, "intent")
        head = journal.append(event, head).observed
    before = deepcopy(store.backing)
    with pytest.raises(JournalError, match="capacity"):
        journal.append(INTENT, head)
    assert store.backing == before
    assert decode(before["body"], SCOPE).head.sequence == MAX_EVENTS
