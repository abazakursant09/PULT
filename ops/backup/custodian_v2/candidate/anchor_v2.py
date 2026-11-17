"""Existing-file, single-owner schema2 candidate. No bootstrap or recovery API."""
from contextlib import contextmanager
from dataclasses import dataclass,field
from pathlib import Path

from event_codec import Binding,Head,Event,CodecError,genesis,encode_candidate,decode_record,_uuid
from ops.backup.erasure_anchor import SQLiteAnchor,AnchorError,AnchorCommitUncertain
from owned_host import Lease

APP_ID=0x50553232
SCHEMA=2
MAX_RECEIPTS=1024
META_SQL='''CREATE TABLE anchor_v2 (
    id INTEGER PRIMARY KEY CHECK(id=1), scope TEXT NOT NULL, generation TEXT NOT NULL,
    location TEXT NOT NULL, seq INTEGER NOT NULL, hash TEXT NOT NULL,
    revision INTEGER NOT NULL, pending TEXT)'''
RECEIPTS_SQL='''CREATE TABLE attempts_v2 (
    id TEXT PRIMARY KEY, base_seq INTEGER NOT NULL, base_hash TEXT NOT NULL,
    target_seq INTEGER NOT NULL UNIQUE, target_hash TEXT NOT NULL, body BLOB NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('PREPARED','DISPATCHED','COMMITTED')))'''

@dataclass(frozen=True)
class State:
    head:Head
    revision:int
    pending:str|None=field(repr=False)

@dataclass(frozen=True)
class Prepared:
    base:Head
    target:Head
    event:Event=field(repr=False)
    body:bytes=field(repr=False)
    attempt_id:str=field(repr=False)


def _phase(phases,event):
    old=phases.get(event.purge_id)
    if event.phase=='intent':
        if old is not None: raise AnchorError('duplicate purge intent')
    elif old!=(event.subject_id,'intent'):
        raise AnchorError('completion lacks matching intent')
    phases[event.purge_id]=(event.subject_id,event.phase)


class V2Anchor(SQLiteAnchor):
    """The trusted entry must hold the supplied Lease through reader cleanup.

    This class reuses v1 connection safety but not its schema, create method,
    128-event receipt model or proof of OS-independent placement.
    """
    def __init__(self,path,*,binding,location,owner):
        if not isinstance(binding,Binding) or type(owner) is not Lease:
            raise AnchorError('explicit typed binding and existing owner required')
        self.binding=binding;self._owner=owner
        super().__init__(path,scope=binding.scope,generation=binding.generation,location=location)
        self._check_owner()

    def __repr__(self): return '<V2Anchor private existing single-owner state>'

    def _check_owner(self):
        try:
            if Path(self._owner.spec['state_file'])!=self._path: raise ValueError()
            self._owner.check()
        except Exception: raise AnchorError('custodian ownership unavailable; no retry') from None

    @contextmanager
    def _connection(self,*,creating=False):
        if creating: raise AnchorError('schema2 bootstrap is not exposed')
        self._check_owner()
        with super()._connection() as connection:
            yield connection
        self._check_owner()

    def create(self,*args,**kwargs):
        raise AnchorError('schema2 bootstrap is not exposed')

    def _read(self,connection):
        if (connection.execute('PRAGMA application_id').fetchone()[0]!=APP_ID
                or connection.execute('PRAGMA user_version').fetchone()[0]!=SCHEMA):
            raise AnchorError('unsupported custodian schema')
        objects=connection.execute("SELECT type,name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'").fetchall()
        actual={(row[0],row[1]):row[2] for row in objects}
        if actual!={('table','anchor_v2'):META_SQL,('table','attempts_v2'):RECEIPTS_SQL}:
            raise AnchorError('unsupported custodian objects')
        rows=connection.execute('SELECT * FROM anchor_v2 LIMIT 2').fetchall()
        if len(rows)!=1: raise AnchorError('invalid custodian metadata')
        meta=rows[0]
        if (meta['id'],meta['scope'],meta['generation'],meta['location'])!=(
                1,self.scope,self.generation,self.location):
            raise AnchorError('custodian scope generation or location mismatch')
        attempts=connection.execute('SELECT * FROM attempts_v2 ORDER BY target_seq LIMIT ?',
            (MAX_RECEIPTS+1,)).fetchall()
        if len(attempts)>MAX_RECEIPTS: raise AnchorError('custodian history exceeds bound')
        head=genesis(self.binding);revision=0;pending=None;phases={}
        try:
            for row in attempts:
                _uuid(row['id'])
                if pending is not None: raise AnchorError('receipt after pending attempt')
                base=Head(row['base_seq'],row['base_hash'])
                target=Head(row['target_seq'],row['target_hash'])
                event,decoded=decode_record(row['body'],self.binding,head)
                if base!=head or target!=decoded: raise AnchorError('invalid custodian receipt chain')
                _phase(phases,event)
                phase=row['phase']
                if phase=='COMMITTED': head=target;revision+=3
                elif phase in ('PREPARED','DISPATCHED'):
                    pending=row['id'];revision+=1 if phase=='PREPARED' else 2
                else: raise AnchorError('invalid custodian receipt phase')
        except (CodecError,ValueError,TypeError,KeyError):
            raise AnchorError('invalid custodian receipt data') from None
        if (meta['seq'],meta['hash'],meta['revision'],meta['pending'])!=(head.sequence,head.digest,revision,pending):
            raise AnchorError('custodian metadata and receipts disagree')
        return State(head,revision,pending),phases,len(attempts)

    def _load(self,connection): return self._read(connection)[0]

    def inspect_attempt(self,attempt_id):
        try: _uuid(attempt_id)
        except CodecError: raise AnchorError('canonical attempt identity required') from None
        with self._connection() as connection:
            connection.execute('BEGIN')
            state=self._load(connection)
            row=connection.execute('SELECT phase FROM attempts_v2 WHERE id=?',(attempt_id,)).fetchone()
            return state,None if row is None else row['phase']

    def prepare(self,expected,attempt_id,event):
        try:
            _uuid(attempt_id)
            if not isinstance(expected,State) or not isinstance(event,Event): raise CodecError()
            body,target=encode_candidate(self.binding,expected.head,event)
        except CodecError: raise AnchorError('typed canonical preparation required') from None
        prepared=Prepared(expected.head,target,event,body,attempt_id)
        committing=False
        try:
            with self._connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                current,phases,count=self._read(connection)
                if current!=expected or current.pending is not None:
                    raise AnchorError('custodian changed or has a pending attempt')
                if count>=MAX_RECEIPTS: raise AnchorError('custodian history budget exhausted')
                _phase(phases,event)
                connection.execute("INSERT INTO attempts_v2 VALUES(?,?,?,?,?,?,'PREPARED')",
                    (attempt_id,prepared.base.sequence,prepared.base.digest,target.sequence,target.digest,body))
                connection.execute('UPDATE anchor_v2 SET revision=revision+1,pending=? WHERE id=1',(attempt_id,))
                committing=True
                connection.execute('COMMIT')
        except AnchorError:
            if committing: raise AnchorCommitUncertain('preparation response uncertain; never resend') from None
            raise
        reserved=State(expected.head,expected.revision+1,attempt_id)
        if self.load()!=reserved: raise AnchorError('prepared receipt readback mismatch')
        return prepared,reserved

    def transition(self,expected,prepared,*,complete):
        if type(complete) is not bool or not isinstance(expected,State) or not isinstance(prepared,Prepared):
            raise AnchorError('typed transition required')
        wanted=State(prepared.target if complete else expected.head,expected.revision+1,
            None if complete else expected.pending)
        committing=False
        try:
            with self._connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                if self._load(connection)!=expected or expected.pending!=prepared.attempt_id:
                    raise AnchorError('stale or missing custodian receipt')
                row=connection.execute('SELECT * FROM attempts_v2 WHERE id=?',(expected.pending,)).fetchone()
                if (row is None or row['phase']!=('DISPATCHED' if complete else 'PREPARED')
                        or row['body']!=prepared.body or (row['base_seq'],row['base_hash'])!=(prepared.base.sequence,prepared.base.digest)
                        or (row['target_seq'],row['target_hash'])!=(prepared.target.sequence,prepared.target.digest)):
                    raise AnchorError('attempt identity or phase mismatch')
                connection.execute('UPDATE attempts_v2 SET phase=? WHERE id=?',
                    ('COMMITTED' if complete else 'DISPATCHED',expected.pending))
                if complete:
                    connection.execute('UPDATE anchor_v2 SET seq=?,hash=?,revision=revision+1,pending=NULL WHERE id=1',
                        (prepared.target.sequence,prepared.target.digest))
                else: connection.execute('UPDATE anchor_v2 SET revision=revision+1 WHERE id=1')
                committing=True
                connection.execute('COMMIT')
        except AnchorError:
            if committing: raise AnchorCommitUncertain('transition response uncertain; never resend') from None
            raise
        return wanted
