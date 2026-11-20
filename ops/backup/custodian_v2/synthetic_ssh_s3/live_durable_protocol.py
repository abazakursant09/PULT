"""Versioned durable storage-only protocol for separately captured live store."""
from dataclasses import dataclass,field

from event_codec import Binding,Head,Event
from pult_synthetic_store_v1 import EventReader,EventWriter,Conflict
from anchor_v2 import V2Anchor,AnchorError,AnchorCommitUncertain


def location(store):
    if type(store) not in (EventReader,EventWriter): raise AnchorError('exact event capability required')
    binding=store._binding
    if not isinstance(binding,Binding): raise AnchorError('typed event binding required')
    return store._endpoint+'/'+store._bucket+'/pult/erasure-v2/'+binding.scope+'/'+binding.generation+'/events/'

@dataclass(frozen=True)
class Outcome:
    status:str
    head:Head|None=None
    purge_or_restore_admitted:bool=False

@dataclass(frozen=True)
class _Ticket:
    prepared:object=field(repr=False)
    state:object=field(repr=False)


class V2Custodian:
    def __init__(self,anchor,reader):
        if type(anchor) is not V2Anchor or type(reader) is not EventReader:
            raise AnchorError('exact anchor and reader-only capability required')
        self._anchor=anchor;self._reader=reader;self._active={}
        self._check()

    def _check(self):
        if location(self._reader)!=self._anchor.location or self._reader._binding!=self._anchor.binding:
            raise AnchorError('custodian reader binding mismatch')
        self._anchor._check_owner()

    def prepare(self,event,*,attempt_id):
        self._check()
        state=self._anchor.load()
        if state.pending is not None: raise AnchorError('pending attempt blocks new preparation')
        prepared,reserved=self._anchor.prepare(state,attempt_id,event)
        ticket=_Ticket(prepared,reserved);self._active[id(ticket)]=(ticket,False)
        return ticket

    def claim(self,ticket):
        if self._active.pop(id(ticket),None)!=(ticket,False):
            raise AnchorError('dispatch ticket stale consumed or foreign')
        self._check()
        claimed=self._anchor.transition(ticket.state,ticket.prepared,complete=False)
        next_ticket=_Ticket(ticket.prepared,claimed)
        self._active[id(next_ticket)]=(next_ticket,True)
        return next_ticket

    def abandon(self,ticket):
        for key,(active,_) in list(self._active.items()):
            if active.prepared is ticket.prepared:
                self._active.pop(key,None)

    def confirm(self,ticket):
        if self._active.pop(id(ticket),None)!=(ticket,True):
            raise AnchorError('confirmation ticket stale consumed or foreign')
        self._check()
        event,head=self._reader.read_next(ticket.prepared.base)
        if event!=ticket.prepared.event or head!=ticket.prepared.target:
            raise AnchorError('independent reader did not verify exact candidate')
        return self._anchor.transition(ticket.state,ticket.prepared,complete=True)

    def inspect(self,attempt_id):
        self._check()
        state,phase=self._anchor.inspect_attempt(attempt_id)
        status=('OBSERVED_COMMITTED_RECEIPT' if phase=='COMMITTED' else
            'PENDING_REQUIRES_REVIEW' if state.pending is not None else 'NO_SUCH_RECEIPT')
        return Outcome(status,state.head)


class AnchoredEvents:
    def __init__(self,custodian,writer):
        if type(custodian) is not V2Custodian or type(writer) is not EventWriter:
            raise AnchorError('exact custodian and writer-only capability required')
        self._custodian=custodian;self._writer=writer;self._check()

    def _check(self):
        self._custodian._check()
        if (location(self._writer)!=self._custodian._anchor.location
                or self._writer._binding!=self._custodian._anchor.binding):
            raise AnchorError('writer location mismatch')

    def append(self,event,*,attempt_id):
        self._check()
        prepared=self._custodian.prepare(event,attempt_id=attempt_id)
        claimed=None
        try:
            claimed=self._custodian.claim(prepared)  # durable DISPATCHED before PUT
            try:
                self._check()
                wire=self._writer.create(claimed.prepared.base,claimed.prepared.event)
            except Conflict: return Outcome('CONFLICT')
            except Exception: return Outcome('UNKNOWN')
            if type(wire) is not str or wire!='PUT_HTTP200_UNCONFIRMED': return Outcome('UNKNOWN')
            try: state=self._custodian.confirm(claimed)
            except AnchorCommitUncertain: return Outcome('ANCHOR_RESPONSE_UNKNOWN')
            except Exception: return Outcome('UNKNOWN')
            return Outcome('ANCHORED_STORAGE_ONLY',state.head)
        finally:
            self._custodian.abandon(prepared)
            if claimed is not None: self._custodian.abandon(claimed)
