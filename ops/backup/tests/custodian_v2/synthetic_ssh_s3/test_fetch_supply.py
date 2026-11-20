"""Offline-only supply tests. Run Linux python3 -I -S -B test_fetch_supply.py."""
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SOURCE = (HERE / 'fetch_openssh_supply.py').read_bytes()


def load(path):
    module = ModuleType('offline_openssh_supply')
    module.__file__ = str(path)
    exec(compile(SOURCE, str(path), 'exec', dont_inherit=True, optimize=0), module.__dict__)
    return module


class Reply:
    def __init__(self, body, status=200, headers=None):
        self.body = io.BytesIO(body)
        self.status = status
        self.headers = [('Content-Length', str(len(body)))] if headers is None else headers
        self.closed = False
        self.bounds = []

    def getheaders(self):
        return self.headers

    def read1(self, bound):
        self.bounds.append(bound)
        return self.body.read(min(7, bound))

    def close(self):
        self.closed = True


class FetchSupplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pult-supply-offline-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / 'fetch_openssh_supply.py'
        self.script.write_bytes(SOURCE)
        self.script.chmod(0o600)
        self.module = load(self.script)
        self.original_packages = self.module.PACKAGES
        self.bodies = (b'offline-openssh-package', b'offline-libwrap-package')
        self.module.PACKAGES = tuple((n, len(b), hashlib.sha256(b).hexdigest(), p)
                                     for (n, _, _, p), b in zip(self.original_packages, self.bodies))
        self.directory = self.root / 'openssh-supply'
        self.directory.mkdir(mode=0o700)
        self.manifest = self.directory / 'SUPPLY.json'
        self.write_manifest(self.module.expected_manifest())
        self.calls = []
        self.requests = []
        self.closed = []
        self.replies = [Reply(b) for b in self.bodies]
        outer = self

        class FakeConnection:
            def __init__(self, host, port, *, timeout, context):
                outer.calls.append((host, port, timeout, context))
                self.sock = None

            def request(self, method, path, *, headers):
                outer.requests.append((method, path, headers))

            def getresponse(self):
                return outer.replies[len(outer.calls) - 1]

            def close(self):
                outer.closed.append(True)

        self.addCleanup(patch.stopall)
        patch.object(self.module.http.client, 'HTTPSConnection', FakeConnection).start()
        # If the fake transport is accidentally bypassed, fail instead of networking.
        patch.object(socket, 'create_connection', side_effect=AssertionError('OFFLINE_ONLY')).start()
        patch.dict(os.environ, {n: '' for n in ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'SSLKEYLOGFILE')}).start()

    def write_manifest(self, value):
        self.manifest.write_text(json.dumps(value), encoding='utf-8')
        self.manifest.chmod(0o600)

    def artifact(self, index=0):
        return self.directory / self.module.PACKAGES[index][0]

    def existing(self, index=0, body=None):
        path = self.artifact(index)
        path.write_bytes(self.bodies[index] if body is None else body)
        path.chmod(0o600)
        return path

    def refused(self):
        with self.assertRaises((self.module.Refused, OSError, ValueError)):
            self.module.fetch_supply()

    def test_real_manifest_and_fixed_https_pins(self):
        actual = json.loads((HERE / 'openssh-supply/SUPPLY.json').read_bytes())
        self.module.PACKAGES = self.original_packages
        self.assertEqual(actual, self.module.expected_manifest())
        self.assertEqual(self.module.HOST, 'archive.ubuntu.com')
        self.assertEqual([p[1] for p in self.original_packages], [511546, 47874])
        self.assertEqual([p[2] for p in self.original_packages], [
            '5d0d5eb672ddc27601e2da4e8465c5736dc73375a21bc1cd1834fcb08fb155bf',
            '36118b862539868408ee056411d6e470068d14c46e5296b29bfb744770f5d2ce'])
        self.assertEqual([p[3] for p in self.original_packages], [
            '/ubuntu/pool/main/o/openssh/openssh-server_9.6p1-3ubuntu13.20_amd64.deb',
            '/ubuntu/pool/main/t/tcp-wrappers/libwrap0_7.6.q-33_amd64.deb'])

    def test_positive_fixed_transport_tls_no_environment_auth(self):
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://user:secret@invalid:9',
                                     'ALL_PROXY': 'http://invalid:9', 'NETRC': '/nonexistent',
                                     'HTTP_AUTHORIZATION': 'secret'}):
            result = self.module.fetch_supply()
        self.assertFalse(result['install_allowed'])
        self.assertFalse(result['maintainer_scripts_allowed'])
        self.assertFalse(result['deployment_admitted'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.closed), 2)
        for index, (host, port, timeout, context) in enumerate(self.calls):
            self.assertEqual((host, port, timeout), ('archive.ubuntu.com', 443, 10))
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertEqual(self.requests[index], ('GET', self.original_packages[index][3],
                             {'Accept-Encoding': 'identity', 'Connection': 'close'}))
            self.assertTrue(self.replies[index].closed)
            self.assertTrue(all(0 < n <= 65536 for n in self.replies[index].bounds))
            self.assertEqual(self.artifact(index).read_bytes(), self.bodies[index])
            self.assertEqual(self.artifact(index).stat().st_nlink, 1)
            self.assertEqual(self.artifact(index).stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),
                         sorted(['SUPPLY.json'] + [p[0] for p in self.original_packages]))

    def test_verified_existing_is_not_replaced_or_fetched(self):
        before = [self.existing(i).stat() for i in range(2)]
        result = self.module.fetch_supply()
        self.assertEqual(self.calls, [])
        self.assertEqual([p['status'] for p in result['packages']], ['verified-existing'] * 2)
        self.assertEqual([self.artifact(i).stat().st_ino for i in range(2)], [s.st_ino for s in before])

    def test_wrong_hash_never_published(self):
        self.replies[1] = Reply(b'X' * len(self.bodies[1]))
        self.refused()
        self.assertFalse(self.artifact(0).exists())
        self.assertFalse(self.artifact(1).exists())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.closed), 2)

    def test_short_long_and_false_content_length(self):
        for body, headers in ((b'short', [('Content-Length', str(len(self.bodies[0])))]),
                              (self.bodies[0] + b'X', [('Content-Length', str(len(self.bodies[0])))]),
                              (self.bodies[0], [('Content-Length', '99999999')])):
            with self.subTest(body=body, headers=headers):
                self.calls.clear()
                self.replies[0] = Reply(body, headers=headers)
                self.refused()
                self.assertEqual(len(self.calls), 1)
                self.assertFalse(self.artifact().exists())

    def test_redirects_are_not_followed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.calls.clear()
                self.replies[0] = Reply(b'', status, [('Location', 'https://evil.invalid/package')])
                self.refused()
                self.assertEqual(len(self.calls), 1)
                self.assertFalse(self.artifact().exists())

    def test_ambiguous_or_encoded_response_refused(self):
        base = [('Content-Length', str(len(self.bodies[0])))]
        for extra in ([('Content-Length', base[0][1])], [('Transfer-Encoding', 'chunked')],
                      [('Content-Encoding', 'gzip')], [('Location', 'https://evil.invalid/')]):
            with self.subTest(extra=extra):
                self.calls.clear()
                self.replies[0] = Reply(self.bodies[0], headers=base + extra)
                self.refused()
                self.assertEqual(len(self.calls), 1)

    def test_existing_partial_is_never_overwritten(self):
        path = self.existing(1, b'partial')
        before = path.stat()
        self.refused()
        self.assertEqual(path.read_bytes(), b'partial')
        self.assertEqual(path.stat().st_ino, before.st_ino)
        self.assertEqual(self.calls, [])

    def test_existing_symlink_refused(self):
        target = self.root / 'outside'
        target.write_bytes(self.bodies[0])
        self.artifact().symlink_to(target)
        self.refused()
        self.assertTrue(self.artifact().is_symlink())
        self.assertEqual(target.read_bytes(), self.bodies[0])
        self.assertEqual(self.calls, [])

    def test_existing_directory_or_fifo_refused(self):
        self.artifact().mkdir()
        self.refused()
        self.artifact().rmdir()
        os.mkfifo(self.artifact(), 0o600)
        self.refused()
        self.assertEqual(self.calls, [])

    def test_existing_hardlink_refused(self):
        path = self.existing()
        os.link(path, self.root / 'alias')
        self.refused()
        self.assertEqual(path.stat().st_nlink, 2)
        self.assertEqual(self.calls, [])

    def test_world_writable_source_or_directory_refused(self):
        for path in (self.script, self.manifest, self.directory, self.root):
            with self.subTest(path=path.name):
                previous = path.stat().st_mode & 0o777
                path.chmod(previous | 0o002)
                self.refused()
                path.chmod(previous)
        path = self.existing()
        path.chmod(0o666)
        self.refused()
        self.assertEqual(self.calls, [])

    def test_manifest_wrong_or_duplicate_refused_before_network(self):
        value = self.module.expected_manifest()
        value['install_allowed'] = True
        self.write_manifest(value)
        self.refused()
        self.manifest.write_bytes(b'{"format":"x","format":"x"}')
        self.refused()
        self.assertEqual(self.calls, [])

    def test_manifest_integer_cannot_impersonate_boolean(self):
        value = self.module.expected_manifest()
        value['component_only'] = 1
        self.write_manifest(value)
        self.refused()
        self.assertEqual(self.calls, [])

    def test_manifest_and_script_hardlinks_and_symlinks_refused(self):
        for path in (self.script, self.manifest):
            with self.subTest(path=path.name):
                alias = self.root / 'source-alias'
                os.link(path, alias)
                self.refused()
                alias.unlink()
                raw = path.read_bytes()
                path.unlink()
                alias.write_bytes(raw)
                alias.chmod(0o600)
                path.symlink_to(alias)
                self.refused()
                path.unlink()
                alias.rename(path)
        self.assertEqual(self.calls, [])

    def test_supply_directory_symlink_refused(self):
        real = self.root / 'real'
        self.directory.rename(real)
        self.directory.symlink_to(real, target_is_directory=True)
        self.refused()
        self.assertEqual(self.calls, [])

    def test_tls_environment_overrides_refused(self):
        for name in ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'SSLKEYLOGFILE'):
            with self.subTest(name=name), patch.dict(os.environ, {name: '/untrusted'}):
                self.refused()
        self.assertEqual(self.calls, [])

    def test_timeout_has_no_retry_and_connection_closed(self):
        with patch.object(Reply, 'read1', side_effect=TimeoutError('synthetic-timeout')):
            self.refused()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.closed), 1)
        self.assertFalse(self.artifact().exists())

    def test_absolute_deadline_is_enforced(self):
        with patch.object(self.module.time, 'monotonic', side_effect=[0, 21]):
            self.refused()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.closed), 1)

    def test_existing_artifact_revalidated_after_other_download(self):
        path = self.existing(0)
        self.replies[0] = Reply(self.bodies[1])
        connection = self.module.http.client.HTTPSConnection
        original = connection.request
        def mutate(*args, **kwargs):
            path.write_bytes(b'X' * len(self.bodies[0]))
            return original(*args, **kwargs)
        with patch.object(connection, 'request', side_effect=mutate, autospec=True):
            self.refused()
        self.assertEqual(len(self.calls), 1)

    def test_source_permissions_rechecked_after_downloads(self):
        original = self.module.fetch
        def mutate(package):
            result = original(package)
            self.script.chmod(0o666)
            return result
        with patch.object(self.module, 'fetch', side_effect=mutate):
            self.refused()
        self.assertEqual(len(self.calls), 2)

    def test_exclusive_publication_does_not_replace_racing_file(self):
        original = self.module.os.link
        def collide(source, destination, **kwargs):
            self.artifact().write_bytes(b'do-not-replace')
            return original(source, destination, **kwargs)
        with patch.object(self.module.os, 'link', side_effect=collide):
            self.refused()
        self.assertEqual(self.artifact().read_bytes(), b'do-not-replace')
        self.assertFalse(any(p.name.startswith('.verified-') for p in self.directory.iterdir()))

    def test_temporary_name_substitution_cannot_publish_a_symlink(self):
        target = self.root / 'unrelated'
        target.write_bytes(b'unchanged')
        original = self.module.os.link
        def substitute(source, destination, **kwargs):
            temporary, = self.directory.glob('.verified-*')
            temporary.unlink()
            temporary.symlink_to(target)
            return original(source, destination, **kwargs)
        with patch.object(self.module.os, 'link', side_effect=substitute):
            self.refused()
        self.assertFalse(os.path.lexists(self.artifact()))
        self.assertEqual(target.read_bytes(), b'unchanged')

    def test_partial_write_failure_removes_only_owned_temporary(self):
        original = self.module.os.write
        count = 0
        def fail(fd, raw):
            nonlocal count
            count += 1
            if count == 1:
                return original(fd, raw[:3])
            raise OSError('synthetic-write-failure')
        with patch.object(self.module.os, 'write', side_effect=fail):
            self.refused()
        self.assertFalse(self.artifact().exists())
        self.assertEqual([p.name for p in self.directory.iterdir()], ['SUPPLY.json'])

    def test_gate_refuses_optimization_missing_flags_or_arguments(self):
        for options, extra in ((['-I', '-S', '-B', '-O'], []), (['-I', '-S', '-B', '-OO'], []),
                               (['-I', '-S'], []), (['-I', '-S', '-B'], ['https://evil.invalid/'])):
            with self.subTest(options=options, extra=extra):
                run = subprocess.run([sys.executable, *options, str(self.script), *extra],
                                     capture_output=True, timeout=5)
                self.assertEqual(run.returncode, 1)
                self.assertEqual(run.stdout, b'')
                self.assertEqual(run.stderr.strip(), b'OPENSSH_SUPPLY_REFUSED')


if __name__ == '__main__':
    if not (sys.platform == 'linux' and sys.flags.isolated and sys.flags.no_site
            and sys.dont_write_bytecode and not sys.flags.optimize):
        raise SystemExit('OFFLINE_SUPPLY_TEST_GATE_REFUSED')
    unittest.main(verbosity=2)
