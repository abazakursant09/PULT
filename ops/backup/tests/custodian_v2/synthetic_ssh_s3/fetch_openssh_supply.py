"""CI-only, fixed Ubuntu supply fetch. Never install or run package contents.

Run on Linux with python3 -I -S -B; no arguments, URL or destination options.
Only an authenticated, size/hash-verified response can become an artifact.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import ssl
import stat
import sys
import time


HERE = Path(os.path.abspath(__file__)).parent
HOST = 'archive.ubuntu.com'
# Local names match the separately frozen OpenSSH component fixture.
PACKAGES = (
    ('openssh-server_1%3a9.6p1-3ubuntu13.20_amd64.deb', 511546,
     '5d0d5eb672ddc27601e2da4e8465c5736dc73375a21bc1cd1834fcb08fb155bf',
     '/ubuntu/pool/main/o/openssh/openssh-server_9.6p1-3ubuntu13.20_amd64.deb'),
    ('libwrap0_7.6.q-33_amd64.deb', 47874,
     '36118b862539868408ee056411d6e470068d14c46e5296b29bfb744770f5d2ce',
     '/ubuntu/pool/main/t/tcp-wrappers/libwrap0_7.6.q-33_amd64.deb'),
)
TIMEOUT = 10
DEADLINE = 20
CHUNK = 65536


class Refused(Exception):
    """Deliberately generic failure; no credentials or response body in logs."""


def need(value):
    if not value:
        raise Refused('OPENSSH_SUPPLY_REFUSED')


def fingerprint(row):
    return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_gid,
            row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)


def safe(row, directory=False):
    need((stat.S_ISDIR if directory else stat.S_ISREG)(row.st_mode))
    need(not row.st_mode & 0o022 and row.st_uid in (0, os.geteuid()))
    if not directory:
        need(row.st_nlink == 1)


def open_directory(path):
    """Traverse lexical absolute path without following any directory symlink."""
    need(path.is_absolute() and '..' not in path.parts)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        safe(os.fstat(descriptor), directory=True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_file(directory, name, limit):
    before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    safe(before)
    need(before.st_size <= limit)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                 dir_fd=directory)
    try:
        need(fingerprint(os.fstat(fd)) == fingerprint(before))
        result = bytearray()
        while len(result) <= limit:
            piece = os.read(fd, min(CHUNK, limit + 1 - len(result)))
            if not piece:
                break
            result.extend(piece)
        need(len(result) == before.st_size and len(result) <= limit)
        need(fingerprint(os.fstat(fd)) == fingerprint(before))
        need(fingerprint(os.stat(name, dir_fd=directory, follow_symlinks=False))
             == fingerprint(before))
        return bytes(result)
    finally:
        os.close(fd)


def checked(raw, size, digest):
    need(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest)
    return raw


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result)
        result[key] = value
    return result


def expected_manifest():
    return dict(format='pult-openssh-component-supply-v1', component_only=True,
                install_allowed=False, maintainer_scripts_allowed=False,
                unpack_only=True, packages=[dict(file=n, bytes=s, sha256=h)
                                           for n, s, h, _ in PACKAGES])


def fetch(package):
    _, size, digest, path = package
    # create_default_context otherwise honors these TLS-specific environment
    # overrides (including a key-log file). Proxy/netrc/auth env is never used.
    need(not any(os.environ.get(n) for n in
                 ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'SSLKEYLOGFILE')))
    context = ssl.create_default_context()
    need(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED)
    deadline = time.monotonic() + DEADLINE
    connection = http.client.HTTPSConnection(HOST, 443, timeout=TIMEOUT, context=context)
    response = None
    try:
        connection.request('GET', path, headers={'Accept-Encoding': 'identity',
                                               'Connection': 'close'})
        response = connection.getresponse()
        need(time.monotonic() < deadline and response.status == 200)
        headers = {}
        for key, value in response.getheaders():
            key = key.lower()
            need(key not in headers)
            headers[key] = value
        need(headers.get('content-length') == str(size))
        need(not any(n in headers for n in ('location', 'transfer-encoding')))
        need(headers.get('content-encoding', 'identity').lower() == 'identity')
        raw = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            need(remaining > 0)
            if connection.sock is not None:
                connection.sock.settimeout(min(TIMEOUT, remaining))
            piece = response.read1(min(CHUNK, size + 1 - len(raw)))
            need(isinstance(piece, bytes) and time.monotonic() < deadline)
            if not piece:
                break
            raw.extend(piece)
            need(len(raw) <= size)
        return checked(bytes(raw), size, digest)
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


def publish(directory, name, raw, size, digest):
    """No replace: publish an already verified, fsynced exclusive inode."""
    checked(raw, size, digest)
    temporary = '.verified-' + secrets.token_hex(16)
    fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                 os.O_CLOEXEC, 0o600, dir_fd=directory)
    identity = os.fstat(fd)
    linked = False
    try:
        safe(identity)
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            need(count > 0)
            offset += count
        os.fsync(fd)
        checked(read_file(directory, temporary, size), size, digest)
        need(os.fstat(fd).st_nlink == 1)
        # Linux procfs resolves THIS held descriptor, not a replaceable temporary
        # pathname. linkat follows only this kernel-owned /proc/self/fd link.
        os.link('/proc/self/fd/' + str(fd), name, dst_dir_fd=directory,
                follow_symlinks=True)
        linked = True
        destination = os.stat(name, dir_fd=directory, follow_symlinks=False)
        need((destination.st_dev, destination.st_ino) == (identity.st_dev, identity.st_ino))
        unlink_owned(directory, temporary, identity)
        checked(read_file(directory, name, size), size, digest)
        os.fsync(directory)
    except BaseException:
        if linked:
            unlink_owned(directory, name, identity)
        raise
    finally:
        try:
            unlink_owned(directory, temporary, identity)
        finally:
            os.close(fd)


def unlink_owned(directory, name, identity):
    try:
        row = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (row.st_dev, row.st_ino) == (identity.st_dev, identity.st_ino):
        os.unlink(name, dir_fd=directory)


def fetch_supply():
    need(sys.platform == 'linux')  # Standard-library POSIX inode/dir-fd proof.
    parent = open_directory(HERE)
    directory = None
    try:
        source = read_file(parent, 'fetch_openssh_supply.py', 65536)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory = os.open('openssh-supply', flags, dir_fd=parent)
        safe(os.fstat(directory), directory=True)
        manifest = read_file(directory, 'SUPPLY.json', 8192)
        value = json.loads(manifest, object_pairs_hook=unique_pairs)
        # JSON booleans must not compare equal to Python integer 0/1.
        need(json.dumps(value, sort_keys=True) == json.dumps(expected_manifest(), sort_keys=True))
        pending, results = [], []
        # Validate ALL existing inputs before making any network request.
        for package in PACKAGES:
            name, size, digest, _ = package
            try:
                raw = read_file(directory, name, size)
            except FileNotFoundError:
                pending.append(package)
            else:
                checked(raw, size, digest)
                results.append(dict(file=name, status='verified-existing'))
        # Both responses verified in memory before any artifact publication.
        fetched = [(package, fetch(package)) for package in pending]
        for (name, size, digest, _), raw in fetched:
            publish(directory, name, raw, size, digest)
            results.append(dict(file=name, status='verified-created'))
        for name, size, digest, _ in PACKAGES:
            checked(read_file(directory, name, size), size, digest)
        need(read_file(parent, 'fetch_openssh_supply.py', 65536) == source)
        need(read_file(directory, 'SUPPLY.json', 8192) == manifest)
        safe(os.fstat(parent), directory=True)
        safe(os.fstat(directory), directory=True)
        need(os.path.samestat(os.fstat(parent), os.stat(HERE, follow_symlinks=False)))
        need(os.path.samestat(os.fstat(directory),
                             os.stat('openssh-supply', dir_fd=parent, follow_symlinks=False)))
        return dict(format='pult-openssh-ci-fetch-v1', packages=results,
                    install_allowed=False, maintainer_scripts_allowed=False,
                    deployment_admitted=False)
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def main():
    try:
        need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
             and not sys.flags.optimize and len(sys.argv) == 1)
        print(json.dumps(fetch_supply(), sort_keys=True))
        return 0
    except (Refused, OSError, ValueError, http.client.HTTPException):
        print('OPENSSH_SUPPLY_REFUSED', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
