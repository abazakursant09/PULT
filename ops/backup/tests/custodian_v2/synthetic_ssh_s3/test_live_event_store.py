"""Signed transport contract proof; mocks do not prove provider/TLS/IAM."""
import inspect
from pathlib import Path
import ssl
import sys
import unittest
from unittest import mock
from uuid import uuid4

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
SOURCE=REPO/'ops/backup/custodian_v2/synthetic_ssh_s3'
sys.path.insert(0,str(HERE))
import test_support as support
s,codec=support.base_store()
import httpx

class LiveStoreTests(unittest.TestCase):
    def setUp(self):
        self.binding=codec.Binding('synthetic_fixture',str(uuid4()))
        self.base=codec.genesis(self.binding)
        self.event=codec.Event(str(uuid4()),str(uuid4()),'intent')
        self.args=dict(endpoint='https://s3.ru-3.storage.selcloud.ru',region='ru-3',bucket='synthetic-bucket',
            binding=self.binding,credential=dict(access_key='FAKE_ONLY_KEY',secret_key='artificial-not-working-secret'),
            peer_ipv4='1.1.1.1')
        self.calls=[];self.transport_args=[]
    def factory(self,handler):
        def wrapped(request):self.calls.append(request);return handler(request)
        def transport(**kwargs):self.transport_args.append(kwargs);return httpx.MockTransport(wrapped)
        return mock.patch.object(httpx,'HTTPTransport',side_effect=transport)
    def test_no_transport_client_or_tls_downgrade_parameter(self):
        signature=inspect.signature(s.EventWriter)
        self.assertEqual(set(signature.parameters),set(self.args))
        for forbidden in ('transport','client','verify','proxy','ca_file','endpoint_ip','auth','retries'):
            with self.assertRaises(TypeError):s.EventWriter(**self.args,**{forbidden:None})
    def test_exact_public_ip_and_reviewed_endpoint_before_transport(self):
        changes=[('peer_ipv4',v) for v in ('127.0.0.1','0.0.0.0','10.0.0.1','224.0.0.1','::1','1.1.1.01','1.1.1.1:443',True,None)]
        changes += [('endpoint','http://s3.ru-3.storage.selcloud.ru'),('region','ru-7'),('bucket','a/b')]
        with mock.patch.object(httpx,'HTTPTransport',side_effect=AssertionError('must not construct')):
            for key,value in changes:
                with self.subTest(key=key,value=value),self.assertRaises(s.StoreError):
                    s.EventWriter(**dict(self.args,**{key:value}))
    def test_verified_context_no_proxy_or_retries(self):
        with self.factory(lambda r:httpx.Response(200)):
            writer=s.EventWriter(**self.args);writer.close()
        self.assertEqual(len(self.transport_args),1)
        args=self.transport_args[0];self.assertIs(args['trust_env'],False);self.assertEqual(args['retries'],0)
        self.assertIsInstance(args['verify'],ssl.SSLContext)
        self.assertTrue(args['verify'].check_hostname);self.assertEqual(args['verify'].verify_mode,ssl.CERT_REQUIRED)
    def test_bad_default_context_refuses(self):
        for value in (mock.Mock(check_hostname=False,verify_mode=ssl.CERT_REQUIRED),
                      mock.Mock(check_hostname=True,verify_mode=ssl.CERT_NONE)):
            with mock.patch.object(ssl,'create_default_context',return_value=value), \
                 mock.patch.object(httpx,'HTTPTransport',side_effect=AssertionError('must refuse')):
                with self.assertRaises(s.StoreError):s.EventWriter(**self.args)
    def test_put_immutable_signed_original_host_tls_name_and_pinned_ip(self):
        with self.factory(lambda r:httpx.Response(200)):
            writer=s.EventWriter(**self.args)
            self.assertEqual(writer.create(self.base,self.event),'PUT_HTTP200_UNCONFIRMED');writer.close()
        self.assertEqual(len(self.calls),1);request=self.calls[0]
        self.assertEqual(request.method,'PUT');self.assertEqual(request.url.host,'1.1.1.1')
        self.assertEqual(request.headers['host'],'s3.ru-3.storage.selcloud.ru')
        self.assertEqual(request.headers['if-none-match'],'*')
        self.assertIn('if-none-match',request.headers['authorization'])
        self.assertEqual(request.extensions['sni_hostname'],'s3.ru-3.storage.selcloud.ru')
        self.assertEqual(request.url.path,'/synthetic-bucket/'+codec.object_key(self.binding,1))
        self.assertEqual(request.content,codec.encode_candidate(self.binding,self.base,self.event)[0])
    def test_put_error_status_no_retry_or_redirect(self):
        for code in (201,204,301,307,400,403,404,409,429,500,503):
            self.calls=[]
            with self.factory(lambda r:httpx.Response(code,headers={'location':'https://example.com/'})):
                writer=s.EventWriter(**self.args)
                with self.subTest(code=code),self.assertRaises(s.WriteUnknown):writer.create(self.base,self.event)
                writer.close()
            self.assertEqual(len(self.calls),1)
    def test_put_received_conflict_classifies_after_close(self):
        with self.factory(lambda r:httpx.Response(412)):
            writer=s.EventWriter(**self.args)
            with self.assertRaises(s.Conflict):writer.create(self.base,self.event)
            writer.close()
        self.assertEqual(len(self.calls),1)
    def test_put_io_exception_not_conflict_and_never_retry(self):
        def handler(r):raise s.Conflict('injected handler cannot forge status')
        with self.factory(handler):
            writer=s.EventWriter(**self.args)
            with self.assertRaises(s.WriteUnknown):writer.create(self.base,self.event)
            writer.close()
        self.assertEqual(len(self.calls),1)
    def test_reader_verifies_exact_next_record(self):
        body,head=codec.encode_candidate(self.binding,self.base,self.event)
        with self.factory(lambda r:httpx.Response(200,content=body)):
            reader=s.EventReader(**self.args);self.assertEqual(reader.read_next(self.base),(self.event,head));reader.close()
        self.assertEqual(len(self.calls),1);self.assertEqual(self.calls[0].method,'GET')
        self.assertNotIn('if-none-match',self.calls[0].headers);self.assertEqual(self.calls[0].content,b'')
    def test_reader_rejects_status_encoding_oversize_and_bad_chain(self):
        body,_=codec.encode_candidate(self.binding,self.base,self.event)
        for code,headers,data in ((403,{},b''),(301,{'location':'https://example.com'},b''),
            (200,{'content-encoding':'gzip'},b'invalid'),(200,{},b'x'*(codec.MAX_RECORD_BYTES+1)),
            (200,{},body.replace(self.binding.scope.encode(),b'different_scope')),(200,{},b'{}')):
            self.calls=[]
            with self.factory(lambda r:httpx.Response(code,headers=headers,content=data)):
                reader=s.EventReader(**self.args)
                with self.assertRaises(s.StoreError):reader.read_next(self.base)
                reader.close()
            self.assertEqual(len(self.calls),1)
    def test_reader_missing_is_not_automatic_bootstrap(self):
        with self.factory(lambda r:httpx.Response(404)):
            reader=s.EventReader(**self.args)
            with self.assertRaises(s.Missing):reader.read_next(self.base)
            reader.close()
        self.assertEqual(len(self.calls),1)
    def test_capabilities_do_not_gain_other_method(self):
        with self.factory(lambda r:httpx.Response(200)):
            writer=s.EventWriter(**self.args);reader=s.EventReader(**self.args)
            self.assertFalse(hasattr(writer,'read_next'));self.assertFalse(hasattr(reader,'create'))
            with self.assertRaises(s.StoreError):writer._signed_request('GET',1)
            with self.assertRaises(s.StoreError):reader._signed_request('PUT',1,b'{}')
            writer.close();reader.close()
        self.assertEqual(self.calls,[])
    def test_closed_and_invalid_inputs_dispatch_nothing(self):
        with self.factory(lambda r:httpx.Response(200)):
            writer=s.EventWriter(**self.args);writer.close()
            with self.assertRaises(s.WriteUnknown):writer.create(self.base,self.event)
            reader=s.EventReader(**self.args);reader.close()
            with self.assertRaises(s.StoreError):reader.read_next(self.base)
        self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
