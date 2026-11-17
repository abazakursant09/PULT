"""Immutable-event FORMAT prototype, not a writer/anchor/freshness/purge gate."""
from dataclasses import dataclass,field
import hashlib
import json
import re
from uuid import UUID

FORMAT='pult-erasure-event-v2'
MAX_RECORD_BYTES=2048
MAX_SEQUENCE=2**63-1

class CodecError(Exception): pass

def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')

def _uuid(value):
    if type(value) is not str: raise CodecError('explicit UUID required')
    try: parsed=UUID(value)
    except ValueError: raise CodecError('invalid UUID') from None
    if parsed.version!=4 or str(parsed)!=value: raise CodecError('canonical UUID4 required')

@dataclass(frozen=True)
class Binding:
    scope:str
    generation:str
    def __post_init__(self):
        if type(self.scope) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,64}',self.scope):
            raise CodecError('invalid scope')
        _uuid(self.generation)

@dataclass(frozen=True)
class Head:
    sequence:int
    digest:str
    def __post_init__(self):
        if (type(self.sequence) is not int or not 0<=self.sequence<=MAX_SEQUENCE or
                type(self.digest) is not str or not re.fullmatch('[0-9a-f]{64}',self.digest)):
            raise CodecError('invalid head')

@dataclass(frozen=True)
class Event:
    purge_id:str=field(repr=False)
    subject_id:str=field(repr=False)
    phase:str
    def __post_init__(self):
        _uuid(self.purge_id);_uuid(self.subject_id)
        if type(self.phase) is not str or self.phase not in ('intent','complete'): raise CodecError('invalid phase')

def genesis(binding):
    if not isinstance(binding,Binding): raise CodecError('typed binding required')
    return Head(0,hashlib.sha256(canonical({'format':FORMAT,'kind':'genesis',
        'scope':binding.scope,'generation':binding.generation})).hexdigest())

def object_key(binding,sequence):
    if not isinstance(binding,Binding): raise CodecError('typed binding required')
    if type(sequence) is not int or not 1<=sequence<=MAX_SEQUENCE: raise CodecError('invalid object sequence')
    return f'pult/erasure-v2/{binding.scope}/{binding.generation}/events/{sequence:020d}.json'

def encode_candidate(binding,previous,event):
    """Format only; no business authorization, dispatch or successful receipt."""
    if not isinstance(binding,Binding) or not isinstance(previous,Head) or not isinstance(event,Event):
        raise CodecError('typed candidate inputs required')
    if previous.sequence==0 and previous!=genesis(binding): raise CodecError('wrong genesis')
    if previous.sequence==MAX_SEQUENCE: raise CodecError('sequence exhausted')
    value={'format':FORMAT,'scope':binding.scope,'generation':binding.generation,'sequence':previous.sequence+1,
        'previous':previous.digest,'event':{'purge_id':event.purge_id,'subject_id':event.subject_id,'phase':event.phase}}
    digest=hashlib.sha256(canonical(value)).hexdigest();value['digest']=digest
    body=canonical(value)
    if len(body)>MAX_RECORD_BYTES: raise CodecError('record exceeds bound')
    return body,Head(previous.sequence+1,digest)

def decode_record(body,binding,previous):
    if not isinstance(binding,Binding) or not isinstance(previous,Head): raise CodecError('typed record binding/head required')
    if type(body) is not bytes or not 0<len(body)<=MAX_RECORD_BYTES: raise CodecError('bounded bytes required')
    def pairs(rows):
        result={}
        for key,item in rows:
            if key in result: raise CodecError('duplicate field')
            result[key]=item
        return result
    try:
        value=json.loads(body,object_pairs_hook=pairs)
        if (type(value) is not dict or set(value)!={'format','scope','generation','sequence','previous','event','digest'}
                or canonical(value)!=body or value['format']!=FORMAT or value['scope']!=binding.scope
                or value['generation']!=binding.generation or type(value['sequence']) is not int
                or value['sequence']!=previous.sequence+1 or value['previous']!=previous.digest):
            raise CodecError('record binding or chain mismatch')
        event=value['event']
        if type(event) is not dict or set(event)!={'purge_id','subject_id','phase'}: raise CodecError('invalid event')
        parsed=Event(**event);actual=value.pop('digest')
        head=Head(value['sequence'],actual)
        if hashlib.sha256(canonical(value)).hexdigest()!=actual: raise CodecError('record digest mismatch')
        if previous.sequence==0 and previous!=genesis(binding): raise CodecError('wrong genesis')
        return parsed,head
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise CodecError('invalid canonical record') from None

@dataclass(frozen=True)
class VerifiedHistory:
    head:Head
    records:int
    intents:int
    completions:int
    unresolved:int
    status:str='HISTORY_BYTES_VERIFIED_ONLY'
    write_admitted:bool=False
    purge_or_restore_admitted:bool=False

def verify_history(binding,trusted_target,read_bytes,*,max_records):
    """Read-only full scan from genesis. No target inference or partial PASS.

    Caller must independently trust target/provision the bounded reader. This
    function cannot authenticate their provenance or enforce a remote I/O deadline.
    """
    if (not isinstance(binding,Binding) or not isinstance(trusted_target,Head) or
            type(max_records) is not int or not 1<=max_records<=MAX_SEQUENCE or not callable(read_bytes)):
        raise CodecError('explicit scan target/budget/reader required')
    if trusted_target.sequence>max_records: raise CodecError('scan budget insufficient; no partial proof')
    head=genesis(binding);phases={};intents=completions=0
    for sequence in range(1,trusted_target.sequence+1):
        key=object_key(binding,sequence)
        try: body=read_bytes(key)
        except Exception: raise CodecError('history object unavailable; no retry') from None
        event,head=decode_record(body,binding,head)
        old=phases.get(event.purge_id)
        if event.phase=='intent':
            if old is not None: raise CodecError('duplicate purge intent')
            phases[event.purge_id]=(event.subject_id,'intent');intents+=1
        else:
            if old!=(event.subject_id,'intent'): raise CodecError('completion lacks matching intent')
            phases[event.purge_id]=(event.subject_id,'complete');completions+=1
    if head!=trusted_target: raise CodecError('trusted target mismatch or rollback')
    return VerifiedHistory(head,trusted_target.sequence,intents,completions,intents-completions)
