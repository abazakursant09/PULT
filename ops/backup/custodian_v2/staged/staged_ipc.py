"""Local schema2 staged IPC candidate; no loader, listener, live S3 or permit.

The embedding trusted process supplies the exact already-owned anchor/reader
or writer capability. This library does not make a cold writable host trusted.
"""
import array
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import struct
import sys
import threading
import time
from uuid import UUID, uuid4

from anchor_v2 import V2Anchor
from event_codec import Event, Head, canonical, encode_candidate
from event_store import EventReader, EventWriter
from protocol_v2 import V2Custodian, location

FORMAT='pult-staged-erasure-ipc-v2'
MAX_FRAME=4096
SECONDS=5
FALSE_AUTHORITY={'purge':False,'restore':False}
SO_PEERPIDFD=77  # Linux uapi asm-generic/socket.h; unavailable means refusal.

class Refused(Exception): pass
class Unknown(Exception): pass

def runtime():
    if (sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12)
        or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.flags.optimize
        or os.getuid()<1000 or os.getuid()!=os.geteuid() or threading.active_count()!=1):
        raise Refused('isolated dedicated runtime required')

def uuid(value):
    if type(value) is not str: raise Refused('UUID required')
    try: parsed=UUID(value)
    except ValueError: raise Refused('UUID required') from None
    if parsed.version!=4 or str(parsed)!=value: raise Refused('UUID4 required')
    return value

def head(value):
    if type(value) is not dict or set(value)!={'sequence','digest'}: raise Refused('head fields')
    return Head(value['sequence'],value['digest'])

def head_wire(value): return {'sequence':value.sequence,'digest':value.digest}

def decode(raw):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_FRAME: raise Refused('frame bound')
    def pairs(rows):
        result={}
        for name,item in rows:
            if name in result: raise Refused('duplicate field')
            result[name]=item
        return result
    try:
        value=json.loads(raw,object_pairs_hook=pairs)
        if type(value) is not dict or canonical(value)!=raw: raise Refused('canonical frame required')
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError): raise Refused('invalid frame') from None

def process_start(pid):
    raw=Path('/proc/'+str(pid)+'/stat').read_text();tail=raw[raw.rfind(')')+2:].split()
    if len(tail)<20 or tail[0]=='Z': raise Refused('live peer required')
    return int(tail[19])

class Channel:
    def __init__(self,connection,uid,gid):
        self.peer_fd=None
        runtime()
        if (type(uid) is not int or type(gid) is not int or not 1000<=uid<2**31 or not 1000<=gid<2**31
            or connection.family!=socket.AF_UNIX or connection.getsockopt(socket.SOL_SOCKET,socket.SO_TYPE)!=socket.SOCK_SEQPACKET):
            raise Refused('fixed local principal/packet channel required')
        connection.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        self.connection=connection;self.peer=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if self.peer[0]<=0 or self.peer[1:]!=(uid,gid): raise Refused('peer principal refused')
        try:
            # Socket provenance is essential: pidfd_open(numeric_pid) could pin
            # a recycled PID rather than the process that opened this socket.
            self.peer_fd=struct.unpack('i',connection.getsockopt(socket.SOL_SOCKET,SO_PEERPIDFD,4))[0]
            os.set_inheritable(self.peer_fd,False)
            self.deadline=time.monotonic()+SECONDS;self.pid=os.getpid();self.thread=threading.current_thread()
            self.check_peer();self.start=process_start(self.peer[0]);self.check_peer()
        except BaseException:
            self.close();raise
    def check_peer(self):
        if self.peer_fd is None or select.select([self.peer_fd],[],[],0)[0]: raise Refused('original peer exited')
        fields=dict(row.split(':',1) for row in Path('/proc/self/fdinfo/'+str(self.peer_fd)).read_text().splitlines() if ':' in row)
        if fields.get('Pid','').strip()!=str(self.peer[0]): raise Refused('socket peer pin mismatch')
    def close(self):
        if self.peer_fd is not None:
            fd=self.peer_fd;self.peer_fd=None;os.close(fd)
    def check(self):
        runtime()
        if os.getpid()!=self.pid or threading.current_thread() is not self.thread: raise Refused('same process/thread required')
        self.check_peer()
        if (struct.unpack('3i',self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))!=self.peer
            or process_start(self.peer[0])!=self.start): raise Refused('peer identity changed')
        self.check_peer()
        remaining=self.deadline-time.monotonic()
        if remaining<=0: raise Unknown('stage deadline')
        self.connection.settimeout(remaining)
    def send(self,value):
        self.check();body=canonical(value)
        if not 0<len(body)<=MAX_FRAME or self.connection.send(body)!=len(body): raise Unknown('frame send uncertain')
    def receive(self):
        self.check();raw,items,flags,_=self.connection.recvmsg(MAX_FRAME+1,
            socket.CMSG_SPACE(12)+socket.CMSG_SPACE(64*array.array('i').itemsize),socket.MSG_CMSG_CLOEXEC)
        credentials=[];rights=[];foreign=False
        try:
            for level,kind,value in items:
                if level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS and len(value)==12:
                    credentials.append(struct.unpack('3i',value))
                elif level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                    descriptors=array.array('i');descriptors.frombytes(value[:len(value)//descriptors.itemsize*descriptors.itemsize]);rights.extend(descriptors)
                else: foreign=True
            if (not raw or flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC) or rights or foreign or credentials!=[self.peer]):
                raise Refused('authenticated single packet required')
            self.check();return decode(raw)
        finally:
            for descriptor in rights: os.close(descriptor)

def binding(anchor):
    return {'scope':anchor.binding.scope,'generation':anchor.binding.generation,'location':anchor.location}

def token(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{32}',value): raise Refused('memory token required')
    return value

def exact(actual,expected):
    # Dict equality alone would equate booleans/floats with integer head fields.
    return type(actual) is dict and canonical(actual)==canonical(expected)

def context(bind,attempt,event,base):
    body,target=encode_candidate(_binding(bind),base,event)
    return {'format':FORMAT,'binding':dict(bind),'attempt':uuid(attempt),
        'event_sha256':hashlib.sha256(body).hexdigest(),'base':head_wire(base),'target':head_wire(target)}

def _binding(bind):
    from event_codec import Binding
    return Binding(bind['scope'],bind['generation'])

class Server:
    """One connection/attempt; no listener, bootstrap, retry or recovery API."""
    def __init__(self,anchor,reader,writer_uid,writer_gid):
        runtime()
        if type(anchor) is not V2Anchor or type(reader) is not EventReader: raise Refused('exact owned capabilities required')
        self.anchor=anchor;self.custodian=V2Custodian(anchor,reader)
        self.writer_uid=writer_uid;self.writer_gid=writer_gid;self.used=False
    def serve(self,connection):
        runtime()
        if self.used: raise Refused('one server attempt only')
        self.used=True;prepared=claimed=channel=None
        try:
            channel=Channel(connection,self.writer_uid,self.writer_gid);request=channel.receive()
            if (set(request)!={'format','op','binding','attempt','event','base'} or request['format']!=FORMAT
                or request['op']!='prepare' or request['binding']!=binding(self.anchor)
                or type(request['event']) is not dict or set(request['event'])!={'purge_id','subject_id','phase'}):
                raise Refused('exact preparation request required')
            item=Event(**request['event']);base=head(request['base']);attempt=uuid(request['attempt'])
            initial=self.anchor.load()
            if initial.head!=base or initial.pending is not None: raise Refused('expected head mismatch or pending')
            ctx=context(binding(self.anchor),attempt,item,base)
            prepared=self.custodian.prepare(item,attempt_id=attempt)
            reserved,phase=self.anchor.inspect_attempt(attempt)
            if (phase!='PREPARED' or reserved.head!=base or reserved.pending!=attempt
                or reserved.revision!=initial.revision+1): raise Unknown('preparation snapshot changed')
            prepare_token=uuid4().hex
            channel.send(dict(ctx,status='PREPARED',token=prepare_token,authority=dict(FALSE_AUTHORITY)))
            dispatch=channel.receive()
            if not exact(dispatch,dict(ctx,op='dispatch',token=prepare_token)): raise Refused('exact dispatch required')
            claimed=self.custodian.claim(prepared)
            dispatched,phase=self.anchor.inspect_attempt(attempt)
            if (phase!='DISPATCHED' or dispatched.head!=base or dispatched.pending!=attempt
                or dispatched.revision!=initial.revision+2): raise Unknown('dispatch snapshot changed')
            dispatch_token=uuid4().hex
            channel.send(dict(ctx,status='DISPATCHED',token=dispatch_token,authority=dict(FALSE_AUTHORITY)))
            confirm=channel.receive()
            if not exact(confirm,dict(ctx,op='confirm',token=dispatch_token)): raise Refused('exact confirmation required')
            state=self.custodian.confirm(claimed)  # Independent GET and durable COMMIT, never writer ACK alone.
            if (head_wire(state.head)!=ctx['target'] or state.pending is not None
                or state.revision!=initial.revision+3): raise Unknown('confirmed head mismatch')
            channel.send(dict(ctx,status='COMMITTED_STORAGE_ONLY',authority=dict(FALSE_AUTHORITY)))
            return 'COMMITTED_STORAGE_ONLY'
        except (Exception,KeyboardInterrupt,SystemExit):
            return 'UNKNOWN_RETAIN_DURABLE_STATE'
        finally:
            if prepared is not None: self.custodian.abandon(prepared)
            if claimed is not None: self.custodian.abandon(claimed)
            if channel is not None: channel.close()

class Client:
    def __init__(self,writer,custodian_uid,custodian_gid):
        runtime()
        if type(writer) is not EventWriter: raise Refused('writer-only capability required')
        self.writer=writer;self.uid=custodian_uid;self.gid=custodian_gid;self.used=False
    def append(self,connection,item,*,attempt_id,expected_head):
        runtime()
        if self.used: raise Refused('one client attempt only')
        self.used=True;channel=None
        try:
            channel=Channel(connection,self.uid,self.gid)
            bind={'scope':self.writer._binding.scope,'generation':self.writer._binding.generation,'location':location(self.writer)}
            ctx=context(bind,attempt_id,item,expected_head)
            channel.send({'format':FORMAT,'op':'prepare','binding':bind,'attempt':attempt_id,
                'event':{'purge_id':item.purge_id,'subject_id':item.subject_id,'phase':item.phase},'base':head_wire(expected_head)})
            ack=channel.receive();cap=token(ack.get('token'))
            if not exact(ack,dict(ctx,status='PREPARED',token=cap,authority=dict(FALSE_AUTHORITY))): raise Refused('preparation ACK refused')
            channel.send(dict(ctx,op='dispatch',token=cap))
            ack=channel.receive();cap=token(ack.get('token'))
            if not exact(ack,dict(ctx,status='DISPATCHED',token=cap,authority=dict(FALSE_AUTHORITY))): raise Refused('dispatch ACK refused')
            wire=self.writer.create(expected_head,item)
            if type(wire) is not str or wire!='PUT_HTTP200_UNCONFIRMED': raise Unknown('PUT response unknown')
            channel.send(dict(ctx,op='confirm',token=cap))
            ack=channel.receive()
            if not exact(ack,dict(ctx,status='COMMITTED_STORAGE_ONLY',authority=dict(FALSE_AUTHORITY))): raise Refused('commit ACK refused')
            return {'status':'COMMITTED_STORAGE_ONLY','head':dict(ctx['target']),'purge_or_restore_admitted':False}
        except (Exception,KeyboardInterrupt,SystemExit):
            return {'status':'UNKNOWN','head':None,'purge_or_restore_admitted':False}
        finally:
            if channel is not None: channel.close()
