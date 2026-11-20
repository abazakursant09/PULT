"""Separate synthetic live S3 event capability; immutable PUT or bounded GET."""
from datetime import datetime,timezone
import hashlib
import re
import ssl
from ipaddress import IPv4Address

import httpx

from ops.canary.canary import LIVE_REGION_ENDPOINTS,SelectelS3Transport
from ops.backup.erasure_journal_s3 import _validate_credential
from event_codec import Binding,Head,Event,MAX_RECORD_BYTES,CodecError,decode_record,encode_candidate,genesis,object_key

class StoreError(Exception): pass
class Missing(StoreError): pass
class Conflict(StoreError): pass
class WriteUnknown(StoreError): pass


class _PinnedLiveStore:
    def __init__(self,*,endpoint,region,bucket,binding,credential,peer_ipv4):
        # No client/transport/TLS/DNS/proxy option may come from a caller.
        try: peer = IPv4Address(peer_ipv4)
        except (ValueError, TypeError): raise StoreError('fixed S3 IPv4 required') from None
        if type(peer_ipv4) is not str or str(peer) != peer_ipv4 or not peer.is_global or peer.is_multicast:
            raise StoreError('canonical public S3 IPv4 required')
        if (type(region) is not str or LIVE_REGION_ENDPOINTS.get(region)!=endpoint
                or type(bucket) is not str or not re.fullmatch('[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',bucket)
                or not isinstance(binding,Binding)):
            raise StoreError('invalid fixed event location')
        try: _validate_credential(credential)
        except Exception: raise StoreError('invalid explicit credential') from None
        self._binding=binding;self._bucket=bucket;self._endpoint=endpoint
        self._peer_ipv4=peer_ipv4; self._host=endpoint.removeprefix('https://')
        if region!='ru-3' or self._host!='s3.ru-3.storage.selcloud.ru':
            raise StoreError('fixed reviewed synthetic endpoint required')
        self._signer=SelectelS3Transport({'endpoint':endpoint,'region':region},dict(credential))
        context=ssl.create_default_context()
        if not context.check_hostname or context.verify_mode!=ssl.CERT_REQUIRED:
            raise StoreError('verified default OS trust context required')
        transport=httpx.HTTPTransport(verify=context,trust_env=False,retries=0)
        self._client=httpx.Client(transport=transport,verify=True,trust_env=False,
            follow_redirects=False,auth=None,timeout=httpx.Timeout(15,connect=5))
        self._closed=False

    def close(self):
        self._closed=True
        self._client.close()

    def _signed_request(self,method,sequence,body=b''):
        if self._closed: raise StoreError('event store closed')
        if (method!=self._method or type(body) is not bytes or len(body)>MAX_RECORD_BYTES
                or (method=='GET' and body)):
            raise StoreError('unsupported event capability')
        key=object_key(self._binding,sequence);uri='/'+self._bucket+'/'+key
        extra={'accept-encoding':'identity'}
        if method=='PUT': extra.update({'content-type':'application/json','if-none-match':'*'})
        now=datetime.now(timezone.utc)
        try:
            headers=self._signer._sign(method,'PutObject' if method=='PUT' else 'GetObject',
                uri,'',hashlib.sha256(body).hexdigest(),now.strftime('%Y%m%dT%H%M%SZ'),now.strftime('%Y%m%d'),extra)
        except Exception: raise StoreError('event signing failed') from None
        headers['host']=self._host
        # TCP goes only to the independently pinned IP; original DNS name is
        # still used for SNI and mandatory certificate hostname verification.
        return self._client.stream(method,'https://'+self._peer_ipv4+uri,content=body,headers=headers,
            follow_redirects=False,auth=None,extensions={'sni_hostname':self._host})

    def __repr__(self): return '<SyntheticPinnedLiveEventStore storage only; no purge or restore>'


class EventWriter(_PinnedLiveStore):
    """PUT-only public operation. Not a durable pending/receipt protocol."""
    _method='PUT'
    def create(self,previous,event):
        # Validate all bytes/key before the one request. No caller supplied body,
        # key, headers or validator. The independent verifier remains mandatory.
        if not isinstance(previous,Head) or not isinstance(event,Event):
            raise StoreError('typed candidate required')
        body,target=encode_candidate(self._binding,previous,event)
        try:
            with self._signed_request('PUT',target.sequence,body) as response:
                code=response.status_code
        except Exception:
            raise WriteUnknown('event write outcome unknown') from None
        # Classify only an actually received status after successful stream close.
        # A handler cannot inject our exception classes to forge a conflict/ACK.
        if code==412: raise Conflict('event key already exists')
        if code!=200: raise WriteUnknown('event write outcome unknown')
        # Even200 does not advance any head, infer a durableACK or allow purge.
        return 'PUT_HTTP200_UNCONFIRMED'


class EventReader(_PinnedLiveStore):
    """GET-only public operation, exact next object and chain verification."""
    _method='GET'
    def read_next(self,previous):
        if not isinstance(previous,Head): raise StoreError('typed previous head required')
        if previous.sequence==0 and previous!=genesis(self._binding):
            raise StoreError('wrong event genesis')
        # object_key rejects sequence exhaustion before signing/dispatch.
        object_key(self._binding,previous.sequence+1)
        try:
            with self._signed_request('GET',previous.sequence+1) as response:
                code=response.status_code
                valid=code==200 and response.headers.get('content-encoding','identity')=='identity'
                body=bytearray()
                if valid:
                    for chunk in response.iter_bytes(chunk_size=MAX_RECORD_BYTES+1):
                        if len(body)+len(chunk)>MAX_RECORD_BYTES: raise ValueError('bounded read failed')
                        body.extend(chunk)
        except Exception:
            raise StoreError('event read failed') from None
        if code==404: raise Missing('event object unavailable')
        if not valid: raise StoreError('event read failed')
        try: return decode_record(bytes(body),self._binding,previous)
        except CodecError: raise StoreError('event chain verification failed') from None
