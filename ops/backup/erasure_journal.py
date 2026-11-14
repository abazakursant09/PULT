"""Bounded external erasure journal. NOT a purge executor or restore-admission gate.

Callers supply an independently trusted head, never a pin from the restored DB.
There is deliberately no automatic initialization, retry, compaction or export of
an empty restore checkpoint. Transport/IAM provisioning is a separate operation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Protocol
from uuid import UUID

MAX_BYTES = 65536
MAX_EVENTS = 128
FORMAT = "pult-erasure-journal-v1"
_HEX = re.compile(r"[0-9a-f]{64}")
_SCOPE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ETAG = re.compile(r'"[\x21\x23-\x7e]{1,126}"')


class JournalError(Exception):
    """Fixed, secret-free failure; no raw payload/transport errors escape."""


class Missing(JournalError):
    pass


class Conflict(JournalError):
    pass


class WriteUnknown(JournalError):
    """A write may have persisted. Never automatically retry it."""


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _scope(value):
    if not isinstance(value, str) or not _SCOPE.fullmatch(value):
        raise JournalError("invalid scope")
    return value


def _uuid(value):
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    # UUIDv4 only: no email, sequential DB IDs, timestamps or host identifiers.
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        raise JournalError("invalid opaque identifier")
    return value


@dataclass(frozen=True)
class Head:
    sequence: int
    digest: str

    def __post_init__(self):
        if (type(self.sequence) is not int or not 0 <= self.sequence <= MAX_EVENTS
                or not isinstance(self.digest, str) or not _HEX.fullmatch(self.digest)):
            raise JournalError("invalid trusted head")


def genesis(scope: str) -> Head:
    return Head(0, _digest({"format": FORMAT, "scope": _scope(scope)}))


@dataclass(frozen=True)
class Event:
    purge_id: str = field(repr=False)
    subject_id: str = field(repr=False)
    phase: str

    def __post_init__(self):
        _uuid(self.purge_id)
        _uuid(self.subject_id)
        if self.phase not in ("intent", "complete"):
            raise JournalError("invalid event phase")


@dataclass(frozen=True)
class Snapshot:
    scope: str
    events: tuple = field(repr=False)
    heads: tuple[Head, ...] = field(repr=False)

    @property
    def head(self):
        return self.heads[-1]

    def require_floor(self, floor: Head):
        if not isinstance(floor, Head) or floor.sequence > self.head.sequence:
            raise JournalError("journal rollback or missing history")
        if self.heads[floor.sequence] != floor:
            raise JournalError("journal fork or wrong trusted head")

    def encode(self):
        return canonical({"format": FORMAT, "scope": self.scope, "events": list(self.events)})


def empty(scope: str) -> Snapshot:
    return Snapshot(scope, (), (genesis(scope),))


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JournalError("duplicate journal field")
        result[key] = value
    return result


def decode(body: bytes, scope: str) -> Snapshot:
    _scope(scope)
    if type(body) is not bytes or not 0 < len(body) <= MAX_BYTES:
        raise JournalError("invalid journal size")
    try:
        doc = json.loads(body, object_pairs_hook=_unique_pairs)
        if canonical(doc) != body:
            raise JournalError("noncanonical journal")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise JournalError("malformed journal") from None
    if (type(doc) is not dict or set(doc) != {"format", "scope", "events"}
            or doc["format"] != FORMAT or doc["scope"] != scope
            or type(doc["events"]) is not list or len(doc["events"]) > MAX_EVENTS):
        raise JournalError("unsupported journal envelope")
    heads = [genesis(scope)]
    phases = {}
    for index, item in enumerate(doc["events"], 1):
        if type(item) is not dict or set(item) != {
                "sequence", "previous", "digest", "purge_id", "subject_id", "phase"}:
            raise JournalError("unsupported journal record")
        event = Event(item["purge_id"], item["subject_id"], item["phase"])
        unsigned = {key: value for key, value in item.items() if key != "digest"}
        if (type(item["sequence"]) is not int or item["sequence"] != index
                or item["previous"] != heads[-1].digest or item["digest"] != _digest(unsigned)):
            raise JournalError("broken journal chain")
        old = phases.get(event.purge_id)
        if event.phase == "intent":
            if old is not None:
                raise JournalError("duplicate or conflicting purge intent")
        elif old != (event.subject_id, "intent"):
            raise JournalError("completion lacks matching intent")
        phases[event.purge_id] = (event.subject_id, event.phase)
        heads.append(Head(index, item["digest"]))
    return Snapshot(scope, tuple(doc["events"]), tuple(heads))


def _append(snapshot: Snapshot, event: Event) -> Snapshot:
    if snapshot.head.sequence == MAX_EVENTS:
        raise JournalError("journal capacity reached; no history may be discarded")
    record = dict(sequence=snapshot.head.sequence + 1, previous=snapshot.head.digest,
                  purge_id=event.purge_id, subject_id=event.subject_id, phase=event.phase)
    record["digest"] = _digest(record)
    body = canonical({"format": FORMAT, "scope": snapshot.scope,
                      "events": [*snapshot.events, record]})
    return decode(body, snapshot.scope)


@dataclass(frozen=True)
class Stored:
    body: bytes = field(repr=False)
    etag: str

    def __post_init__(self):
        if not isinstance(self.etag, str) or not _ETAG.fullmatch(self.etag):
            raise JournalError("missing or invalid strong ETag")
        if type(self.body) is not bytes or len(self.body) > MAX_BYTES:
            raise JournalError("invalid stored journal size")


class Store(Protocol):
    def read(self) -> Stored: ...
    def write(self, body: bytes, *, etag: str | None) -> None:
        """None means explicit create-only; otherwise exactly one If-Match PUT."""


@dataclass(frozen=True)
class Outcome:
    # Successful storage has NOT persisted a new independent trusted anchor.
    status: str  # STORED_UNANCHORED / ALREADY_STORED_UNANCHORED / CONFLICT / UNKNOWN
    observed: Head | None = None


class Journal:
    def __init__(self, store: Store, scope: str):
        self.store = store
        self.scope = _scope(scope)

    def observe(self, floor: Head) -> tuple[Snapshot, str]:
        stored = self.store.read()  # Missing must never silently become empty history.
        snapshot = decode(stored.body, self.scope)
        snapshot.require_floor(floor)
        return snapshot, stored.etag

    def initialize(self, expected: Head) -> Outcome:
        if expected != genesis(self.scope):
            raise JournalError("initialization requires explicit genesis pin")
        return self._write(empty(self.scope), etag=None)

    def append(self, event: Event, expected: Head) -> Outcome:
        if not isinstance(event, Event):
            raise JournalError("invalid event")
        snapshot, etag = self.observe(expected)
        # Idempotence is an observation, not a second write or purge authorization.
        for record in snapshot.events:
            if record["purge_id"] == event.purge_id and record["phase"] == event.phase:
                if record["subject_id"] != event.subject_id:
                    raise JournalError("conflicting idempotency identity")
                return Outcome("ALREADY_STORED_UNANCHORED", snapshot.head)
        if snapshot.head != expected:
            raise Conflict("head advanced; refresh independent trusted state")
        return self._write(_append(snapshot, event), etag=etag)

    def _write(self, candidate: Snapshot, *, etag: str | None) -> Outcome:
        ambiguous = False
        try:
            self.store.write(candidate.encode(), etag=etag)
        except Conflict:
            return Outcome("CONFLICT")
        except WriteUnknown:
            ambiguous = True
        # Exactly one read-back, including after an unknown write. NEVER retry PUT.
        try:
            observed, _ = self.observe(candidate.head)
        except JournalError:
            return Outcome("UNKNOWN")
        return Outcome("UNKNOWN" if ambiguous else "STORED_UNANCHORED", observed.head)
